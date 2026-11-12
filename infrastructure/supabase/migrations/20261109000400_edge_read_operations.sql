-- Existing registry_api is the narrow PostgREST RPC boundary. No table exposure.
BEGIN;
GRANT registry_reserver TO CURRENT_USER WITH INHERIT TRUE, SET TRUE;
CREATE FUNCTION registry_api.get_context(actor uuid,generation uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE role_name text; s registry_private.registry_state; events jsonb; capacity jsonb;
BEGIN
    role_name:=registry_private.authorize_reservation(actor,generation);
    SELECT * INTO STRICT s FROM registry_private.registry_state;
    SELECT coalesce(jsonb_agg(to_jsonb(catalog) ORDER BY catalog.event_key),'[]') INTO events FROM
        (SELECT id,event_key,internal_label,display_title,display_date,state FROM registry_private.events
         ORDER BY event_key LIMIT 200) catalog;
    SELECT jsonb_build_object('year',extract(year FROM clock_timestamp() AT TIME ZONE 'UTC'),
        'reserved',count(*) FILTER(WHERE state='reserved'),'issued',count(*) FILTER(WHERE state='issued'),
        'retired',count(*) FILTER(WHERE state='retired'),'available',10000-count(*)) INTO capacity
        FROM registry_private.certificate_codes WHERE year=extract(year FROM clock_timestamp() AT TIME ZONE 'UTC');
    RETURN jsonb_build_object('user_id',actor,'role',role_name,'registry_generation',s.generation,'mode',s.mode,
        'events',events,'events_truncated',(SELECT count(*)>200 FROM registry_private.events),
        'formats',ARRAY['jpg','pdf'],'certificate_types',ARRAY['participation'],
        'max_batch_items',s.max_batch_items,'daily_new_code_limit',s.daily_new_code_limit,'capacity',capacity);
END $$;

CREATE FUNCTION registry_api.list_batches(actor uuid,generation uuid,page_limit integer DEFAULT 25,
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
         FROM registry_private.batches WHERE (issuer_id=actor OR role_name='registry_admin')
            AND (filter_state IS NULL OR state=filter_state)
         ORDER BY created_at DESC,id LIMIT page_limit OFFSET page_offset) summary;
    RETURN jsonb_build_object('batches',rows,'next_offset',CASE WHEN jsonb_array_length(rows)=page_limit
        AND page_offset+page_limit<=10000 THEN page_offset+page_limit ELSE NULL END);
END $$;

CREATE FUNCTION registry_api.lookup_certificate(actor uuid,generation uuid,query jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE role_name text; claim registry_private.certificate_codes; identity_id uuid;
    origin registry_private.batch_items;
BEGIN
    role_name:=registry_private.authorize_reservation(actor,generation);
    IF jsonb_typeof(query) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'INVALID_REQUEST'; END IF;
    IF query ? 'code' THEN
        IF query-ARRAY['code']::text[]<>'{}' OR coalesce(query->>'code','') !~ '^GDG-[0-9]{4}-[0-9]{4}$'
            THEN RAISE EXCEPTION 'INVALID_REQUEST'; END IF;
        SELECT * INTO claim FROM registry_private.certificate_codes WHERE code=query->>'code' AND state='issued';
    ELSE
        IF query-ARRAY['event_id','email','certificate_type']::text[]<>'{}'
            OR coalesce(query->>'certificate_type','participation')<>'participation'
            OR coalesce(btrim(query->>'email'),'')='' OR query->>'event_id' IS NULL THEN RAISE EXCEPTION 'INVALID_REQUEST'; END IF;
        SELECT id INTO identity_id FROM registry_private.certificates WHERE event_id=(query->>'event_id')::uuid
            AND normalized_email=lower(btrim(query->>'email')) AND certificate_type='participation';
        SELECT * INTO claim FROM registry_private.certificate_codes WHERE certificate_id=identity_id AND state='issued';
    END IF;
    IF claim.id IS NULL THEN RETURN jsonb_build_object('found',false); END IF;
    -- Knowable identity/code alone is not permission to read someone else's snapshot.
    PERFORM registry_private.require_certificate_access(actor,role_name,claim.id);
    SELECT * INTO origin FROM registry_private.batch_items WHERE code_id=claim.id AND kind='new';
    IF NOT FOUND THEN RAISE EXCEPTION 'ORIGIN_INCONSISTENT'; END IF;
    PERFORM registry_private.validate_issuance_links(origin.batch_id);
    IF NOT EXISTS(SELECT 1 FROM registry_private.batches WHERE id=origin.batch_id AND state='completed'
        AND validated_generation=generation) THEN RAISE EXCEPTION 'GENERATION_CHANGED'; END IF;
    RETURN jsonb_build_object('found',true,'certificate_id',claim.certificate_id,'code_id',claim.id,
        'code',claim.code,'event_id',claim.event_id,
        'issued_at',to_char(claim.issued_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        'snapshot',jsonb_build_object('name',origin.participant_name,'email',origin.delivery_email,
            'display_title',origin.display_title,'display_date',origin.display_date,
            'template_key',origin.template_key,'template_sha256',encode(origin.template_sha256,'hex'),
            'render_profile',origin.render_profile));
END $$;

GRANT CREATE ON SCHEMA registry_api TO registry_reserver;
ALTER FUNCTION registry_api.get_context(uuid,uuid) OWNER TO registry_reserver;
ALTER FUNCTION registry_api.list_batches(uuid,uuid,integer,integer,text) OWNER TO registry_reserver;
ALTER FUNCTION registry_api.lookup_certificate(uuid,uuid,jsonb) OWNER TO registry_reserver;
REVOKE CREATE ON SCHEMA registry_api FROM registry_reserver;
REVOKE EXECUTE ON FUNCTION registry_api.get_context(uuid,uuid),registry_api.list_batches(uuid,uuid,integer,integer,text),
    registry_api.lookup_certificate(uuid,uuid,jsonb) FROM PUBLIC,anon,authenticated,service_role;
GRANT EXECUTE ON FUNCTION registry_api.get_context(uuid,uuid),registry_api.list_batches(uuid,uuid,integer,integer,text),
    registry_api.lookup_certificate(uuid,uuid,jsonb) TO service_role;
-- No public/authenticated USAGE and no private table grants are added.
REVOKE registry_reserver FROM CURRENT_USER;
COMMIT;
