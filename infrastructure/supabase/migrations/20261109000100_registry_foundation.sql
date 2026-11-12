-- Foundation only. No allocator, issuance RPC, client policies, or real data.
BEGIN;
CREATE SCHEMA registry_private;
REVOKE ALL ON SCHEMA registry_private FROM PUBLIC, anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA registry_private REVOKE ALL ON TABLES FROM PUBLIC, anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA registry_private REVOKE ALL ON SEQUENCES FROM PUBLIC, anon, authenticated, service_role;
-- PostgreSQL global default EXECUTE grants cannot be negated by schema-scoped
-- defaults. Explicitly revoke every created function below and in future migrations.

CREATE TABLE registry_private.registry_state (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    generation uuid NOT NULL DEFAULT gen_random_uuid(),
    mode text NOT NULL DEFAULT 'recovery' CHECK (mode IN ('normal','recovery')),
    maintenance_reason text,
    changed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
-- Fail closed until a later, explicitly authorized initialization procedure.
INSERT INTO registry_private.registry_state DEFAULT VALUES;

CREATE TABLE registry_private.issuers (
    user_id uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE RESTRICT,
    role text NOT NULL DEFAULT 'issuer' CHECK (role IN ('issuer','registry_admin')),
    active boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE registry_private.events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_key text NOT NULL UNIQUE CHECK (event_key ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
    internal_label text NOT NULL CHECK (btrim(internal_label) <> ''),
    display_title text NOT NULL DEFAULT '',
    display_date date,
    state text NOT NULL DEFAULT 'active' CHECK (state IN ('active','closed')),
    created_by uuid NOT NULL REFERENCES registry_private.issuers(user_id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX events_catalog ON registry_private.events(state,event_key);
CREATE INDEX events_creator ON registry_private.events(created_by);

CREATE TABLE registry_private.certificates (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id uuid NOT NULL REFERENCES registry_private.events(id) ON DELETE RESTRICT,
    normalized_email text NOT NULL CHECK (
        normalized_email = lower(btrim(normalized_email))
        AND normalized_email ~ '^[^[:space:]@[:cntrl:]]+@[^[:space:]@[:cntrl:]]+[.][^[:space:]@[:cntrl:]]+$'
    ),
    certificate_type text NOT NULL DEFAULT 'participation' CHECK (certificate_type = 'participation'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(event_id,normalized_email,certificate_type),
    UNIQUE(id,event_id)
);

CREATE TABLE registry_private.batches (
    id uuid PRIMARY KEY, -- caller persists this operation/idempotency UUID first
    issuer_id uuid NOT NULL REFERENCES registry_private.issuers(user_id) ON DELETE RESTRICT,
    event_id uuid NOT NULL REFERENCES registry_private.events(id) ON DELETE RESTRICT,
    registry_generation uuid NOT NULL,
    validated_generation uuid,
    intent_hash bytea NOT NULL CHECK (octet_length(intent_hash)=32),
    fingerprint_version integer NOT NULL DEFAULT 1 CHECK (fingerprint_version=1),
    commit_hash bytea CHECK (commit_hash IS NULL OR octet_length(commit_hash)=32),
    requested_formats text[] NOT NULL DEFAULT ARRAY['pdf']::text[] CHECK (
        array_position(requested_formats,NULL) IS NULL AND (
        requested_formats = ARRAY['pdf']::text[] OR requested_formats = ARRAY['jpg']::text[]
        OR requested_formats = ARRAY['jpg','pdf']::text[])
    ),
    state text NOT NULL DEFAULT 'reserved' CHECK (state IN ('reserved','completed','abandoned','expired')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    review_after timestamptz NOT NULL DEFAULT (clock_timestamp()+interval '24 hours'),
    confirmed_at timestamptz,
    ended_at timestamptz,
    end_reason text,
    supersedes_batch_id uuid REFERENCES registry_private.batches(id) ON DELETE RESTRICT,
    UNIQUE(id,event_id),
    CHECK (supersedes_batch_id IS NULL OR supersedes_batch_id <> id),
    CHECK (review_after >= created_at),
    CHECK (
        (state='reserved' AND confirmed_at IS NULL AND ended_at IS NULL AND end_reason IS NULL AND commit_hash IS NULL)
        OR (state='completed' AND confirmed_at IS NOT NULL AND confirmed_at >= created_at
            AND commit_hash IS NOT NULL AND validated_generation IS NOT NULL AND ended_at IS NULL AND end_reason IS NULL)
        OR (state IN ('abandoned','expired') AND ended_at IS NOT NULL AND ended_at >= created_at
            AND end_reason IS NOT NULL AND btrim(end_reason) <> '' AND confirmed_at IS NULL AND commit_hash IS NULL)
    )
);
CREATE INDEX batches_owner_status ON registry_private.batches(issuer_id,state,created_at);
CREATE INDEX batches_event ON registry_private.batches(event_id);
CREATE INDEX batches_predecessor ON registry_private.batches(supersedes_batch_id);

CREATE TABLE registry_private.certificate_codes (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    certificate_id uuid NOT NULL,
    event_id uuid NOT NULL,
    reserving_batch_id uuid NOT NULL,
    year smallint NOT NULL CHECK (year BETWEEN 1 AND 9999),
    suffix smallint NOT NULL CHECK (suffix BETWEEN 0 AND 9999),
    code text GENERATED ALWAYS AS ('GDG-' || lpad(year::text,4,'0') || '-' || lpad(suffix::text,4,'0')) STORED,
    state text NOT NULL DEFAULT 'reserved' CHECK (state IN ('reserved','issued','retired')),
    reserved_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    issued_at timestamptz,
    retired_at timestamptz,
    retirement_reason text,
    UNIQUE(year,suffix),
    UNIQUE(code),
    UNIQUE(id,certificate_id,event_id),
    FOREIGN KEY(certificate_id,event_id) REFERENCES registry_private.certificates(id,event_id) ON DELETE RESTRICT,
    FOREIGN KEY(reserving_batch_id,event_id) REFERENCES registry_private.batches(id,event_id) ON DELETE RESTRICT,
    CHECK (
        (state='reserved' AND issued_at IS NULL AND retired_at IS NULL AND retirement_reason IS NULL)
        OR (state='issued' AND issued_at IS NOT NULL AND retired_at IS NULL AND retirement_reason IS NULL
            AND extract(year FROM issued_at AT TIME ZONE 'UTC')=year)
        OR (state='retired' AND issued_at IS NULL AND retired_at IS NOT NULL
            AND retirement_reason IS NOT NULL AND btrim(retirement_reason) <> '')
    )
);
CREATE UNIQUE INDEX certificate_one_live_code ON registry_private.certificate_codes(certificate_id)
    WHERE state IN ('reserved','issued');
CREATE INDEX codes_batch ON registry_private.certificate_codes(reserving_batch_id,event_id);

CREATE TABLE registry_private.batch_items (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id uuid NOT NULL,
    event_id uuid NOT NULL,
    client_item_id uuid NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal > 0),
    source_row integer CHECK (source_row IS NULL OR source_row >= 2),
    certificate_id uuid NOT NULL,
    code_id uuid NOT NULL,
    kind text NOT NULL DEFAULT 'new' CHECK (kind IN ('new','existing')),
    participant_name text NOT NULL CHECK (btrim(participant_name) <> ''),
    delivery_email text NOT NULL CHECK (delivery_email ~ '^[^[:space:]@[:cntrl:]]+@[^[:space:]@[:cntrl:]]+[.][^[:space:]@[:cntrl:]]+$'),
    display_title text NOT NULL DEFAULT '',
    display_date date,
    template_key text NOT NULL CHECK (btrim(template_key) <> ''),
    template_sha256 bytea NOT NULL CHECK (octet_length(template_sha256)=32),
    render_profile text NOT NULL CHECK (btrim(render_profile) <> ''),
    jpg_sha256 bytea,
    jpg_bytes bigint,
    pdf_sha256 bytea,
    pdf_bytes bigint,
    UNIQUE(batch_id,client_item_id),
    UNIQUE(batch_id,ordinal),
    UNIQUE(batch_id,certificate_id),
    FOREIGN KEY(batch_id,event_id) REFERENCES registry_private.batches(id,event_id) ON DELETE RESTRICT,
    FOREIGN KEY(code_id,certificate_id,event_id) REFERENCES registry_private.certificate_codes(id,certificate_id,event_id) ON DELETE RESTRICT,
    CHECK ((jpg_sha256 IS NULL AND jpg_bytes IS NULL)
        OR (jpg_sha256 IS NOT NULL AND octet_length(jpg_sha256)=32 AND jpg_bytes IS NOT NULL AND jpg_bytes > 0)),
    CHECK ((pdf_sha256 IS NULL AND pdf_bytes IS NULL)
        OR (pdf_sha256 IS NOT NULL AND octet_length(pdf_sha256)=32 AND pdf_bytes IS NOT NULL AND pdf_bytes > 0))
);
CREATE UNIQUE INDEX code_one_origin ON registry_private.batch_items(code_id) WHERE kind='new';
CREATE INDEX items_code_identity ON registry_private.batch_items(code_id,certificate_id,event_id);

CREATE TABLE registry_private.audit_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    actor_id uuid REFERENCES registry_private.issuers(user_id) ON DELETE RESTRICT,
    operation text NOT NULL CHECK (operation ~ '^[a-z][a-z0-9_]{0,63}$'),
    batch_id uuid REFERENCES registry_private.batches(id) ON DELETE RESTRICT,
    certificate_id uuid REFERENCES registry_private.certificates(id) ON DELETE RESTRICT,
    code_id uuid REFERENCES registry_private.certificate_codes(id) ON DELETE RESTRICT,
    event_id uuid REFERENCES registry_private.events(id) ON DELETE RESTRICT,
    request_id uuid,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (
        jsonb_typeof(metadata)='object' AND metadata - ARRAY['reason_code','client_version']::text[] = '{}'::jsonb
        AND (NOT metadata ? 'reason_code' OR (jsonb_typeof(metadata->'reason_code')='string' AND metadata->>'reason_code' ~ '^[A-Z][A-Z0-9_]{0,63}$'))
        AND (NOT metadata ? 'client_version' OR (jsonb_typeof(metadata->'client_version')='string' AND metadata->>'client_version' ~ '^[0-9]+([.][0-9]+){1,3}$'))
    )
);
CREATE INDEX audit_actor_time ON registry_private.audit_events(actor_id,occurred_at);
CREATE INDEX audit_batch_time ON registry_private.audit_events(batch_id,occurred_at);
CREATE INDEX audit_certificate ON registry_private.audit_events(certificate_id);
CREATE INDEX audit_code ON registry_private.audit_events(code_id);
CREATE INDEX audit_event ON registry_private.audit_events(event_id);

-- Small protective triggers, not issuance/allocation routines.
CREATE FUNCTION registry_private.protect_code_claim() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Code claims cannot be deleted' USING ERRCODE='23514';
    END IF;
    IF OLD.state IN ('issued','retired') OR
       (NEW.id,NEW.certificate_id,NEW.event_id,NEW.reserving_batch_id,NEW.year,NEW.suffix,NEW.reserved_at)
       IS DISTINCT FROM
       (OLD.id,OLD.certificate_id,OLD.event_id,OLD.reserving_batch_id,OLD.year,OLD.suffix,OLD.reserved_at) THEN
        RAISE EXCEPTION 'Code claims cannot be reassigned or terminal claims modified' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER protect_code_claim BEFORE UPDATE OR DELETE ON registry_private.certificate_codes
    FOR EACH ROW EXECUTE FUNCTION registry_private.protect_code_claim();

CREATE FUNCTION registry_private.reject_history_change() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
    RAISE EXCEPTION 'Registry history is immutable' USING ERRCODE='23514';
END $$;
CREATE TRIGGER immutable_certificate_identity BEFORE UPDATE OR DELETE ON registry_private.certificates
    FOR EACH ROW EXECUTE FUNCTION registry_private.reject_history_change();
CREATE TRIGGER append_only_audit BEFORE UPDATE OR DELETE ON registry_private.audit_events
    FOR EACH ROW EXECUTE FUNCTION registry_private.reject_history_change();
CREATE TRIGGER no_truncate_codes BEFORE TRUNCATE ON registry_private.certificate_codes
    FOR EACH STATEMENT EXECUTE FUNCTION registry_private.reject_history_change();
CREATE TRIGGER no_truncate_audit BEFORE TRUNCATE ON registry_private.audit_events
    FOR EACH STATEMENT EXECUTE FUNCTION registry_private.reject_history_change();

ALTER TABLE registry_private.registry_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE registry_private.issuers ENABLE ROW LEVEL SECURITY;
ALTER TABLE registry_private.events ENABLE ROW LEVEL SECURITY;
ALTER TABLE registry_private.certificates ENABLE ROW LEVEL SECURITY;
ALTER TABLE registry_private.batches ENABLE ROW LEVEL SECURITY;
ALTER TABLE registry_private.certificate_codes ENABLE ROW LEVEL SECURITY;
ALTER TABLE registry_private.batch_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE registry_private.audit_events ENABLE ROW LEVEL SECURITY;
-- No policies: deny ordinary clients. Later routines need explicit, reviewed grants.
REVOKE ALL ON ALL TABLES IN SCHEMA registry_private FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA registry_private FROM PUBLIC, anon, authenticated, service_role;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA registry_private FROM PUBLIC, anon, authenticated, service_role;
COMMIT;
