-- Run as the non-superuser immediately after EACH owner-changing migration.
DO $$
BEGIN
    ASSERT current_user = 'registry_migration';
    ASSERT NOT (SELECT rolsuper FROM pg_roles WHERE rolname=current_user);
    ASSERT NOT pg_has_role(current_user,'registry_reserver','SET');
    ASSERT NOT pg_has_role(current_user,'registry_reserver','USAGE');
    ASSERT NOT has_schema_privilege('registry_reserver','registry_api','CREATE');
    ASSERT NOT has_schema_privilege('registry_reserver','registry_private','CREATE');
END $$;
\echo 'Non-superuser migration boundary assertions passed.'
