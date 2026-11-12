-- Executed only against the positively identified disposable restore target.
DO $$
DECLARE r text; object record;
BEGIN
    -- This guard must work even when PostgreSQL skips every ASSERT statement.
    IF current_setting('plpgsql.check_asserts')::boolean IS DISTINCT FROM true THEN
        RAISE EXCEPTION 'REHEARSAL_ASSERTIONS_DISABLED';
    END IF;
    ASSERT current_setting('session_replication_role')='origin', 'REHEARSAL_TRIGGER_MODE';
    ASSERT NOT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname='supabase_migrations'), 'REHEARSAL_LEDGER';
    ASSERT (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='registry_private' AND c.relkind='r' AND c.relrowsecurity
        AND c.relowner='postgres'::regrole)=8, 'REHEARSAL_TABLE_SECURITY';
    ASSERT NOT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='registry_api' AND c.relkind IN ('r','v','m')), 'REHEARSAL_API_TABLE';
    ASSERT (SELECT count(*) FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace
        WHERE n.nspname='registry_private' AND c.contype='f' AND c.convalidated)=15, 'REHEARSAL_FOREIGN_KEYS';
    ASSERT NOT EXISTS(SELECT 1 FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace
        WHERE n.nspname='registry_private' AND NOT c.convalidated), 'REHEARSAL_INVALID_CONSTRAINT';
    ASSERT (SELECT count(*) FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace
        WHERE n.nspname='registry_private' AND c.contype='p')=8, 'REHEARSAL_PRIMARY_KEYS';
    ASSERT (SELECT count(*) FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace
        WHERE n.nspname='registry_private' AND c.contype='u')=10, 'REHEARSAL_UNIQUE_KEYS';
    ASSERT (SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='registry_private' AND NOT t.tgisinternal AND t.tgenabled='O'
        AND t.tgname IN ('append_only_audit','immutable_certificate_identity','no_truncate_audit',
            'no_truncate_codes','protect_batch_result','protect_code_claim','protect_item_snapshot'))=7,
        'REHEARSAL_HISTORY_TRIGGERS';
    ASSERT (SELECT count(*) FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='registry_private')=18,
        'REHEARSAL_POLICIES';
    ASSERT (SELECT NOT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls AND NOT rolcreaterole
        AND NOT rolcreatedb FROM pg_roles WHERE rolname='registry_reserver'), 'REHEARSAL_OWNER_ROLE';
    ASSERT NOT has_schema_privilege('registry_reserver','registry_private','CREATE')
        AND NOT has_schema_privilege('registry_reserver','registry_api','CREATE'), 'REHEARSAL_SCHEMA_CREATE';
    ASSERT (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='registry_api' AND p.proowner='registry_reserver'::regrole AND p.prosecdef
        AND p.proconfig @> ARRAY['search_path=""'])=8, 'REHEARSAL_API_OWNER';
    ASSERT (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='registry_private' AND NOT p.prosecdef
        AND p.proconfig @> ARRAY['search_path=""']
        AND p.proowner=(CASE WHEN p.proname IN ('begin_recovery','finish_recovery','protect_batch_result',
            'protect_code_claim','protect_item_snapshot','reject_history_change')
            THEN 'postgres'::regrole ELSE 'registry_reserver'::regrole END))=12, 'REHEARSAL_PRIVATE_OWNER';
    FOREACH r IN ARRAY ARRAY['anon','authenticated','service_role','authenticator'] LOOP
        ASSERT NOT pg_has_role(r,'registry_reserver','MEMBER'), 'REHEARSAL_OWNER_MEMBERSHIP';
    END LOOP;
    FOREACH r IN ARRAY ARRAY['anon','authenticated','service_role'] LOOP
        FOR object IN SELECT p.oid,n.nspname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname IN ('registry_api','registry_private') LOOP
            ASSERT has_function_privilege(r,object.oid,'EXECUTE')=
                (r='service_role' AND object.nspname='registry_api'), 'REHEARSAL_FUNCTION_GRANT';
        END LOOP;
        FOR object IN SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='registry_private' AND c.relkind='r' LOOP
            ASSERT NOT has_table_privilege(r,object.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'),
                'REHEARSAL_PRIVATE_TABLE_GRANT';
        END LOOP;
    END LOOP;
    PERFORM registry_private.validate_issuance_links(id) FROM registry_private.batches
        WHERE state IN ('reserved','completed');
    PERFORM registry_private.completed_batch_status(id) FROM registry_private.batches WHERE state='completed';
    ASSERT NOT EXISTS(SELECT 1 FROM registry_private.certificate_codes
        WHERE code <> 'GDG-'||lpad(year::text,4,'0')||'-'||lpad(suffix::text,4,'0')), 'REHEARSAL_CODE';
END $$;
