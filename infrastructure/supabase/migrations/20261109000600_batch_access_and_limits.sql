-- Recheck certificate ownership in batch operations and apply limits only to new work.
-- Replace routine definitions in place to preserve grants and existing issuance history.
BEGIN;
GRANT registry_reserver TO CURRENT_USER WITH INHERIT TRUE, SET TRUE;

-- Match require_certificate_access: existing claims belong to their original issuing batch.
-- Call after live issuer authorization under the registry lock.
CREATE FUNCTION registry_private.batch_certificates_owned_by(actor uuid,batch uuid) RETURNS boolean
LANGUAGE sql STABLE SET search_path='' AS $$
    SELECT NOT EXISTS (
        SELECT 1 FROM registry_private.batch_items i
        WHERE i.batch_id=batch AND i.kind='existing' AND NOT EXISTS (
            SELECT 1 FROM registry_private.certificate_codes c
            JOIN registry_private.batches origin ON origin.id=c.reserving_batch_id
            WHERE c.id=i.code_id AND c.state='issued' AND origin.issuer_id=actor
        )
    )
$$;
REVOKE ALL ON FUNCTION registry_private.batch_certificates_owned_by(uuid,uuid)
    FROM PUBLIC,anon,authenticated,service_role;
GRANT CREATE ON SCHEMA registry_private TO registry_reserver;
ALTER FUNCTION registry_private.batch_certificates_owned_by(uuid,uuid) OWNER TO registry_reserver;
REVOKE CREATE ON SCHEMA registry_private FROM registry_reserver;

CREATE OR REPLACE FUNCTION registry_api.reserve_batch(actor uuid, generation uuid, batch uuid, request jsonb) RETURNS jsonb
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
    -- Keep the protocol ceiling for all requests. Only new batches use the current configured limit.
    IF jsonb_array_length(request->'items') NOT BETWEEN 1 AND 500 THEN RAISE EXCEPTION 'BATCH_LIMIT'; END IF;
    SELECT * INTO b FROM registry_private.batches WHERE id=batch;
    IF FOUND THEN
        IF b.issuer_id<>actor THEN RAISE EXCEPTION 'BATCH_DENIED'; END IF;
        IF b.registry_generation<>generation THEN RAISE EXCEPTION 'GENERATION_CHANGED'; END IF;
        IF role_name<>'registry_admin' AND NOT registry_private.batch_certificates_owned_by(actor,batch)
            THEN RAISE EXCEPTION 'CERTIFICATE_DENIED'; END IF;
    ELSIF jsonb_array_length(request->'items')>s.max_batch_items THEN
        RAISE EXCEPTION 'BATCH_LIMIT';
    END IF;
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
    IF b.id IS NOT NULL THEN
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
    -- Existing-only regeneration allocates no codes, even above the current allowance.
    IF needed>0 THEN
        SELECT array_agg(v ORDER BY v) INTO free FROM generate_series(0,9999) v
            WHERE NOT EXISTS(SELECT 1 FROM registry_private.certificate_codes c WHERE c.year=yr AND c.suffix=v);
        IF needed>coalesce(cardinality(free),0) THEN RAISE EXCEPTION 'YEAR_EXHAUSTED'; END IF;
        SELECT count(*) INTO used_today FROM registry_private.certificate_codes c JOIN registry_private.batches owner_batch ON owner_batch.id=c.reserving_batch_id
            WHERE owner_batch.issuer_id=actor
              AND c.reserved_at >= (date_trunc('day',t AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
              AND c.reserved_at < ((date_trunc('day',t AT TIME ZONE 'UTC') + interval '1 day') AT TIME ZONE 'UTC');
        IF used_today+needed>s.daily_new_code_limit THEN RAISE EXCEPTION 'DAILY_LIMIT'; END IF;
    END IF;
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

CREATE OR REPLACE FUNCTION registry_api.get_batch_status(actor uuid,generation uuid,batch uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE role_name text; b registry_private.batches;
BEGIN
    role_name:=registry_private.authorize_reservation(actor,generation);
    SELECT * INTO b FROM registry_private.batches WHERE id=batch;
    IF NOT FOUND THEN RAISE EXCEPTION 'BATCH_NOT_FOUND'; END IF;
    IF b.issuer_id<>actor AND role_name<>'registry_admin' THEN RAISE EXCEPTION 'BATCH_DENIED'; END IF;
    IF role_name<>'registry_admin' AND NOT registry_private.batch_certificates_owned_by(actor,batch)
        THEN RAISE EXCEPTION 'CERTIFICATE_DENIED'; END IF;
    RETURN registry_private.completed_batch_status(batch);
END $$;

CREATE OR REPLACE FUNCTION registry_api.commit_batch(actor uuid,generation uuid,batch uuid,manifest jsonb) RETURNS jsonb
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
    IF role_name<>'registry_admin' AND NOT registry_private.batch_certificates_owned_by(actor,batch)
        THEN RAISE EXCEPTION 'CERTIFICATE_DENIED'; END IF;
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

CREATE OR REPLACE FUNCTION registry_api.abandon_reservation(actor uuid, generation uuid, batch uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE r text; b registry_private.batches;
BEGIN
    r:=registry_private.authorize_reservation(actor,generation);
    SELECT * INTO b FROM registry_private.batches WHERE id=batch;
    IF NOT FOUND THEN RAISE EXCEPTION 'BATCH_NOT_FOUND'; END IF;
    IF b.issuer_id<>actor AND r<>'registry_admin' THEN RAISE EXCEPTION 'BATCH_DENIED'; END IF;
    IF r<>'registry_admin' AND NOT registry_private.batch_certificates_owned_by(actor,batch)
        THEN RAISE EXCEPTION 'CERTIFICATE_DENIED'; END IF;
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

CREATE OR REPLACE FUNCTION registry_api.list_batches(actor uuid,generation uuid,page_limit integer DEFAULT 25,
    page_offset integer DEFAULT 0,filter_state text DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE role_name text; rows jsonb;
BEGIN
    role_name:=registry_private.authorize_reservation(actor,generation);
    IF page_limit IS NULL OR page_limit NOT BETWEEN 1 AND 100 OR page_offset IS NULL
        OR page_offset NOT BETWEEN 0 AND 10000 OR (filter_state IS NOT NULL
        AND filter_state NOT IN ('reserved','completed','abandoned','expired')) THEN RAISE EXCEPTION 'INVALID_REQUEST'; END IF;
    SELECT coalesce(jsonb_agg(to_jsonb(summary) ORDER BY summary.created_at DESC,summary.id),'[]') INTO rows FROM
        (SELECT id,event_id,issuer_id,state,created_at,review_after,confirmed_at,registry_generation,
            state='reserved' AND review_after<clock_timestamp() AS stale
         FROM registry_private.batches WHERE (role_name='registry_admin' OR
            (issuer_id=actor AND registry_private.batch_certificates_owned_by(actor,id)))
            AND (filter_state IS NULL OR state=filter_state)
         ORDER BY created_at DESC,id LIMIT page_limit OFFSET page_offset) summary;
    RETURN jsonb_build_object('batches',rows,'next_offset',CASE WHEN jsonb_array_length(rows)=page_limit
        AND page_offset+page_limit<=10000 THEN page_offset+page_limit ELSE NULL END);
END $$;

REVOKE registry_reserver FROM CURRENT_USER;
COMMIT;
