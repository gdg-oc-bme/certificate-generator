-- Reservation only. The service role is a trusted Edge caller, NOT an end user.
BEGIN;
CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions;
-- Fail rather than accidentally resolve a different random source/search path.
DO $$ BEGIN PERFORM extensions.gen_random_bytes(2); END $$;
CREATE ROLE registry_reserver NOLOGIN NOINHERIT;
-- PostgreSQL 16+ gives a non-superuser creator ADMIN, but not SET/INHERIT.
-- Temporarily obtain ownership authority for this migration only. The creator's
-- bootstrap-granted ADMIN membership remains non-assumable after our REVOKE.
GRANT registry_reserver TO CURRENT_USER WITH INHERIT TRUE, SET TRUE;
CREATE SCHEMA registry_api;
REVOKE ALL ON SCHEMA registry_api FROM PUBLIC, anon, authenticated;
GRANT USAGE ON SCHEMA registry_api TO service_role, registry_reserver;
GRANT USAGE ON SCHEMA registry_private, extensions TO registry_reserver;
-- Trusted extensions can own their functions as the bootstrap superuser. Avoid
-- an ineffective GRANT by a non-owner when pgcrypto already grants PUBLIC execute.
DO $$ BEGIN
    IF NOT has_function_privilege('registry_reserver','extensions.gen_random_bytes(integer)','EXECUTE') THEN
        GRANT EXECUTE ON FUNCTION extensions.gen_random_bytes(integer) TO registry_reserver;
    END IF;
END $$;

ALTER TABLE registry_private.registry_state
    ADD COLUMN max_batch_items integer NOT NULL DEFAULT 500 CHECK (max_batch_items BETWEEN 1 AND 500),
    ADD COLUMN daily_new_code_limit integer NOT NULL DEFAULT 500 CHECK (daily_new_code_limit BETWEEN 1 AND 10000);
CREATE UNIQUE INDEX issuer_one_reservation ON registry_private.batches(issuer_id) WHERE state='reserved';
-- The single global row lock serializes all mutations made by these routines.
GRANT SELECT ON registry_private.registry_state,registry_private.issuers,registry_private.events,
    registry_private.certificates,registry_private.batches,registry_private.certificate_codes,
    registry_private.batch_items TO registry_reserver;
-- Row-locking SELECTs require UPDATE privilege; expose no routine that changes these columns.
GRANT UPDATE(mode) ON registry_private.registry_state TO registry_reserver;
GRANT UPDATE(active) ON registry_private.issuers TO registry_reserver;
GRANT UPDATE(state) ON registry_private.events TO registry_reserver;
GRANT INSERT ON registry_private.certificates, registry_private.batches,
    registry_private.certificate_codes, registry_private.batch_items, registry_private.audit_events TO registry_reserver;
GRANT UPDATE ON registry_private.batches, registry_private.certificate_codes TO registry_reserver;
CREATE POLICY reserver_read_state ON registry_private.registry_state FOR SELECT TO registry_reserver USING (true);
CREATE POLICY reserver_lock_state ON registry_private.registry_state FOR UPDATE TO registry_reserver USING (true) WITH CHECK (true);
CREATE POLICY reserver_read_issuers ON registry_private.issuers FOR SELECT TO registry_reserver USING (true);
CREATE POLICY reserver_lock_issuers ON registry_private.issuers FOR UPDATE TO registry_reserver USING (true) WITH CHECK (true);
CREATE POLICY reserver_read_events ON registry_private.events FOR SELECT TO registry_reserver USING (true);
CREATE POLICY reserver_lock_events ON registry_private.events FOR UPDATE TO registry_reserver USING (true) WITH CHECK (true);
CREATE POLICY reserver_read_certificates ON registry_private.certificates FOR SELECT TO registry_reserver USING (true);
CREATE POLICY reserver_insert_certificates ON registry_private.certificates FOR INSERT TO registry_reserver WITH CHECK (true);
CREATE POLICY reserver_read_batches ON registry_private.batches FOR SELECT TO registry_reserver USING (true);
CREATE POLICY reserver_insert_batches ON registry_private.batches FOR INSERT TO registry_reserver WITH CHECK (true);
CREATE POLICY reserver_update_batches ON registry_private.batches FOR UPDATE TO registry_reserver USING (true) WITH CHECK (true);
CREATE POLICY reserver_read_codes ON registry_private.certificate_codes FOR SELECT TO registry_reserver USING (true);
CREATE POLICY reserver_insert_codes ON registry_private.certificate_codes FOR INSERT TO registry_reserver WITH CHECK (true);
CREATE POLICY reserver_update_codes ON registry_private.certificate_codes FOR UPDATE TO registry_reserver USING (true) WITH CHECK (true);
CREATE POLICY reserver_read_items ON registry_private.batch_items FOR SELECT TO registry_reserver USING (true);
CREATE POLICY reserver_insert_items ON registry_private.batch_items FOR INSERT TO registry_reserver WITH CHECK (true);
CREATE POLICY reserver_insert_audit ON registry_private.audit_events FOR INSERT TO registry_reserver WITH CHECK (true);

CREATE FUNCTION registry_private.authorize_reservation(actor uuid, generation uuid) RETURNS text
LANGUAGE plpgsql SET search_path='' AS $$
DECLARE s registry_private.registry_state; r text;
BEGIN
    SELECT * INTO STRICT s FROM registry_private.registry_state WHERE singleton FOR UPDATE;
    IF generation IS NULL OR generation <> s.generation THEN RAISE EXCEPTION 'GENERATION_CHANGED'; END IF;
    IF s.mode <> 'normal' THEN RAISE EXCEPTION 'REGISTRY_RECOVERY'; END IF;
    SELECT role INTO r FROM registry_private.issuers WHERE user_id=actor AND active FOR SHARE;
    IF r IS NULL THEN RAISE EXCEPTION 'ISSUER_DENIED'; END IF;
    RETURN r;
END $$;

-- Existing-certificate access belongs to the ORIGINAL issuing batch owner.
-- Later associations (even abandoned/admin regeneration batches) confer no
-- ownership. Call only after live issuer authorization under the registry lock.
CREATE FUNCTION registry_private.require_certificate_access(actor uuid, role_name text, claim_id uuid) RETURNS void
LANGUAGE plpgsql SET search_path='' AS $$
BEGIN
    IF role_name IS DISTINCT FROM 'registry_admin' AND NOT EXISTS (
        SELECT 1 FROM registry_private.certificate_codes c
        JOIN registry_private.batches b ON b.id=c.reserving_batch_id
        WHERE c.id=claim_id AND c.state='issued' AND b.issuer_id=actor
    ) THEN RAISE EXCEPTION 'CERTIFICATE_DENIED'; END IF;
END $$;

CREATE FUNCTION registry_private.reservation_snapshot(batch uuid) RETURNS jsonb
LANGUAGE sql SET search_path='' AS $$
    SELECT jsonb_build_object('batch_id',b.id,'state',b.state,'registry_generation',b.registry_generation,
        'intent_hash',encode(b.intent_hash,'hex'),'formats',b.requested_formats,
        'stale', b.state='reserved' AND clock_timestamp()>b.review_after,
        'year_valid',NOT EXISTS (SELECT 1 FROM registry_private.certificate_codes c
            WHERE c.reserving_batch_id=b.id AND c.state='reserved'
              AND c.year<>extract(year FROM clock_timestamp() AT TIME ZONE 'UTC')),
        'items',coalesce((SELECT jsonb_agg(jsonb_build_object(
            'item_id',i.id,'client_item_id',i.client_item_id,'ordinal',i.ordinal,'source_row',i.source_row,
            'certificate_id',i.certificate_id,'code',c.code,'code_id',c.id,'kind',i.kind,
            'code_state',c.state,'issued_at',c.issued_at,'name',i.participant_name,'email',i.delivery_email,
            'display_title',i.display_title,'display_date',i.display_date,'template_key',i.template_key,
            'template_sha256',encode(i.template_sha256,'hex'),'render_profile',i.render_profile) ORDER BY i.ordinal)
            FROM registry_private.batch_items i JOIN registry_private.certificate_codes c ON c.id=i.code_id
            WHERE i.batch_id=b.id),'[]'::jsonb),
        'capacity',(SELECT jsonb_build_object('year',extract(year FROM clock_timestamp() AT TIME ZONE 'UTC'),
            'reserved',count(*) FILTER(WHERE state='reserved'),'issued',count(*) FILTER(WHERE state='issued'),
            'retired',count(*) FILTER(WHERE state='retired'),'available',10000-count(*))
            FROM registry_private.certificate_codes WHERE year=extract(year FROM clock_timestamp() AT TIME ZONE 'UTC')))
    FROM registry_private.batches b WHERE b.id=batch
$$;

CREATE FUNCTION registry_api.get_reservation(actor uuid, generation uuid, batch uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE r text; b registry_private.batches;
BEGIN
    r := registry_private.authorize_reservation(actor,generation);
    SELECT * INTO b FROM registry_private.batches WHERE id=batch;
    IF NOT FOUND THEN RAISE EXCEPTION 'BATCH_NOT_FOUND'; END IF;
    IF b.issuer_id<>actor AND r<>'registry_admin' THEN RAISE EXCEPTION 'BATCH_DENIED'; END IF;
    RETURN registry_private.reservation_snapshot(batch);
END $$;

CREATE FUNCTION registry_api.reserve_batch(actor uuid, generation uuid, batch uuid, request jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE
    s registry_private.registry_state; b registry_private.batches;
    event uuid; event_state text; role_name text; formats text[]; items jsonb := '[]'; item jsonb; normalized jsonb;
    fingerprint bytea; cert uuid; claim registry_private.certificate_codes;
    origin registry_private.batch_items; free integer[]; m integer; pick integer; n integer;
    entropy bytea; accepted boolean; yr integer; needed integer:=0; used_today integer;
    position integer:=0; t timestamptz;
BEGIN
    role_name:=registry_private.authorize_reservation(actor,generation);
    SELECT * INTO STRICT s FROM registry_private.registry_state;
    IF batch IS NULL OR jsonb_typeof(request) IS DISTINCT FROM 'object'
       OR request - ARRAY['event_id','formats','items']::text[] <> '{}'::jsonb
       OR jsonb_typeof(request->'items') IS DISTINCT FROM 'array'
       OR jsonb_typeof(request->'formats') IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'INVALID_REQUEST'; END IF;
    IF jsonb_array_length(request->'items') NOT BETWEEN 1 AND s.max_batch_items THEN RAISE EXCEPTION 'BATCH_LIMIT'; END IF;
    event := (request->>'event_id')::uuid;
    SELECT array_agg(value ORDER BY value) INTO formats FROM jsonb_array_elements_text(request->'formats');
    IF formats IS NULL OR array_position(formats,NULL) IS NOT NULL
       OR NOT (formats=ARRAY['pdf'] OR formats=ARRAY['jpg'] OR formats=ARRAY['jpg','pdf']) THEN RAISE EXCEPTION 'INVALID_FORMATS'; END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(request->'items') LOOP
        IF jsonb_typeof(item) IS DISTINCT FROM 'object' OR item - ARRAY['client_item_id','source_row','name','email','display_title','display_date','template_key','template_sha256','render_profile']::text[] <> '{}'::jsonb
            OR jsonb_typeof(item->'name') IS DISTINCT FROM 'string'
            OR jsonb_typeof(item->'email') IS DISTINCT FROM 'string'
            OR coalesce(btrim(item->>'name'),'')='' OR coalesce(btrim(item->>'template_key'),'')=''
            OR coalesce(btrim(item->>'render_profile'),'')=''
            OR coalesce(item->>'template_sha256','') !~ '^[0-9a-fA-F]{64}$'
            OR coalesce(btrim(item->>'email'),'') !~ '^[A-Za-z0-9][A-Za-z0-9.!#$%&''*+/=?^_`{|}~-]*@[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?[.][A-Za-z]{2,63}$'
            OR split_part(btrim(item->>'email'),'@',1) LIKE '%..%'
            OR split_part(btrim(item->>'email'),'@',1) LIKE '%.'
            OR length(btrim(item->>'email'))>254 OR length(split_part(btrim(item->>'email'),'@',1))>64
            OR split_part(btrim(item->>'email'),'@',2) ~ '(^|[.])-|-([.]|$)|[.][.]'
            OR coalesce((item->>'source_row')::integer,2)<2
            OR (item->>'client_item_id') IS NULL THEN RAISE EXCEPTION 'INVALID_PARTICIPANT'; END IF;
        normalized := jsonb_build_object('client_item_id',(item->>'client_item_id')::uuid,
            'source_row',(item->>'source_row')::integer,'name',btrim(item->>'name'),
            'email',btrim(item->>'email'),'email_norm',lower(btrim(item->>'email')),
            'display_title',coalesce(item->>'display_title',''),'display_date',(item->>'display_date')::date,
            'template_key',btrim(item->>'template_key'),'template_sha256',lower(item->>'template_sha256'),
            'render_profile',btrim(item->>'render_profile'));
        items := items || jsonb_build_array(normalized);
    END LOOP;
    IF (SELECT count(DISTINCT value->>'email_norm') FROM jsonb_array_elements(items))<>jsonb_array_length(items)
        THEN RAISE EXCEPTION 'DUPLICATE_IDENTITY'; END IF;
    IF (SELECT count(DISTINCT value->>'client_item_id') FROM jsonb_array_elements(items))<>jsonb_array_length(items)
        THEN RAISE EXCEPTION 'DUPLICATE_ITEM'; END IF;
    fingerprint := sha256(convert_to(jsonb_build_object('version',1,'generation',generation,
        'event_id',event,'formats',formats,'items',items)::text,'UTF8'));
    SELECT * INTO b FROM registry_private.batches WHERE id=batch;
    IF FOUND THEN
        IF b.issuer_id<>actor THEN RAISE EXCEPTION 'BATCH_DENIED'; END IF;
        IF b.registry_generation<>generation THEN RAISE EXCEPTION 'GENERATION_CHANGED'; END IF;
        IF b.intent_hash<>fingerprint THEN RAISE EXCEPTION 'IDEMPOTENCY_CONFLICT'; END IF;
        IF EXISTS (SELECT 1 FROM registry_private.certificate_codes WHERE reserving_batch_id=batch AND state='reserved'
            AND year<>extract(year FROM clock_timestamp() AT TIME ZONE 'UTC')) THEN RAISE EXCEPTION 'YEAR_ROLLOVER'; END IF;
        RETURN registry_private.reservation_snapshot(batch);
    END IF;
    IF EXISTS(SELECT 1 FROM registry_private.batches WHERE issuer_id=actor AND state='reserved') THEN RAISE EXCEPTION 'ISSUER_BUSY'; END IF;
    SELECT state INTO event_state FROM registry_private.events WHERE id=event FOR SHARE;
    IF NOT FOUND THEN RAISE EXCEPTION 'EVENT_UNAVAILABLE'; END IF;
    t := clock_timestamp(); yr := extract(year FROM t AT TIME ZONE 'UTC');
    -- All writes below roll back together on any error, including late validation failures.
    INSERT INTO registry_private.batches(id,issuer_id,event_id,registry_generation,intent_hash,requested_formats)
        VALUES(batch,actor,event,generation,fingerprint,formats);
    FOR item IN SELECT value FROM jsonb_array_elements(items) LOOP
        SELECT id INTO cert FROM registry_private.certificates WHERE event_id=event
            AND normalized_email=item->>'email_norm' AND certificate_type='participation';
        IF cert IS NULL THEN
            INSERT INTO registry_private.certificates(event_id,normalized_email) VALUES(event,item->>'email_norm') RETURNING id INTO cert;
        END IF;
        SELECT * INTO claim FROM registry_private.certificate_codes WHERE certificate_id=cert AND state IN ('reserved','issued');
        IF FOUND AND claim.state='reserved' THEN RAISE EXCEPTION 'CERTIFICATE_BUSY'; END IF;
        IF claim.state='issued' THEN
            -- Check before reading/comparing/copying any originating snapshot.
            PERFORM registry_private.require_certificate_access(actor,role_name,claim.id);
        END IF;
        IF claim.id IS NULL THEN needed:=needed+1; END IF;
    END LOOP;
    -- Closed events allow authorized existing-only regeneration. Any new
    -- identity/claim (including mixed batches) rejects the entire reservation.
    IF needed>0 AND event_state<>'active' THEN RAISE EXCEPTION 'EVENT_UNAVAILABLE'; END IF;
    SELECT array_agg(v ORDER BY v) INTO free FROM generate_series(0,9999) v
        WHERE NOT EXISTS(SELECT 1 FROM registry_private.certificate_codes c WHERE c.year=yr AND c.suffix=v);
    IF needed>coalesce(cardinality(free),0) THEN RAISE EXCEPTION 'YEAR_EXHAUSTED'; END IF;
    SELECT count(*) INTO used_today FROM registry_private.certificate_codes c JOIN registry_private.batches owner_batch ON owner_batch.id=c.reserving_batch_id
        WHERE owner_batch.issuer_id=actor
          AND c.reserved_at >= (date_trunc('day',t AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
          AND c.reserved_at < ((date_trunc('day',t AT TIME ZONE 'UTC') + interval '1 day') AT TIME ZONE 'UTC');
    IF used_today+needed>s.daily_new_code_limit THEN RAISE EXCEPTION 'DAILY_LIMIT'; END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(items) LOOP
        position:=position+1;
        SELECT id INTO STRICT cert FROM registry_private.certificates WHERE event_id=event AND normalized_email=item->>'email_norm' AND certificate_type='participation';
        SELECT * INTO claim FROM registry_private.certificate_codes WHERE certificate_id=cert AND state='issued';
        IF FOUND THEN
            SELECT * INTO STRICT origin FROM registry_private.batch_items WHERE code_id=claim.id AND kind='new';
            IF origin.participant_name<>item->>'name' THEN RAISE EXCEPTION 'CONTENT_CONFLICT'; END IF;
        ELSE
            m:=cardinality(free); accepted:=false;
            FOR attempt IN 1..32 LOOP
                entropy:=extensions.gen_random_bytes(2); n:=get_byte(entropy,0)*256+get_byte(entropy,1);
                IF n<(65536/m)*m THEN pick:=n%m+1; accepted:=true; EXIT; END IF;
            END LOOP;
            IF NOT accepted THEN RAISE EXCEPTION 'RANDOMNESS_UNAVAILABLE'; END IF;
            INSERT INTO registry_private.certificate_codes(certificate_id,event_id,reserving_batch_id,year,suffix)
                VALUES(cert,event,batch,yr,free[pick]) RETURNING * INTO claim;
            free:=array_remove(free,free[pick]);
            origin.participant_name:=item->>'name'; origin.delivery_email:=item->>'email';
            origin.display_title:=item->>'display_title'; origin.display_date:=(item->>'display_date')::date;
            origin.template_key:=item->>'template_key'; origin.template_sha256:=decode(item->>'template_sha256','hex');
            origin.render_profile:=item->>'render_profile';
        END IF;
        INSERT INTO registry_private.batch_items(batch_id,event_id,client_item_id,ordinal,source_row,certificate_id,code_id,kind,
            participant_name,delivery_email,display_title,display_date,template_key,template_sha256,render_profile)
        VALUES(batch,event,(item->>'client_item_id')::uuid,position,(item->>'source_row')::integer,cert,claim.id,
            CASE WHEN claim.state='issued' THEN 'existing' ELSE 'new' END,origin.participant_name,origin.delivery_email,
            origin.display_title,origin.display_date,origin.template_key,origin.template_sha256,origin.render_profile);
    END LOOP;
    IF extract(year FROM clock_timestamp() AT TIME ZONE 'UTC')<>yr THEN RAISE EXCEPTION 'YEAR_ROLLOVER'; END IF;
    INSERT INTO registry_private.audit_events(actor_id,operation,batch_id,event_id) VALUES(actor,'batch_reserved',batch,event);
    RETURN registry_private.reservation_snapshot(batch);
END $$;

CREATE FUNCTION registry_api.abandon_reservation(actor uuid, generation uuid, batch uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE r text; b registry_private.batches;
BEGIN
    r:=registry_private.authorize_reservation(actor,generation);
    SELECT * INTO b FROM registry_private.batches WHERE id=batch;
    IF NOT FOUND THEN RAISE EXCEPTION 'BATCH_NOT_FOUND'; END IF;
    IF b.issuer_id<>actor AND r<>'registry_admin' THEN RAISE EXCEPTION 'BATCH_DENIED'; END IF;
    IF b.registry_generation<>generation THEN RAISE EXCEPTION 'GENERATION_CHANGED'; END IF;
    IF b.state='completed' THEN RAISE EXCEPTION 'BATCH_COMPLETED'; END IF;
    IF b.state='reserved' THEN
        UPDATE registry_private.certificate_codes SET state='retired',retired_at=clock_timestamp(),retirement_reason='abandoned'
            WHERE reserving_batch_id=batch AND state='reserved';
        UPDATE registry_private.batches SET state='abandoned',ended_at=clock_timestamp(),end_reason='abandoned' WHERE id=batch;
        INSERT INTO registry_private.audit_events(actor_id,operation,batch_id) VALUES(actor,'batch_abandoned',batch);
    END IF;
    RETURN registry_private.reservation_snapshot(batch);
END $$;

-- Function owners have no login, no membership granted to callers, and no DDL rights.
GRANT CREATE ON SCHEMA registry_api, registry_private TO registry_reserver;
ALTER FUNCTION registry_private.authorize_reservation(uuid,uuid) OWNER TO registry_reserver;
ALTER FUNCTION registry_private.require_certificate_access(uuid,text,uuid) OWNER TO registry_reserver;
ALTER FUNCTION registry_private.reservation_snapshot(uuid) OWNER TO registry_reserver;
ALTER FUNCTION registry_api.reserve_batch(uuid,uuid,uuid,jsonb) OWNER TO registry_reserver;
ALTER FUNCTION registry_api.get_reservation(uuid,uuid,uuid) OWNER TO registry_reserver;
ALTER FUNCTION registry_api.abandon_reservation(uuid,uuid,uuid) OWNER TO registry_reserver;
REVOKE CREATE ON SCHEMA registry_api, registry_private FROM registry_reserver;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA registry_api FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION registry_private.authorize_reservation(uuid,uuid), registry_private.reservation_snapshot(uuid) FROM PUBLIC,anon,authenticated,service_role;
REVOKE ALL ON FUNCTION registry_private.require_certificate_access(uuid,text,uuid) FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION registry_api.reserve_batch(uuid,uuid,uuid,jsonb),
    registry_api.get_reservation(uuid,uuid,uuid), registry_api.abandon_reservation(uuid,uuid,uuid) TO service_role;
REVOKE registry_reserver FROM CURRENT_USER;
COMMIT;
