"""Existing-certificate access and closed-event regressions using real RPCs."""
import json
import unittest
import uuid
import test_reservations as base
import test_commits as commits

sql, literal = base.sql, base.literal


class CertificateScope(unittest.TestCase):
    payload = base.Reservations.payload
    call = base.Reservations.call
    reserve = base.Reservations.reserve
    prepare = commits.Commits.prepare
    commit = commits.Commits.commit

    def setUp(self):
        base.Reservations.setUp(self)
        self.prepare()
        self.original = self.commit()
        self.owner_batch = self.batch
        self.other = str(uuid.uuid4())
        sql(f"INSERT INTO auth.users VALUES ('{self.other}'); INSERT INTO registry_private.issuers(user_id,active) VALUES ('{self.other}',true)")

    def lookup(self, actor=None, check=True):
        query = literal(json.dumps({'code': self.original['items'][0]['code']}))
        result = sql(f"SET ROLE service_role; SELECT registry_api.lookup_certificate('{actor or self.actor}','{self.generation}',{query}::jsonb)", check)
        return json.loads(result.stdout) if check else result

    def denied(self, result, code='CERTIFICATE_DENIED'):
        self.assertNotEqual(result.returncode, 0)
        self.assertRegex(result.stderr, r'\bP0001:\s+'+code+r'\b')
        self.assertNotIn('Alex Example', result.stderr)
        self.assertNotIn('person0@example.com', result.stderr)

    def test_other_issuer_cannot_lookup_or_reserve_known_identity(self):
        self.denied(self.lookup(self.other, False))
        attempted = str(uuid.uuid4())
        self.denied(self.reserve(self.request, actor=self.other, batch=attempted, check=False))
        self.assertEqual(sql(f"SELECT count(*) FROM registry_private.batches WHERE id='{attempted}'").stdout.strip(), '0')
        self.assertEqual(sql(f"SELECT count(*) FROM registry_private.batch_items WHERE batch_id='{attempted}'").stdout.strip(), '0')
        self.denied(self.lookup(self.other, False))

    def test_denial_precedes_name_comparison(self):
        payload = self.payload()
        payload['items'][0]['name'] = 'Different known-name guess'
        self.denied(self.reserve(payload, actor=self.other, batch=str(uuid.uuid4()), check=False))

    def test_abandoned_admin_association_does_not_transfer_ownership(self):
        sql(f"UPDATE registry_private.issuers SET role='registry_admin' WHERE user_id='{self.other}'")
        association = str(uuid.uuid4())
        self.reserve(self.request, actor=self.other, batch=association)
        self.call('abandon_reservation', actor=self.other, batch=association)
        sql(f"UPDATE registry_private.issuers SET role='issuer' WHERE user_id='{self.other}'")
        self.denied(self.lookup(self.other, False))
        self.denied(self.reserve(self.request, actor=self.other, batch=str(uuid.uuid4()), check=False))

    def test_abandon_cannot_establish_access(self):
        attempted = str(uuid.uuid4())
        self.denied(self.reserve(self.request, actor=self.other, batch=attempted, check=False))
        self.denied(self.call('abandon_reservation', actor=self.other, batch=attempted, check=False), 'BATCH_NOT_FOUND')
        self.denied(self.call('abandon_reservation', actor=self.other, batch=self.owner_batch, check=False), 'BATCH_DENIED')
        own_request = self.payload()
        own_request['items'][0]['email'] = 'other@example.com'
        own_batch = str(uuid.uuid4())
        self.reserve(own_request, actor=self.other, batch=own_batch)
        self.call('abandon_reservation', actor=self.other, batch=own_batch)
        self.denied(self.lookup(self.other, False))
        self.denied(self.reserve(self.request, actor=self.other, batch=str(uuid.uuid4()), check=False))

    def test_owner_regenerates_closed_event_preserving_original_rows(self):
        sql(f"UPDATE registry_private.events SET state='closed' WHERE id='{self.event}'")
        self.assertTrue(self.lookup()['found'])
        self.batch = str(uuid.uuid4())
        self.prepare(payload=self.request)
        self.assertEqual(self.reservation['items'][0]['kind'], 'existing')
        self.assertEqual(self.commit()['issuance_rows'], self.original['issuance_rows'])

    def test_closed_event_rejects_new_and_mixed_batches_atomically(self):
        sql(f"UPDATE registry_private.events SET state='closed' WHERE id='{self.event}'")
        for count in (1, 2):
            payload = self.payload(count)
            payload['items'][-1]['email'] = 'new-person@example.com'
            batch = str(uuid.uuid4())
            result = self.reserve(payload, batch=batch, check=False)
            self.assertIn('EVENT_UNAVAILABLE', result.stderr)
            self.assertEqual(sql(f"SELECT count(*) FROM registry_private.batches WHERE id='{batch}'").stdout.strip(), '0')
            self.assertEqual(sql(f"SELECT count(*) FROM registry_private.certificates WHERE event_id='{self.event}' AND normalized_email='new-person@example.com'").stdout.strip(), '0')

    def test_closed_event_does_not_bypass_other_issuer_authorization(self):
        sql(f"UPDATE registry_private.events SET state='closed' WHERE id='{self.event}'")
        self.denied(self.reserve(self.request, actor=self.other, batch=str(uuid.uuid4()), check=False))

    def test_admin_can_regenerate_and_complete_owner_batch(self):
        sql(f"UPDATE registry_private.issuers SET role='registry_admin' WHERE user_id='{self.other}'")
        self.assertTrue(self.lookup(self.other)['found'])
        sql(f"UPDATE registry_private.events SET state='closed' WHERE id='{self.event}'")
        self.batch = str(uuid.uuid4())
        self.prepare(payload=self.request)
        self.assertEqual(self.commit(actor=self.other)['issuance_rows'], self.original['issuance_rows'])
        self.batch = str(uuid.uuid4())
        self.reservation = self.reserve(self.request, actor=self.other)
        self.manifest = {'version': 1, 'intent_hash': self.reservation['intent_hash'], 'artifacts': [
            {'item_id': item['item_id'], 'certificate_id': item['certificate_id'], 'code_id': item['code_id'],
             'format': fmt, 'sha256': 'ab'*32, 'byte_count': 100, 'filename': item['code']+'.'+fmt}
            for item in self.reservation['items'] for fmt in self.reservation['formats']]}
        self.assertEqual(self.commit(actor=self.other)['issuance_rows'], self.original['issuance_rows'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
