-- Completes database issuance; file correctness remains a trusted client attestation.
BEGIN;
-- Needed for replacing the existing owner-held RPC and transferring new helpers.
GRANT registry_reserver TO CURRENT_USER WITH INHERIT TRUE, SET TRUE;
ALTER TABLE registry_private.batches ADD COLUMN commit_fingerprint_version integer
    NOT NULL DEFAULT 1 CHECK (commit_fingerprint_version=1);
GRANT UPDATE(jpg_sha256,jpg_bytes,pdf_sha256,pdf_bytes)
    ON registry_private.batch_items TO registry_reserver;
CREATE POLICY reserver_update_artifacts ON registry_private.batch_items
    FOR UPDATE TO registry_reserver USING (true) WITH CHECK (true);

CREATE FUNCTION registry_private.protect_item_snapshot() RETURNS trigger
LANGUAGE plpgsql SET search_path='' AS $$
BEGIN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'SNAPSHOT_IMMUTABLE'; END IF;
    IF (to_jsonb(NEW)-ARRAY['jpg_sha256','jpg_bytes','pdf_sha256','pdf_bytes'])
        IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['jpg_sha256','jpg_bytes','pdf_sha256','pdf_bytes'])
        OR EXISTS(SELECT 1 FROM registry_private.batches WHERE id=OLD.batch_id AND state<>'reserved')
    THEN RAISE EXCEPTION 'SNAPSHOT_IMMUTABLE'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER protect_item_snapshot BEFORE UPDATE OR DELETE ON registry_private.batch_items
    FOR EACH ROW EXECUTE FUNCTION registry_private.protect_item_snapshot();

CREATE FUNCTION registry_private.protect_batch_result() RETURNS trigger
LANGUAGE plpgsql SET search_path='' AS $$
BEGIN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'BATCH_IMMUTABLE'; END IF;
    IF OLD.state<>'reserved' THEN
        -- A later privileged recovery procedure may revalidate the generation,
        -- but cannot rewrite the original request or completed issuance result.
        IF OLD.state='completed' AND (to_jsonb(NEW)-'validated_generation')=(to_jsonb(OLD)-'validated_generation') THEN RETURN NEW; END IF;
        RAISE EXCEPTION 'BATCH_IMMUTABLE';
    END IF;
    IF (NEW.id,NEW.issuer_id,NEW.event_id,NEW.registry_generation,NEW.intent_hash,
        NEW.fingerprint_version,NEW.commit_fingerprint_version,NEW.requested_formats,NEW.supersedes_batch_id)
       IS DISTINCT FROM
       (OLD.id,OLD.issuer_id,OLD.event_id,OLD.registry_generation,OLD.intent_hash,
        OLD.fingerprint_version,OLD.commit_fingerprint_version,OLD.requested_formats,OLD.supersedes_batch_id)
    THEN RAISE EXCEPTION 'BATCH_INTENT_IMMUTABLE'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER protect_batch_result BEFORE UPDATE OR DELETE ON registry_private.batches
    FOR EACH ROW EXECUTE FUNCTION registry_private.protect_batch_result();

CREATE FUNCTION registry_private.validate_issuance_links(batch uuid) RETURNS void
LANGUAGE plpgsql SET search_path='' AS $$
DECLARE b registry_private.batches; item registry_private.batch_items;
    claim registry_private.certificate_codes; origin registry_private.batch_items;
    item_count integer; last_ordinal integer;
BEGIN
    SELECT * INTO STRICT b FROM registry_private.batches WHERE id=batch;
    SELECT count(*),max(ordinal) INTO item_count,last_ordinal FROM registry_private.batch_items WHERE batch_id=batch;
    IF item_count=0 OR item_count<>last_ordinal THEN RAISE EXCEPTION 'BATCH_INCONSISTENT'; END IF;
    FOR item IN SELECT * FROM registry_private.batch_items WHERE batch_id=batch ORDER BY ordinal LOOP
        SELECT * INTO STRICT claim FROM registry_private.certificate_codes WHERE id=item.code_id;
        IF item.event_id<>b.event_id OR claim.event_id<>b.event_id OR claim.certificate_id<>item.certificate_id
            OR NOT EXISTS(SELECT 1 FROM registry_private.certificates WHERE id=item.certificate_id
                AND event_id=b.event_id AND normalized_email=lower(btrim(item.delivery_email)))
        THEN RAISE EXCEPTION 'BATCH_INCONSISTENT'; END IF;
        SELECT * INTO origin FROM registry_private.batch_items WHERE code_id=claim.id AND kind='new';
        IF NOT FOUND OR origin.batch_id<>claim.reserving_batch_id OR origin.certificate_id<>claim.certificate_id
            THEN RAISE EXCEPTION 'ORIGIN_INCONSISTENT'; END IF;
        IF item.kind='new' THEN
            IF origin.id<>item.id OR claim.reserving_batch_id<>batch
                OR claim.state<>(CASE WHEN b.state='completed' THEN 'issued' ELSE 'reserved' END)
            THEN RAISE EXCEPTION 'RESERVATION_INCONSISTENT'; END IF;
        ELSE
            IF claim.state<>'issued' OR NOT EXISTS(SELECT 1 FROM registry_private.batches
                WHERE id=origin.batch_id AND state='completed')
                OR (item.participant_name,item.delivery_email,item.display_title,item.display_date,
                    item.template_key,item.template_sha256,item.render_profile)
                IS DISTINCT FROM (origin.participant_name,origin.delivery_email,origin.display_title,origin.display_date,
                    origin.template_key,origin.template_sha256,origin.render_profile)
            THEN RAISE EXCEPTION 'REGENERATION_INCONSISTENT'; END IF;
        END IF;
    END LOOP;
    IF EXISTS(SELECT 1 FROM registry_private.certificate_codes c WHERE c.reserving_batch_id=batch
        AND NOT EXISTS(SELECT 1 FROM registry_private.batch_items i WHERE i.batch_id=batch AND i.code_id=c.id AND i.kind='new'))
    THEN RAISE EXCEPTION 'UNMAPPED_CLAIM'; END IF;
END $$;

CREATE FUNCTION registry_private.completed_batch_status(batch uuid) RETURNS jsonb
LANGUAGE plpgsql SET search_path='' AS $$
DECLARE b registry_private.batches; rows jsonb := '[]'; current_generation uuid;
BEGIN
    SELECT * INTO STRICT b FROM registry_private.batches WHERE id=batch;
    SELECT generation INTO STRICT current_generation FROM registry_private.registry_state;
    IF b.state='completed' AND b.validated_generation=current_generation THEN
        PERFORM registry_private.validate_issuance_links(batch);
        IF EXISTS(SELECT 1 FROM registry_private.batch_items WHERE batch_id=batch AND
            (('jpg'=ANY(b.requested_formats)) IS DISTINCT FROM (jpg_sha256 IS NOT NULL)
             OR ('pdf'=ANY(b.requested_formats)) IS DISTINCT FROM (pdf_sha256 IS NOT NULL)))
        THEN RAISE EXCEPTION 'COMPLETED_ARTIFACTS_INCONSISTENT'; END IF;
        SELECT jsonb_agg(jsonb_build_object('code',c.code,'email',origin.delivery_email,
            'issued_at',to_char(c.issued_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')) ORDER BY i.ordinal)
        INTO rows FROM registry_private.batch_items i
        JOIN registry_private.certificate_codes c ON c.id=i.code_id
        JOIN registry_private.batch_items origin ON origin.code_id=c.id AND origin.kind='new'
        WHERE i.batch_id=batch AND c.state='issued';
    END IF;
    RETURN registry_private.reservation_snapshot(batch) || jsonb_build_object(
        'confirmed_at',to_char(b.confirmed_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        'commit_hash',encode(b.commit_hash,'hex'),'commit_fingerprint_version',b.commit_fingerprint_version,
        'recovery_validation_required',b.state='completed' AND b.validated_generation IS DISTINCT FROM current_generation,
        'issuance_rows',coalesce(rows,'[]'::jsonb));
END $$;

CREATE FUNCTION registry_api.get_batch_status(actor uuid,generation uuid,batch uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE role_name text; b registry_private.batches;
BEGIN
    role_name:=registry_private.authorize_reservation(actor,generation);
    SELECT * INTO b FROM registry_private.batches WHERE id=batch;
    IF NOT FOUND THEN RAISE EXCEPTION 'BATCH_NOT_FOUND'; END IF;
    IF b.issuer_id<>actor AND role_name<>'registry_admin' THEN RAISE EXCEPTION 'BATCH_DENIED'; END IF;
    RETURN registry_private.completed_batch_status(batch);
END $$;

-- Preserve the existing status operation while extending it with authoritative rows.
CREATE OR REPLACE FUNCTION registry_api.get_reservation(actor uuid,generation uuid,batch uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
BEGIN RETURN registry_api.get_batch_status(actor,generation,batch); END $$;

CREATE FUNCTION registry_api.commit_batch(actor uuid,generation uuid,batch uuid,manifest jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE role_name text; b registry_private.batches; a jsonb; canonical jsonb;
    item registry_private.batch_items; claim registry_private.certificate_codes;
    item_id uuid; cert_id uuid; code_id uuid; fmt text; hash_text text; bytes bigint;
    fingerprint bytea; t timestamptz; yr integer; count_items integer;
BEGIN
    role_name:=registry_private.authorize_reservation(actor,generation);
    SELECT * INTO b FROM registry_private.batches WHERE id=batch;
    IF NOT FOUND THEN RAISE EXCEPTION 'BATCH_NOT_FOUND'; END IF;
    IF b.issuer_id<>actor AND role_name<>'registry_admin' THEN RAISE EXCEPTION 'BATCH_DENIED'; END IF;
    IF b.registry_generation<>generation THEN RAISE EXCEPTION 'GENERATION_CHANGED'; END IF;
    IF b.state NOT IN ('reserved','completed') THEN RAISE EXCEPTION 'BATCH_NOT_ISSUABLE'; END IF;
    IF jsonb_typeof(manifest) IS DISTINCT FROM 'object'
       OR manifest-ARRAY['version','intent_hash','artifacts']::text[]<>'{}'::jsonb
       OR manifest->'version' IS DISTINCT FROM '1'::jsonb
       OR jsonb_typeof(manifest->'intent_hash') IS DISTINCT FROM 'string'
       OR jsonb_typeof(manifest->'artifacts') IS DISTINCT FROM 'array'
       OR coalesce(manifest->>'intent_hash','') !~ '^[a-fA-F0-9]{64}$'
       OR lower(manifest->>'intent_hash')<>encode(b.intent_hash,'hex')
    THEN RAISE EXCEPTION 'INVALID_MANIFEST'; END IF;
    SELECT count(*) INTO count_items FROM registry_private.batch_items WHERE batch_id=batch;
    IF count_items=0 OR jsonb_array_length(manifest->'artifacts')<>count_items*cardinality(b.requested_formats)
        THEN RAISE EXCEPTION 'INCOMPLETE_MANIFEST'; END IF;
    canonical:='[]';
    FOR a IN SELECT value FROM jsonb_array_elements(manifest->'artifacts') LOOP
        IF jsonb_typeof(a) IS DISTINCT FROM 'object'
            OR a-ARRAY['item_id','certificate_id','code_id','format','sha256','byte_count','filename']::text[]<>'{}'::jsonb
            OR jsonb_typeof(a->'item_id') IS DISTINCT FROM 'string'
            OR jsonb_typeof(a->'certificate_id') IS DISTINCT FROM 'string'
            OR jsonb_typeof(a->'code_id') IS DISTINCT FROM 'string'
            OR jsonb_typeof(a->'byte_count') IS DISTINCT FROM 'number'
            OR coalesce(a->>'byte_count','') !~ '^[1-9][0-9]{0,18}$'
            OR coalesce(a->>'sha256','') !~ '^[0-9a-fA-F]{64}$'
            OR jsonb_typeof(a->'sha256') IS DISTINCT FROM 'string'
            OR jsonb_typeof(a->'format') IS DISTINCT FROM 'string'
            OR coalesce(a->>'format','') NOT IN ('jpg','pdf')
            OR jsonb_typeof(a->'filename') IS DISTINCT FROM 'string'
        THEN RAISE EXCEPTION 'INVALID_ARTIFACT'; END IF;
        item_id:=(a->>'item_id')::uuid; cert_id:=(a->>'certificate_id')::uuid;
        code_id:=(a->>'code_id')::uuid; fmt:=a->>'format';
        bytes:=(a->>'byte_count')::bigint; hash_text:=lower(a->>'sha256');
        SELECT * INTO item FROM registry_private.batch_items WHERE id=item_id AND batch_id=batch;
        IF NOT FOUND OR item.certificate_id<>cert_id OR item.code_id<>code_id OR NOT fmt=ANY(b.requested_formats)
            THEN RAISE EXCEPTION 'ARTIFACT_ASSOCIATION'; END IF;
        SELECT * INTO STRICT claim FROM registry_private.certificate_codes WHERE id=code_id;
        IF a->>'filename'<>claim.code||'.'||fmt THEN RAISE EXCEPTION 'ARTIFACT_FILENAME'; END IF;
        canonical:=canonical||jsonb_build_array(jsonb_build_object('item_id',item_id,'certificate_id',cert_id,
            'code_id',code_id,'format',fmt,'sha256',hash_text,'byte_count',bytes,'filename',a->>'filename','ordinal',item.ordinal));
    END LOOP;
    IF (SELECT count(DISTINCT (value->>'item_id',value->>'format')) FROM jsonb_array_elements(canonical))
        <>jsonb_array_length(canonical) THEN RAISE EXCEPTION 'DUPLICATE_ARTIFACT'; END IF;
    SELECT jsonb_agg(value ORDER BY (value->>'ordinal')::integer,value->>'format') INTO canonical FROM jsonb_array_elements(canonical);
    fingerprint:=sha256(convert_to(jsonb_build_object('version',1,'intent_hash',encode(b.intent_hash,'hex'),
        'artifacts',canonical)::text,'UTF8'));
    -- Historical successful replay precedes reservation-year checks.
    IF b.state='completed' THEN
        IF b.commit_hash<>fingerprint THEN RAISE EXCEPTION 'COMMIT_CONFLICT'; END IF;
        RETURN registry_private.completed_batch_status(batch);
    END IF;
    IF EXISTS(SELECT 1 FROM registry_private.batch_items WHERE batch_id=batch AND kind='new') THEN
        PERFORM 1 FROM registry_private.events WHERE id=b.event_id AND state='active' FOR SHARE;
        IF NOT FOUND THEN RAISE EXCEPTION 'EVENT_UNAVAILABLE'; END IF;
    END IF;
    PERFORM registry_private.validate_issuance_links(batch);
    t:=clock_timestamp(); yr:=extract(year FROM t AT TIME ZONE 'UTC');
    IF EXISTS(SELECT 1 FROM registry_private.certificate_codes WHERE reserving_batch_id=batch AND state='reserved' AND year<>yr) THEN
        UPDATE registry_private.certificate_codes SET state='retired',retired_at=t,retirement_reason='year_rollover'
            WHERE reserving_batch_id=batch AND state='reserved';
        UPDATE registry_private.batches SET state='expired',ended_at=t,end_reason='year_rollover' WHERE id=batch;
        INSERT INTO registry_private.audit_events(actor_id,operation,batch_id,metadata)
            VALUES(actor,'batch_expired',batch,'{"reason_code":"YEAR_ROLLOVER"}');
        -- Return a domain error, do not throw: throwing would undo permanent retirement.
        RETURN registry_private.completed_batch_status(batch)||jsonb_build_object('error','YEAR_ROLLOVER');
    END IF;
    FOR a IN SELECT value FROM jsonb_array_elements(canonical) LOOP
        IF a->>'format'='jpg' THEN
            UPDATE registry_private.batch_items SET jpg_sha256=decode(a->>'sha256','hex'),jpg_bytes=(a->>'byte_count')::bigint WHERE id=(a->>'item_id')::uuid;
        ELSE
            UPDATE registry_private.batch_items SET pdf_sha256=decode(a->>'sha256','hex'),pdf_bytes=(a->>'byte_count')::bigint WHERE id=(a->>'item_id')::uuid;
        END IF;
    END LOOP;
    -- Logical transition time, not transaction-start or response-arrival time.
    -- Recheck after recording attestations, before changing any claim to issued.
    t:=clock_timestamp();
    IF extract(year FROM t AT TIME ZONE 'UTC')<>yr THEN RAISE EXCEPTION 'YEAR_ROLLOVER_RETRY'; END IF;
    UPDATE registry_private.certificate_codes SET state='issued',issued_at=t WHERE reserving_batch_id=batch AND state='reserved';
    UPDATE registry_private.batches SET state='completed',confirmed_at=t,commit_hash=fingerprint,validated_generation=generation WHERE id=batch;
    INSERT INTO registry_private.audit_events(actor_id,operation,batch_id,event_id) VALUES(actor,'batch_completed',batch,b.event_id);
    RETURN registry_private.completed_batch_status(batch);
EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN
    RAISE EXCEPTION 'INVALID_MANIFEST_VALUE';
END $$;

GRANT CREATE ON SCHEMA registry_api,registry_private TO registry_reserver;
ALTER FUNCTION registry_private.validate_issuance_links(uuid) OWNER TO registry_reserver;
ALTER FUNCTION registry_private.completed_batch_status(uuid) OWNER TO registry_reserver;
ALTER FUNCTION registry_api.get_batch_status(uuid,uuid,uuid) OWNER TO registry_reserver;
ALTER FUNCTION registry_api.commit_batch(uuid,uuid,uuid,jsonb) OWNER TO registry_reserver;
REVOKE CREATE ON SCHEMA registry_api,registry_private FROM registry_reserver;
REVOKE EXECUTE ON FUNCTION registry_private.protect_item_snapshot(),registry_private.protect_batch_result(),
    registry_private.validate_issuance_links(uuid),registry_private.completed_batch_status(uuid)
    FROM PUBLIC,anon,authenticated,service_role;
REVOKE EXECUTE ON FUNCTION registry_api.get_batch_status(uuid,uuid,uuid),registry_api.commit_batch(uuid,uuid,uuid,jsonb)
    FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION registry_api.get_batch_status(uuid,uuid,uuid),registry_api.commit_batch(uuid,uuid,uuid,jsonb) TO service_role;
REVOKE registry_reserver FROM CURRENT_USER;
COMMIT;
