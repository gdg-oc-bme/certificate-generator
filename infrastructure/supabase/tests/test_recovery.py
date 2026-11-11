"""Recovery controls in a pristine disposable database, never a hosted project."""
import json
import unittest
import uuid
import test_reservations as base
import test_commits as commits

sql, literal = base.sql, base.literal


class Recovery(unittest.TestCase):
    payload = base.Reservations.payload
    call = base.Reservations.call
    reserve = base.Reservations.reserve
    prepare = commits.Commits.prepare
    commit = commits.Commits.commit

    def setUp(self):
        base.Reservations.setUp(self)
        sql(f"UPDATE registry_private.issuers SET role='registry_admin' WHERE user_id='{self.actor}'")
        self.approval = str(uuid.uuid4())

    def begin_recovery(self):
        new = str(uuid.uuid4())
        result = sql(f"SELECT registry_private.begin_recovery('{self.actor}','{self.generation}','{new}','{self.approval}')", user='registry_migration')
        self.generation = new
        return result

    def finish(self, complete='true', reviewed='true', check=True):
        return sql(f"SELECT registry_private.finish_recovery('{self.actor}','{self.generation}',"
                   f"'{self.approval}',{complete},{reviewed})", check, user='registry_migration')

    def enable_actor(self):
        # Deliberate DB-owner approval fixture, never a desktop/API operation.
        sql(f"UPDATE registry_private.issuers SET active=true WHERE user_id='{self.actor}'")

    def test_begin_rotates_disables_and_audits(self):
        self.begin_recovery()
        self.assertEqual(sql('SELECT mode FROM registry_private.registry_state').stdout.strip(), 'recovery')
        self.assertEqual(sql('SELECT count(*) FROM registry_private.issuers WHERE active').stdout.strip(), '0')
        self.assertEqual(sql(f"SELECT count(*) FROM registry_private.audit_events WHERE operation='recovery_started' AND request_id='{self.approval}'").stdout.strip(), '1')
        self.assertIn('REGISTRY_RECOVERY', self.reserve(self.payload(), check=False).stderr)

    def test_completion_requires_both_admin_attestations(self):
        self.begin_recovery()
        for complete, reviewed in [('false', 'true'), ('true', 'false'), ('NULL', 'true')]:
            self.assertIn('RECOVERY_APPROVAL_REQUIRED', self.finish(complete, reviewed, False).stderr)
        self.assertEqual(sql('SELECT mode FROM registry_private.registry_state').stdout.strip(), 'recovery')
        self.finish()
        self.assertEqual(sql('SELECT mode FROM registry_private.registry_state').stdout.strip(), 'normal')
        self.assertEqual(sql('SELECT count(*) FROM registry_private.issuers WHERE active').stdout.strip(), '0')
        self.assertIn('ISSUER_DENIED', self.reserve(self.payload(), check=False).stderr)

    def test_recovery_preserves_history_and_retires_pending(self):
        self.prepare()
        original = self.commit()
        completed_batch = self.batch
        old_generation = self.generation
        self.batch = str(uuid.uuid4())
        pending = self.reserve(self.payload(2))
        self.begin_recovery()
        self.finish()
        self.enable_actor()
        restored = self.call('get_batch_status', batch=completed_batch)
        self.assertEqual(restored['issuance_rows'], original['issuance_rows'])
        self.assertFalse(restored['recovery_validation_required'])
        state = self.call('get_batch_status')
        self.assertEqual(state['state'], 'abandoned')
        self.assertTrue(all(i['code_state'] == ('retired' if i['kind'] == 'new' else 'issued') for i in state['items']))
        self.assertEqual({i['code'] for i in state['items']}, {i['code'] for i in pending['items']})
        self.assertIn('GENERATION_CHANGED', self.commit(check=False).stderr)
        self.assertIn('GENERATION_CHANGED', self.call('commit_batch', self.manifest, batch=completed_batch, check=False).stderr)
        # An old worker still holding the old external configuration is fenced.
        result = sql(f"SET ROLE service_role; SELECT registry_api.get_context('{self.actor}','{old_generation}')", False)
        self.assertIn('GENERATION_CHANGED', result.stderr)
        # Explicit regeneration is a new batch using the unchanged identity.
        self.batch = str(uuid.uuid4())
        regenerated = self.reserve(self.request)
        self.assertEqual(regenerated['items'][0]['code'], original['items'][0]['code'])
        self.prepare(payload=self.request)
        self.assertEqual(self.commit()['issuance_rows'], original['issuance_rows'])

    def test_external_generation_fences_all_rpc_operations(self):
        old = str(uuid.uuid4())
        payload = literal(json.dumps(self.payload())) + '::jsonb'
        operations = [('get_context', ''), ('list_batches', ''),
                      ('lookup_certificate', ",'{\"code\":\"GDG-2026-0000\"}'::jsonb"),
                      ('reserve_batch', f",'{self.batch}',{payload}"),
                      ('get_batch_status', f",'{self.batch}'"),
                      ('commit_batch', f",'{self.batch}','{{}}'::jsonb"),
                      ('abandon_reservation', f",'{self.batch}'")]
        for name, args in operations:
            with self.subTest(operation=name):
                result = sql(f"SET ROLE service_role; SELECT registry_api.{name}('{self.actor}','{old}'{args})", False)
                self.assertIn('GENERATION_CHANGED', result.stderr)
        self.assertEqual(sql(f"SELECT count(*) FROM registry_private.batches WHERE issuer_id='{self.actor}'").stdout.strip(), '0')

    def test_private_recovery_and_tables_not_executable_by_api_roles(self):
        # Recovery's migration owner needs explicit helper execution, not an
        # accidental dependency on the disposable cluster owner's superuser flag.
        self.assertEqual(sql("""SELECT count(*) FROM pg_proc p
            JOIN pg_namespace n ON n.oid=p.pronamespace
            CROSS JOIN LATERAL aclexplode(p.proacl) a
            WHERE n.nspname='registry_private' AND p.proname IN
            ('validate_issuance_links','completed_batch_status','reservation_snapshot')
            AND a.grantee='registry_migration'::regrole
            AND a.privilege_type='EXECUTE'""").stdout.strip(), '3')
        for role in ('anon', 'authenticated', 'service_role', 'registry_reserver'):
            with self.subTest(role=role):
                result = sql(f"SET ROLE {role}; SELECT registry_private.begin_recovery('{self.actor}','{self.generation}','{uuid.uuid4()}','{self.approval}')", False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('42501:', result.stderr)
                result = sql(f"SET ROLE {role}; SELECT registry_private.finish_recovery('{self.actor}','{self.generation}','{self.approval}',true,true)", False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('42501:', result.stderr)
        for role in ('anon', 'authenticated', 'service_role'):
            for table in ('registry_state', 'issuers', 'certificate_codes', 'batch_items'):
                self.assertEqual(sql(f"SELECT has_table_privilege('{role}','registry_private.{table}','SELECT,INSERT,UPDATE,DELETE')").stdout.strip(), 'f')
        self.assertEqual(sql("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='registry_api' AND c.relkind IN ('r','v','m')").stdout.strip(), '0')

    def test_requires_database_owner_and_recorded_admin(self):
        sql(f"UPDATE registry_private.issuers SET role='issuer' WHERE user_id='{self.actor}'")
        result = sql(f"SELECT registry_private.begin_recovery('{self.actor}','{self.generation}','{uuid.uuid4()}','{self.approval}')", False)
        self.assertIn('RECOVERY_ADMIN_REQUIRED', result.stderr)

    def test_generation_compare_and_swap(self):
        result = sql(f"SELECT registry_private.begin_recovery('{self.actor}','{uuid.uuid4()}','{uuid.uuid4()}','{self.approval}')", False)
        self.assertIn('GENERATION_CHANGED', result.stderr)
        self.assertEqual(sql('SELECT mode FROM registry_private.registry_state').stdout.strip(), 'normal')

    def test_normal_mode_requires_fresh_generation(self):
        result = sql(f"SELECT registry_private.begin_recovery('{self.actor}','{self.generation}','{self.generation}','{self.approval}')", False)
        self.assertIn('FRESH_GENERATION_REQUIRED', result.stderr)

    def test_active_issuers_prevent_reactivation(self):
        self.begin_recovery()
        self.enable_actor()
        self.assertIn('RECOVERY_ISSUERS_STILL_ACTIVE', self.finish(check=False).stderr)
        sql(f"UPDATE registry_private.issuers SET active=false WHERE user_id='{self.actor}'")
        self.finish()

    def test_reentry_does_not_reactivate_issuers(self):
        self.begin_recovery()
        sql(f"SELECT registry_private.begin_recovery('{self.actor}','{self.generation}','{self.generation}','{self.approval}')")
        self.assertEqual(sql('SELECT count(*) FROM registry_private.issuers WHERE active').stdout.strip(), '0')
        self.finish()
        self.assertIn('RECOVERY_NOT_ACTIVE', self.finish(check=False).stderr)

    def test_corrupt_history_rolls_back_reactivation(self):
        self.prepare()
        self.commit()
        completed_batch = self.batch
        self.batch = str(uuid.uuid4())
        self.reserve(self.payload(2))
        self.begin_recovery()
        # Deliberately corrupt only inside a transaction that rolls back on error.
        result = sql(f"""BEGIN;
            ALTER TABLE registry_private.batch_items DISABLE TRIGGER protect_item_snapshot;
            UPDATE registry_private.batch_items SET jpg_sha256=NULL,jpg_bytes=NULL WHERE batch_id='{completed_batch}';
            SET LOCAL ROLE registry_migration;
            SELECT registry_private.finish_recovery('{self.actor}','{self.generation}','{self.approval}',true,true);
            COMMIT;""", False)
        self.assertIn('COMPLETED_ARTIFACTS_INCONSISTENT', result.stderr)
        self.assertEqual(sql('SELECT mode FROM registry_private.registry_state').stdout.strip(), 'recovery')
        self.assertEqual(sql(f"SELECT state FROM registry_private.batches WHERE id='{self.batch}'").stdout.strip(), 'reserved')
        self.finish()


if __name__ == '__main__':
    unittest.main(verbosity=2)
