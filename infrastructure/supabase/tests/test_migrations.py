"""Full-chain role assertions after every migration ran as registry_migration."""
import unittest
import test_reservations as base


class MigrationRoles(unittest.TestCase):
    def query(self, statement):
        return base.sql(statement, user='registry_migration').stdout.strip()

    def test_migration_identity_is_not_superuser(self):
        self.assertEqual(self.query("SELECT current_user,rolsuper,rolcreaterole,rolbypassrls "
                                    "FROM pg_roles WHERE rolname=current_user"),
                         'registry_migration|f|t|f')

    def test_creator_admin_membership_cannot_assume_owner(self):
        self.assertEqual(self.query("""SELECT admin_option,inherit_option,set_option
            FROM pg_auth_members WHERE roleid='registry_reserver'::regrole
            AND member='registry_migration'::regrole
            AND grantor='registry_test_owner'::regrole"""), 't|f|f')
        self.assertEqual(self.query("SELECT pg_has_role(current_user,'registry_reserver','SET'),"
                                   "pg_has_role(current_user,'registry_reserver','USAGE')"), 'f|f')

    def test_api_functions_have_non_login_least_privilege_owner(self):
        self.assertEqual(self.query("""SELECT count(*) FROM pg_proc p
            JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_roles r ON r.oid=p.proowner
            WHERE n.nspname='registry_api' AND r.rolname='registry_reserver'
            AND NOT r.rolsuper AND NOT r.rolcanlogin AND NOT r.rolbypassrls
            AND p.prosecdef AND p.proconfig @> ARRAY['search_path=""']"""), '8')
        for schema in ('registry_private', 'registry_api'):
            self.assertEqual(self.query(f"SELECT has_schema_privilege('registry_reserver','{schema}','CREATE')"), 'f')

    def test_every_api_and_private_function_effective_grants(self):
        for role in ('anon', 'authenticated', 'service_role'):
            for schema in ('registry_api', 'registry_private'):
                expected = 'true' if role == 'service_role' and schema == 'registry_api' else 'false'
                self.assertEqual(self.query(f"""SELECT bool_and(has_function_privilege(
                    '{role}',p.oid,'EXECUTE')={expected}) FROM pg_proc p
                    JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='{schema}'"""), 't')
        for role in ('anon', 'authenticated', 'service_role', 'authenticator'):
            self.assertEqual(self.query(f"SELECT pg_has_role('{role}','registry_reserver','SET')"), 'f')

    def test_recovery_helpers_are_explicitly_granted_to_migration_owner(self):
        self.assertEqual(self.query("""SELECT count(*) FROM pg_proc p
            JOIN pg_namespace n ON n.oid=p.pronamespace CROSS JOIN LATERAL aclexplode(p.proacl) a
            WHERE n.nspname='registry_private' AND p.proname IN
            ('validate_issuance_links','completed_batch_status','reservation_snapshot')
            AND a.grantee='registry_migration'::regrole AND a.privilege_type='EXECUTE'"""), '3')


if __name__ == '__main__':
    unittest.main(verbosity=2)
