"""Exercise the actual execute-only database boundary used by the Edge handler."""
import json
import unittest
import uuid
import test_reservations as base

sql, literal = base.sql, base.literal


class EdgeRPC(unittest.TestCase):
    setUp = base.Reservations.setUp
    payload = base.Reservations.payload
    reserve = base.Reservations.reserve
    call = base.Reservations.call

    def rpc(self, name, args='', actor=None, check=True):
        result = sql(f"SET ROLE service_role; SELECT registry_api.{name}('{actor or self.actor}','{self.generation}'{args});", check)
        return json.loads(result.stdout) if check else result

    def test_context_and_catalog(self):
        result = self.rpc('get_context')
        self.assertEqual(result['user_id'], self.actor)
        self.assertTrue(any(e['id'] == self.event for e in result['events']))
        self.assertEqual(result['mode'], 'normal')
        self.assertIn('capacity', result)
        self.assertNotIn('email', str(result))

    def test_list_owner_and_admin(self):
        self.reserve(self.payload())
        other = str(uuid.uuid4())
        sql(f"INSERT INTO auth.users VALUES ('{other}'); INSERT INTO registry_private.issuers(user_id,active) VALUES ('{other}',true)")
        self.assertEqual(self.rpc('list_batches', actor=other)['batches'], [])
        self.assertEqual(len(self.rpc('list_batches')['batches']), 1)
        sql(f"UPDATE registry_private.issuers SET role='registry_admin' WHERE user_id='{other}'")
        self.assertTrue(any(b['id'] == self.batch for b in self.rpc('list_batches', actor=other)['batches']))

    def test_lookup_scope(self):
        reservation = self.reserve(self.payload())
        item = reservation['items'][0]
        manifest = {'version': 1, 'intent_hash': reservation['intent_hash'], 'artifacts': [
            {'item_id': item['item_id'], 'certificate_id': item['certificate_id'], 'code_id': item['code_id'],
             'format': f, 'sha256': 'ab'*32, 'byte_count': 100, 'filename': item['code']+'.'+f} for f in ('jpg','pdf')]}
        self.call('commit_batch', manifest)
        args = ','+literal(json.dumps({'code': item['code']}))+'::jsonb'
        result = self.rpc('lookup_certificate', args)
        self.assertTrue(result['found'])
        self.assertRegex(result['issued_at'], r'\.\d{6}Z$')
        other = str(uuid.uuid4())
        sql(f"INSERT INTO auth.users VALUES ('{other}'); INSERT INTO registry_private.issuers(user_id,active) VALUES ('{other}',true)")
        self.assertIn('CERTIFICATE_DENIED', self.rpc('lookup_certificate', args, actor=other, check=False).stderr)
        sql(f"UPDATE registry_private.issuers SET role='registry_admin' WHERE user_id='{other}'")
        self.assertTrue(self.rpc('lookup_certificate', args, actor=other)['found'])

    def test_disabled_and_recovery(self):
        sql(f"UPDATE registry_private.issuers SET active=false WHERE user_id='{self.actor}'")
        self.assertIn('ISSUER_DENIED', self.rpc('get_context', check=False).stderr)
        sql(f"UPDATE registry_private.issuers SET active=true WHERE user_id='{self.actor}'; UPDATE registry_private.registry_state SET mode='recovery'")
        self.assertIn('REGISTRY_RECOVERY', self.rpc('get_context', check=False).stderr)

    def test_grants_and_pagination(self):
        for role in ('anon', 'authenticated'):
            for function in ('get_context', 'list_batches'):
                result = sql(f"SET ROLE {role}; SELECT registry_api.{function}('{self.actor}','{self.generation}')", False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('42501:', result.stderr)
                self.assertIn('permission denied', result.stderr)
        self.assertEqual(sql("SELECT has_table_privilege('service_role','registry_private.batch_items','SELECT')").stdout.strip(), 'f')
        self.assertIn('INVALID_REQUEST', self.rpc('list_batches', ',101,0,NULL', check=False).stderr)
        self.assertEqual(self.rpc('list_batches', ',1,0,NULL')['batches'], [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
