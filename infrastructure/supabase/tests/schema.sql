BEGIN;
CREATE FUNCTION pg_temp.expect_error(statement text, expected_state text, expected_constraint text DEFAULT NULL)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE actual_state text; actual_constraint text;
BEGIN
    BEGIN
        EXECUTE statement;
    EXCEPTION WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS actual_state = RETURNED_SQLSTATE, actual_constraint = CONSTRAINT_NAME;
        IF actual_state = expected_state AND (expected_constraint IS NULL OR actual_constraint=expected_constraint) THEN RETURN; END IF;
        IF expected_constraint IS NOT NULL THEN
            RAISE EXCEPTION 'Expected constraint %, got %: %', expected_constraint, actual_constraint, SQLERRM;
        END IF;
        RAISE EXCEPTION 'Expected SQLSTATE %, got %: %', expected_state, actual_state, SQLERRM;
    END;
    RAISE EXCEPTION 'Expected SQLSTATE %, statement succeeded: %', expected_state, statement;
END;
$$;

DO $$
BEGIN
    ASSERT (SELECT count(*) FROM pg_tables WHERE schemaname = 'registry_private') = 8;
    ASSERT (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='registry_private' AND c.relkind='r' AND c.relrowsecurity) = 8;
    ASSERT (SELECT mode FROM registry_private.registry_state) = 'recovery';
END $$;

INSERT INTO auth.users VALUES ('00000000-0000-0000-0000-000000000001');
INSERT INTO registry_private.issuers(user_id) VALUES ('00000000-0000-0000-0000-000000000001');
INSERT INTO registry_private.events(id,event_key,internal_label,created_by)
VALUES ('00000000-0000-0000-0000-000000000002','synthetic-event','Synthetic event','00000000-0000-0000-0000-000000000001');
INSERT INTO registry_private.certificates(id,event_id,normalized_email)
VALUES ('00000000-0000-0000-0000-000000000003','00000000-0000-0000-0000-000000000002','alex@example.com');
INSERT INTO registry_private.batches(id,issuer_id,event_id,intent_hash,registry_generation)
SELECT '00000000-0000-0000-0000-000000000004','00000000-0000-0000-0000-000000000001',
       '00000000-0000-0000-0000-000000000002',decode(repeat('00',32),'hex'),generation
FROM registry_private.registry_state;
INSERT INTO registry_private.certificate_codes(id,certificate_id,event_id,reserving_batch_id,year,suffix)
VALUES ('00000000-0000-0000-0000-000000000005','00000000-0000-0000-0000-000000000003',
        '00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000004',2026,0);
INSERT INTO registry_private.batch_items(id,batch_id,event_id,certificate_id,code_id,client_item_id,ordinal,participant_name,delivery_email,template_key,template_sha256,render_profile)
VALUES ('00000000-0000-0000-0000-000000000006','00000000-0000-0000-0000-000000000004',
        '00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000003',
        '00000000-0000-0000-0000-000000000005','00000000-0000-0000-0000-000000000007',1,
        'Alex Example','alex@example.com','synthetic-template',decode(repeat('00',32),'hex'),'v1');

DO $$
BEGIN
    ASSERT (SELECT code FROM registry_private.certificate_codes) = 'GDG-2026-0000';
END $$;
SELECT pg_temp.expect_error($q$INSERT INTO registry_private.certificates(event_id,normalized_email)
 SELECT event_id,normalized_email FROM registry_private.certificates$q$, '23505');
-- INSERT a fresh identity: neither the immutable-update trigger nor a live-code
-- uniqueness failure can satisfy these exact constraint assertions.
INSERT INTO registry_private.certificates(id,event_id,normalized_email)
VALUES ('00000000-0000-0000-0000-000000000020','00000000-0000-0000-0000-000000000002','bounds@example.com');
SELECT pg_temp.expect_error($q$INSERT INTO registry_private.certificate_codes(certificate_id,event_id,reserving_batch_id,year,suffix)
 VALUES ('00000000-0000-0000-0000-000000000020','00000000-0000-0000-0000-000000000002',
 '00000000-0000-0000-0000-000000000004',2026,-1)$q$, '23514','certificate_codes_suffix_check');
SELECT pg_temp.expect_error($q$INSERT INTO registry_private.certificate_codes(certificate_id,event_id,reserving_batch_id,year,suffix)
 VALUES ('00000000-0000-0000-0000-000000000020','00000000-0000-0000-0000-000000000002',
 '00000000-0000-0000-0000-000000000004',2026,10000)$q$, '23514','certificate_codes_suffix_check');
SELECT pg_temp.expect_error($q$UPDATE registry_private.certificate_codes SET state='invalid'$q$, '23514');
SELECT pg_temp.expect_error($q$UPDATE registry_private.batches SET state='invalid'$q$, '23514');
SELECT pg_temp.expect_error($q$UPDATE registry_private.issuers SET role='owner'$q$, '23514');
SELECT pg_temp.expect_error($q$UPDATE registry_private.events SET state='invalid'$q$, '23514');
SELECT pg_temp.expect_error($q$UPDATE registry_private.batch_items SET source_row=1$q$, '23514');
SELECT pg_temp.expect_error($q$UPDATE registry_private.batch_items SET jpg_bytes=10$q$, '23514');
SELECT pg_temp.expect_error($q$UPDATE registry_private.batches SET requested_formats=ARRAY['pdf','pdf']$q$, '23514');
SELECT pg_temp.expect_error($q$UPDATE registry_private.batches SET requested_formats=ARRAY[NULL]::text[]$q$, '23514');
SELECT pg_temp.expect_error($q$UPDATE registry_private.certificates SET normalized_email='changed@example.com'$q$, '23514');
SELECT pg_temp.expect_error($q$DELETE FROM registry_private.events$q$, '23503');
SELECT pg_temp.expect_error($q$DELETE FROM auth.users$q$, '23503');
SELECT pg_temp.expect_error($q$UPDATE registry_private.batch_items SET certificate_id=gen_random_uuid()$q$, '23503');

-- A different identity isolates suffix uniqueness from active-identity uniqueness.
INSERT INTO registry_private.certificates(id,event_id,normalized_email)
VALUES ('00000000-0000-0000-0000-000000000008','00000000-0000-0000-0000-000000000002','jane@example.com');
SELECT pg_temp.expect_error($q$INSERT INTO registry_private.certificate_codes(certificate_id,event_id,reserving_batch_id,year,suffix)
 VALUES ('00000000-0000-0000-0000-000000000008','00000000-0000-0000-0000-000000000002',
 '00000000-0000-0000-0000-000000000004',2026,0)$q$, '23505');
SELECT pg_temp.expect_error($q$INSERT INTO registry_private.certificate_codes(certificate_id,event_id,reserving_batch_id,year,suffix)
 SELECT certificate_id,event_id,reserving_batch_id,year,1 FROM registry_private.certificate_codes$q$, '23505');
SELECT pg_temp.expect_error($q$UPDATE registry_private.certificate_codes SET code='GDG-2026-0001'$q$, '428C9');
SELECT pg_temp.expect_error($q$UPDATE registry_private.certificate_codes SET state='issued',issued_at='2027-01-01T00:00:00Z'$q$, '23514');
UPDATE registry_private.certificate_codes SET state='issued',issued_at='2026-11-09T10:30:00Z';
SELECT pg_temp.expect_error($q$UPDATE registry_private.certificate_codes SET issued_at=issued_at+interval '1 second'$q$, '23514');
SELECT pg_temp.expect_error($q$UPDATE registry_private.certificate_codes SET state='reserved',issued_at=NULL$q$, '23514');
SELECT pg_temp.expect_error($q$UPDATE registry_private.certificate_codes SET suffix=7$q$, '23514');
SELECT pg_temp.expect_error($q$DELETE FROM registry_private.certificate_codes$q$, '23514');

-- Retired claims free the logical identity for a later reservation, never the suffix.
INSERT INTO registry_private.certificate_codes(id,certificate_id,event_id,reserving_batch_id,year,suffix)
VALUES ('00000000-0000-0000-0000-000000000009','00000000-0000-0000-0000-000000000008',
 '00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000004',2026,1);
UPDATE registry_private.certificate_codes SET state='retired',retired_at=clock_timestamp(),retirement_reason='abandoned'
WHERE id='00000000-0000-0000-0000-000000000009';
SELECT pg_temp.expect_error($q$UPDATE registry_private.certificate_codes SET state='reserved',retired_at=NULL,retirement_reason=NULL
 WHERE id='00000000-0000-0000-0000-000000000009'$q$, '23514');
INSERT INTO registry_private.certificate_codes(certificate_id,event_id,reserving_batch_id,year,suffix)
VALUES ('00000000-0000-0000-0000-000000000008','00000000-0000-0000-0000-000000000002',
 '00000000-0000-0000-0000-000000000004',2026,2);
SELECT pg_temp.expect_error($q$INSERT INTO registry_private.certificate_codes(certificate_id,event_id,reserving_batch_id,year,suffix)
 VALUES ('00000000-0000-0000-0000-000000000008','00000000-0000-0000-0000-000000000002',
 '00000000-0000-0000-0000-000000000004',2026,1)$q$, '23505');
SELECT pg_temp.expect_error('TRUNCATE registry_private.audit_events','23514');

-- Both referenced entities exist, but their event scopes disagree.
INSERT INTO registry_private.events(id,event_key,internal_label,created_by)
VALUES ('00000000-0000-0000-0000-000000000010','other-event','Other synthetic event','00000000-0000-0000-0000-000000000001');
INSERT INTO registry_private.batches(id,issuer_id,event_id,intent_hash,registry_generation)
SELECT '00000000-0000-0000-0000-000000000011','00000000-0000-0000-0000-000000000001',
 '00000000-0000-0000-0000-000000000010',decode(repeat('00',32),'hex'),generation FROM registry_private.registry_state;
SELECT pg_temp.expect_error($q$UPDATE registry_private.batch_items SET batch_id='00000000-0000-0000-0000-000000000011'$q$, '23503');

INSERT INTO registry_private.audit_events(operation) VALUES ('synthetic_test');
SELECT pg_temp.expect_error($q$INSERT INTO registry_private.audit_events(operation,metadata)
 VALUES ('synthetic_test','{"email":"alex@example.com"}')$q$, '23514');
SELECT pg_temp.expect_error($q$UPDATE registry_private.audit_events SET operation='changed'$q$, '23514');
SELECT pg_temp.expect_error($q$DELETE FROM registry_private.audit_events$q$, '23514');
DO $$
DECLARE role_name text; table_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY['anon','authenticated','service_role'] LOOP
        ASSERT NOT has_schema_privilege(role_name,'registry_private','USAGE');
        FOR table_name IN SELECT tablename FROM pg_tables WHERE schemaname='registry_private' LOOP
            ASSERT NOT has_table_privilege(role_name,'registry_private.'||table_name,'SELECT');
            ASSERT NOT has_table_privilege(role_name,'registry_private.'||table_name,'INSERT');
            ASSERT NOT has_table_privilege(role_name,'registry_private.'||table_name,'UPDATE');
            ASSERT NOT has_table_privilege(role_name,'registry_private.'||table_name,'DELETE');
        END LOOP;
    END LOOP;
    ASSERT NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='registry_private');
    ASSERT NOT has_function_privilege('authenticated','registry_private.protect_code_claim()','EXECUTE');
    ASSERT NOT has_function_privilege('anon','registry_private.reject_history_change()','EXECUTE');
    -- Both the generated public code and the numeric pair have independent unique constraints.
    ASSERT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='registry_private.certificate_codes'::regclass
                   AND contype='u' AND pg_get_constraintdef(oid)='UNIQUE (code)');
    ASSERT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='registry_private.certificate_codes'::regclass
                   AND contype='u' AND pg_get_constraintdef(oid)='UNIQUE (year, suffix)');
END $$;
SET LOCAL ROLE authenticated;
SELECT pg_temp.expect_error('SELECT * FROM registry_private.certificates','42501');
RESET ROLE;
ROLLBACK;
\echo 'Schema assertions passed (synthetic fixtures rolled back).'
