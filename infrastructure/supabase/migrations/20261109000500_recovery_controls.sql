-- Manual database-owner controls only. Never exposed as Edge/Data API operations.
-- The external maintenance gate and draining old workers MUST precede restoration.
BEGIN;
-- Authorize the helper grants as their owner, without relying on SUPERUSER.
GRANT registry_reserver TO CURRENT_USER WITH INHERIT TRUE, SET TRUE;

-- Helpers are owned by registry_reserver, not necessarily the migration owner.
-- Grant only that trusted owner explicitly, so recovery does not assume SUPERUSER.
GRANT EXECUTE ON FUNCTION registry_private.validate_issuance_links(uuid),
    registry_private.completed_batch_status(uuid),registry_private.reservation_snapshot(uuid)
    TO CURRENT_USER;

CREATE FUNCTION registry_private.begin_recovery(
    operator_id uuid, expected_database_generation uuid, external_generation uuid, approval_id uuid
) RETURNS void
LANGUAGE plpgsql SECURITY INVOKER SET search_path='' AS $$
DECLARE s registry_private.registry_state;
BEGIN
    SELECT * INTO STRICT s FROM registry_private.registry_state WHERE singleton FOR UPDATE;
    -- SQL privileges authenticate the operator. This UUID records the responsible
    -- administrator, including when all Auth issuer access has been disabled.
    IF NOT EXISTS(SELECT 1 FROM registry_private.issuers
        WHERE user_id=operator_id AND role='registry_admin')
    THEN RAISE EXCEPTION 'RECOVERY_ADMIN_REQUIRED'; END IF;
    IF approval_id IS NULL OR external_generation IS NULL THEN RAISE EXCEPTION 'RECOVERY_APPROVAL_REQUIRED'; END IF;
    IF expected_database_generation IS DISTINCT FROM s.generation THEN RAISE EXCEPTION 'GENERATION_CHANGED'; END IF;
    IF external_generation=s.generation AND s.mode<>'recovery' THEN RAISE EXCEPTION 'FRESH_GENERATION_REQUIRED'; END IF;
    UPDATE registry_private.registry_state SET generation=external_generation,
        mode='recovery',maintenance_reason='administrator_recovery',changed_at=clock_timestamp() WHERE singleton;
    UPDATE registry_private.issuers SET active=false WHERE active;
    INSERT INTO registry_private.audit_events(actor_id,operation,request_id,metadata)
        VALUES(operator_id,'recovery_started',approval_id,'{"reason_code":"ADMINISTRATOR_RECOVERY"}');
END $$;

CREATE FUNCTION registry_private.finish_recovery(
    operator_id uuid, expected_generation uuid, approval_id uuid,
    historical_complete boolean, permissions_and_pending_reviewed boolean
) RETURNS void
LANGUAGE plpgsql SECURITY INVOKER SET search_path='' AS $$
DECLARE s registry_private.registry_state; b registry_private.batches; t timestamptz;
BEGIN
    SELECT * INTO STRICT s FROM registry_private.registry_state WHERE singleton FOR UPDATE;
    IF NOT EXISTS(SELECT 1 FROM registry_private.issuers
        WHERE user_id=operator_id AND role='registry_admin')
    THEN RAISE EXCEPTION 'RECOVERY_ADMIN_REQUIRED'; END IF;
    IF expected_generation IS DISTINCT FROM s.generation THEN RAISE EXCEPTION 'GENERATION_CHANGED'; END IF;
    IF s.mode<>'recovery' THEN RAISE EXCEPTION 'RECOVERY_NOT_ACTIVE'; END IF;
    -- These are accountable human attestations, NOT a proof of backup completeness.
    -- Never supply true if any acknowledged issue/retirement/identity is missing.
    IF approval_id IS NULL OR historical_complete IS DISTINCT FROM true
        OR permissions_and_pending_reviewed IS DISTINCT FROM true
    THEN RAISE EXCEPTION 'RECOVERY_APPROVAL_REQUIRED'; END IF;
    IF EXISTS(SELECT 1 FROM registry_private.issuers WHERE active)
        THEN RAISE EXCEPTION 'RECOVERY_ISSUERS_STILL_ACTIVE'; END IF;

    -- Conservatively abandon every reviewed pending operation. Its exposed codes
    -- remain permanently occupied; no silent replacement or renewed old-year claim.
    FOR b IN SELECT * FROM registry_private.batches WHERE state='reserved' ORDER BY id LOOP
        PERFORM registry_private.validate_issuance_links(b.id);
        t:=clock_timestamp();
        UPDATE registry_private.certificate_codes SET state='retired',retired_at=t,
            retirement_reason='recovery_review' WHERE reserving_batch_id=b.id AND state='reserved';
        UPDATE registry_private.batches SET state='abandoned',ended_at=t,end_reason='recovery_review' WHERE id=b.id;
        INSERT INTO registry_private.audit_events(actor_id,operation,batch_id,request_id,metadata)
            VALUES(operator_id,'recovery_batch_retired',b.id,approval_id,'{"reason_code":"RECOVERY_REVIEW"}');
    END LOOP;
    IF EXISTS(SELECT 1 FROM registry_private.certificate_codes WHERE state='reserved')
        THEN RAISE EXCEPTION 'RECOVERY_UNRECONCILED_CLAIMS'; END IF;
    IF EXISTS(SELECT 1 FROM registry_private.certificate_codes c WHERE c.state='issued'
        AND NOT EXISTS(SELECT 1 FROM registry_private.batch_items i
            JOIN registry_private.batches origin ON origin.id=i.batch_id
            WHERE i.code_id=c.id AND i.kind='new' AND origin.state='completed'))
        THEN RAISE EXCEPTION 'RECOVERY_UNRECONCILED_CLAIMS'; END IF;

    -- Revalidate mappings/artifact attestations, never rewrite issuance snapshots,
    -- original generation, code, time, fingerprints or artifacts. Any failure
    -- rolls back ALL retirement, revalidation, audit writes and mode transition.
    FOR b IN SELECT * FROM registry_private.batches WHERE state='completed' ORDER BY id LOOP
        PERFORM registry_private.validate_issuance_links(b.id);
        UPDATE registry_private.batches SET validated_generation=s.generation WHERE id=b.id;
        PERFORM registry_private.completed_batch_status(b.id);
        INSERT INTO registry_private.audit_events(actor_id,operation,batch_id,request_id)
            VALUES(operator_id,'recovery_batch_validated',b.id,approval_id);
    END LOOP;
    INSERT INTO registry_private.audit_events(actor_id,operation,request_id,metadata)
        VALUES(operator_id,'recovery_approved',approval_id,'{"reason_code":"HISTORY_RECONCILED"}');
    UPDATE registry_private.registry_state SET mode='normal',maintenance_reason=NULL,
        changed_at=clock_timestamp() WHERE singleton;
    -- Issuers remain disabled; external gate remains closed until separate approval.
END $$;

REVOKE ALL ON FUNCTION registry_private.begin_recovery(uuid,uuid,uuid,uuid),
    registry_private.finish_recovery(uuid,uuid,uuid,boolean,boolean)
    FROM PUBLIC,anon,authenticated,service_role,registry_reserver;
-- No API recovery grants, new roles, SECURITY DEFINER escalation or public endpoint.
REVOKE registry_reserver FROM CURRENT_USER;
COMMIT;
