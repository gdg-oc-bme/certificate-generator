"""Real PostgreSQL tests; invoked only by the disposable-cluster runner."""
import concurrent.futures
import json
import subprocess
import sys
import unittest
import uuid

PSQL, PORT = sys.argv[1:3]
DATABASE = sys.argv[3] if len(sys.argv) > 3 else 'postgres'
sys.argv = sys.argv[:1]


def sql(statement, check=True, user='registry_test_owner'):
    args = [PSQL, '-X', '-w', '-h', '127.0.0.1', '-p', PORT, '-U',
            user, '-d', DATABASE, '-v', 'ON_ERROR_STOP=1', '-v', 'VERBOSITY=verbose', '-qAt']
    # Windows command lines cannot carry a 500-person request/1000-artifact
    # manifest. These large RPC calls have no transaction-control statements.
    streamed = len(statement) > 16000
    args += ['--single-transaction', '-f', '-'] if streamed else ['-c', statement]
    result = subprocess.run(
        args, input=statement if streamed else None,
        capture_output=True, text=True, encoding='utf-8')
    if check and result.returncode:
        raise AssertionError(result.stderr)
    return result


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


class Reservations(unittest.TestCase):
    def setUp(self):
        self.actor, self.event, self.batch = [str(uuid.uuid4()) for _ in range(3)]
        self.generation = sql('SELECT generation FROM registry_private.registry_state').stdout.strip()
        sql(f"""UPDATE registry_private.registry_state SET mode='normal';
        INSERT INTO auth.users VALUES ('{self.actor}');
        INSERT INTO registry_private.issuers(user_id,active) VALUES ('{self.actor}',true);
        INSERT INTO registry_private.events(id,event_key,internal_label,created_by)
        VALUES ('{self.event}','event-{self.event}','Synthetic event','{self.actor}');""")

    def payload(self, count=1):
        return {'event_id': self.event, 'formats': ['jpg', 'pdf'], 'items': [
            {'client_item_id': str(uuid.uuid4()), 'source_row': i + 2,
             'name': 'Alex Example', 'email': f'person{i}@example.com',
             'template_key': 'synthetic', 'template_sha256': '00' * 32,
             'render_profile': 'v1'} for i in range(count)]}

    def call(self, operation, payload=None, batch=None, actor=None, check=True):
        args = f"'{actor or self.actor}','{self.generation}','{batch or self.batch}'"
        if payload is not None:
            args += ',' + literal(json.dumps(payload)) + '::jsonb'
        result = sql(f'SET ROLE service_role; SELECT registry_api.{operation}({args});', check)
        return json.loads(result.stdout) if check else result

    def reserve(self, payload, **kwargs):
        return self.call('reserve_batch', payload, **kwargs)

    def test_multiple_and_retry(self):
        payload = self.payload(20)
        first = self.reserve(payload)
        second = self.reserve(json.loads(json.dumps(payload, sort_keys=True)))
        self.assertEqual(first['items'], second['items'])
        self.assertEqual(len({i['code'] for i in first['items']}), 20)
        for item in first['items']:
            self.assertRegex(item['code'], r'^GDG-[0-9]{4}-[0-9]{4}$')
        self.assertEqual(first['intent_hash'], second['intent_hash'])
        self.assertEqual(self.call('get_reservation')['items'], first['items'])
        payload['formats'] = ['pdf']
        self.assertIn('IDEMPOTENCY_CONFLICT', self.reserve(payload, check=False).stderr)

    def test_duplicate_atomic_failure(self):
        payload = self.payload(2)
        payload['items'][1]['email'] = payload['items'][0]['email'].upper()
        self.assertIn('DUPLICATE_IDENTITY', self.reserve(payload, check=False).stderr)
        count = sql(f"SELECT count(*) FROM registry_private.batches WHERE id='{self.batch}'").stdout.strip()
        self.assertEqual(count, '0')
        self.assertEqual(sql(f"SELECT count(*) FROM registry_private.certificates WHERE event_id='{self.event}'").stdout.strip(), '0')

    def test_rollback_after_partial_work(self):
        # A test-only failing trigger forces failure after the first claim insert.
        sql(f"""CREATE FUNCTION pg_temp_placeholder() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
        IF NEW.event_id='{self.event}' AND (SELECT count(*) FROM registry_private.certificate_codes WHERE event_id=NEW.event_id)>0
        THEN RAISE EXCEPTION 'synthetic failure'; END IF; RETURN NEW; END $$;
        CREATE TRIGGER test_failure BEFORE INSERT ON registry_private.certificate_codes
        FOR EACH ROW EXECUTE FUNCTION pg_temp_placeholder();""")
        try:
            self.assertNotEqual(self.reserve(self.payload(2), check=False).returncode, 0)
            self.assertEqual(sql(f"SELECT count(*) FROM registry_private.certificates WHERE event_id='{self.event}'").stdout.strip(), '0')
            self.assertEqual(sql(f"SELECT count(*) FROM registry_private.batches WHERE id='{self.batch}'").stdout.strip(), '0')
        finally:
            sql('DROP TRIGGER test_failure ON registry_private.certificate_codes; DROP FUNCTION pg_temp_placeholder();')

    def test_abandon_and_capacity(self):
        result = self.reserve(self.payload(3))
        before = result['capacity']
        abandoned = self.call('abandon_reservation')
        self.assertEqual(abandoned['state'], 'abandoned')
        self.assertEqual(abandoned['capacity']['available'], before['available'])
        self.assertEqual(abandoned['capacity']['retired'], before['retired'] + 3)
        self.assertEqual(self.call('abandon_reservation')['items'], abandoned['items'])
        replacement = self.reserve(self.payload(3), batch=str(uuid.uuid4()))
        self.assertFalse({i['code'] for i in result['items']} & {i['code'] for i in replacement['items']})

    def test_stale_same_year(self):
        payload = self.payload()
        original = self.reserve(payload)
        sql(f"UPDATE registry_private.batches SET created_at=clock_timestamp()-interval '2 days',review_after=clock_timestamp()-interval '1 day' WHERE id='{self.batch}'")
        resumed = self.reserve(payload)
        self.assertTrue(resumed['stale'])
        self.assertEqual(original['items'], resumed['items'])

    def test_permissions_and_recovery(self):
        payload = self.payload()
        sql('UPDATE registry_private.registry_state SET mode=\'recovery\'')
        self.assertIn('REGISTRY_RECOVERY', self.reserve(payload, check=False).stderr)
        sql('UPDATE registry_private.registry_state SET mode=\'normal\'')
        self.reserve(payload)
        for role in ('anon', 'authenticated'):
            result = sql(f"SET ROLE {role}; SELECT registry_api.get_reservation('{self.actor}','{self.generation}','{self.batch}')", False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('42501:', result.stderr)
            self.assertIn('permission denied', result.stderr)
            self.assertEqual(sql(f"SELECT has_function_privilege('{role}','registry_api.get_reservation(uuid,uuid,uuid)','EXECUTE')").stdout.strip(), 'f')
        sql(f"UPDATE registry_private.issuers SET active=false WHERE user_id='{self.actor}'")
        self.assertIn('ISSUER_DENIED', self.reserve(payload, check=False).stderr)

    def test_one_open_batch(self):
        self.reserve(self.payload())
        self.assertIn('ISSUER_BUSY', self.reserve(self.payload(), batch=str(uuid.uuid4()), check=False).stderr)

    def test_501_items_rejected_without_writes(self):
        self.assertIn('BATCH_LIMIT', self.reserve(self.payload(501), check=False).stderr)
        self.assertEqual(sql(f"SELECT count(*) FROM registry_private.batches WHERE id='{self.batch}'").stdout.strip(), '0')

    def test_configured_limit_cannot_exceed_edge_maximum(self):
        result = sql('BEGIN; UPDATE registry_private.registry_state SET max_batch_items=501; ROLLBACK;', False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('23514:', result.stderr)
        self.assertIn('registry_state_max_batch_items_check', result.stderr)

    def test_concurrent_sessions(self):
        other = str(uuid.uuid4())
        sql(f"INSERT INTO auth.users VALUES ('{other}'); INSERT INTO registry_private.issuers(user_id,active) VALUES ('{other}',true)")
        p1, p2 = self.payload(10), self.payload(10)
        for item in p2['items']:
            item['email'] = 'other' + item['email']
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            a = pool.submit(self.reserve, p1)
            b = pool.submit(self.reserve, p2, actor=other, batch=str(uuid.uuid4()))
            a, b = a.result(), b.result()
        self.assertFalse({i['code'] for i in a['items']} & {i['code'] for i in b['items']})

    def test_competing_identity(self):
        other = str(uuid.uuid4())
        sql(f"INSERT INTO auth.users VALUES ('{other}'); INSERT INTO registry_private.issuers(user_id,active) VALUES ('{other}',true)")
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            calls = [pool.submit(self.reserve, self.payload(), actor=actor, batch=str(uuid.uuid4()), check=False)
                     for actor in (self.actor, other)]
            results = [call.result() for call in calls]
        self.assertEqual(sum(r.returncode == 0 for r in results), 1)
        self.assertTrue(any('CERTIFICATE_BUSY' in r.stderr for r in results))

    def test_generation_and_owner(self):
        payload = self.payload()
        self.reserve(payload)
        other = str(uuid.uuid4())
        sql(f"INSERT INTO auth.users VALUES ('{other}'); INSERT INTO registry_private.issuers(user_id,active) VALUES ('{other}',true)")
        self.assertIn('BATCH_DENIED', self.call('get_reservation', actor=other, check=False).stderr)
        self.generation = str(uuid.uuid4())
        self.assertIn('GENERATION_CHANGED', self.reserve(payload, check=False).stderr)

    def test_daily_limit_counts_retired(self):
        try:
            sql('UPDATE registry_private.registry_state SET daily_new_code_limit=1')
            self.reserve(self.payload())
            self.call('abandon_reservation')
            self.assertIn('DAILY_LIMIT', self.reserve(self.payload(), batch=str(uuid.uuid4()), check=False).stderr)
        finally:
            sql('UPDATE registry_private.registry_state SET daily_new_code_limit=500')

    def test_input_validation(self):
        for email in ('invalid', 'x\n@example.com', '=formula@example.com', 'x@a..com', 'x@-a.com'):
            payload = self.payload()
            payload['items'][0]['email'] = email
            self.assertIn('INVALID_PARTICIPANT', self.reserve(payload, check=False).stderr)
        payload = self.payload()
        payload['formats'] = ['pdf', 'pdf']
        self.assertIn('INVALID_FORMATS', self.reserve(payload, check=False).stderr)
        payload['formats'] = [None]
        self.assertNotEqual(self.reserve(payload, check=False).returncode, 0)

    def test_secure_source_and_grants(self):
        definition = sql("SELECT pg_get_functiondef('registry_api.reserve_batch(uuid,uuid,uuid,jsonb)'::regprocedure)").stdout
        self.assertIn('extensions.gen_random_bytes(2)', definition)
        self.assertNotIn('random()', definition)
        self.assertIn('65536/m', definition)
        self.assertEqual(sql("SELECT has_table_privilege('service_role','registry_private.certificate_codes','INSERT')").stdout.strip(), 'f')
        self.assertEqual(sql("SELECT has_function_privilege('authenticated','registry_api.reserve_batch(uuid,uuid,uuid,jsonb)','EXECUTE')").stdout.strip(), 'f')

    def test_bounded_entropy_failure(self):
        # Test-owner-only override within a transaction: closing the failed
        # connection rolls this back and restores the genuine extension function.
        payload = self.payload()
        result = sql(f"""BEGIN;
        CREATE OR REPLACE FUNCTION extensions.gen_random_bytes(integer) RETURNS bytea
        LANGUAGE sql AS $$ SELECT decode('ffff','hex') $$;
        SET LOCAL ROLE service_role;
        SELECT registry_api.reserve_batch('{self.actor}','{self.generation}','{self.batch}',{literal(json.dumps(payload))}::jsonb);
        ROLLBACK;""", False)
        self.assertIn('RANDOMNESS_UNAVAILABLE', result.stderr)
        self.assertEqual(sql(f"SELECT count(*) FROM registry_private.batches WHERE id='{self.batch}'").stdout.strip(), '0')

    def test_year_boundary(self):
        payload = self.payload()
        self.reserve(payload)
        # Simulate an old reservation without exposing a client-controlled clock.
        sql(f"""ALTER TABLE registry_private.certificate_codes DISABLE TRIGGER protect_code_claim;
        UPDATE registry_private.certificate_codes SET year=extract(year FROM clock_timestamp() AT TIME ZONE 'UTC')-1
        WHERE reserving_batch_id='{self.batch}';
        ALTER TABLE registry_private.certificate_codes ENABLE TRIGGER protect_code_claim;""")
        self.assertFalse(self.call('get_reservation')['year_valid'])
        self.assertIn('YEAR_ROLLOVER', self.reserve(payload, check=False).stderr)
        self.assertEqual(self.call('abandon_reservation')['state'], 'abandoned')

    def test_existing_issued_snapshot(self):
        payload = self.payload()
        original = self.reserve(payload)
        # Owner-only fixture models a previously issued certificate, not an issuance API.
        sql(f"""BEGIN;
        UPDATE registry_private.certificate_codes SET state='issued',issued_at=clock_timestamp() WHERE reserving_batch_id='{self.batch}';
        UPDATE registry_private.batches SET state='completed',confirmed_at=clock_timestamp(),commit_hash=decode(repeat('00',32),'hex'),
        validated_generation=registry_generation WHERE id='{self.batch}'; COMMIT;""")
        payload['items'][0]['display_title'] = 'Different new display default'
        regenerated = self.reserve(payload, batch=str(uuid.uuid4()))
        self.assertEqual(original['items'][0]['code'], regenerated['items'][0]['code'])
        self.assertEqual(regenerated['items'][0]['kind'], 'existing')
        self.assertEqual(regenerated['items'][0]['display_title'], '')
        self.assertIsNotNone(regenerated['items'][0]['issued_at'])

    def test_00_exhaustion_and_zero_suffix(self):
        # Fill remaining namespace in one rollback-only owner transaction. First
        # leave only 0000 free and exercise the actual allocator's leading zero.
        seed_actor, seed_batch = str(uuid.uuid4()), str(uuid.uuid4())
        payload = self.payload()
        command = f"""BEGIN;
        INSERT INTO auth.users VALUES ('{seed_actor}');
        INSERT INTO registry_private.issuers(user_id,active) VALUES ('{seed_actor}',true);
        INSERT INTO registry_private.batches(id,issuer_id,event_id,intent_hash,registry_generation)
        VALUES ('{seed_batch}','{seed_actor}','{self.event}',decode(repeat('00',32),'hex'),'{self.generation}');
        WITH pending AS MATERIALIZED (SELECT v,gen_random_uuid() id FROM generate_series(1,9999) v
          WHERE NOT EXISTS(SELECT 1 FROM registry_private.certificate_codes WHERE year=extract(year FROM clock_timestamp() AT TIME ZONE 'UTC') AND suffix=v)),
        identities AS (INSERT INTO registry_private.certificates(id,event_id,normalized_email)
          SELECT id,'{self.event}','capacity-'||v||'@example.com' FROM pending RETURNING id)
        INSERT INTO registry_private.certificate_codes(certificate_id,event_id,reserving_batch_id,year,suffix,state,retired_at,retirement_reason)
        SELECT p.id,'{self.event}','{seed_batch}',extract(year FROM clock_timestamp() AT TIME ZONE 'UTC'),p.v,'retired',clock_timestamp(),'synthetic'
        FROM pending p JOIN identities i ON i.id=p.id;
        SET LOCAL ROLE service_role;
        SELECT registry_api.reserve_batch('{self.actor}','{self.generation}','{self.batch}',{literal(json.dumps(payload))}::jsonb);
        RESET ROLE;
        ROLLBACK;"""
        result = json.loads(sql(command).stdout)
        self.assertTrue(result['items'][0]['code'].endswith('-0000'))
        self.assertEqual(result['capacity']['available'], 0)
        # Same filled namespace, requesting two IDs fails atomically instead.
        command = command.replace(literal(json.dumps(payload)), literal(json.dumps(self.payload(2))))
        failure = sql(command, False)
        self.assertIn('YEAR_EXHAUSTED', failure.stderr)
        self.assertEqual(sql(f"SELECT count(*) FROM registry_private.batches WHERE id='{self.batch}'").stdout.strip(), '0')


if __name__ == '__main__':
    unittest.main(verbosity=2)
