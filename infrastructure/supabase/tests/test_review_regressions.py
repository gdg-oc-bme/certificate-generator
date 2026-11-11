"""Authorization and allocation limit regressions against disposable PostgreSQL."""
import copy
import json
import unittest
import uuid

import test_certificate_scope as scope
import test_reservations as base

sql = base.sql


class ReservationLimits(unittest.TestCase):
    setUp = base.Reservations.setUp
    payload = base.Reservations.payload
    call = base.Reservations.call
    reserve = base.Reservations.reserve

    def test_identical_replay_ignores_lowered_batch_limit(self):
        request = self.payload(2)
        first = self.reserve(request)
        sql('UPDATE registry_private.registry_state SET max_batch_items=1')
        self.addCleanup(sql, 'UPDATE registry_private.registry_state SET max_batch_items=500')

        self.assertEqual(self.reserve(request), first)
        changed = copy.deepcopy(request)
        changed['formats'] = ['pdf']
        self.assertIn('IDEMPOTENCY_CONFLICT', self.reserve(changed, check=False).stderr)
        self.call('abandon_reservation')
        self.assertEqual(self.reserve(request)['state'], 'abandoned')

        new_batch = str(uuid.uuid4())
        self.assertIn('BATCH_LIMIT', self.reserve(request, batch=new_batch, check=False).stderr)
        self.assertEqual(sql(f"SELECT count(*) FROM registry_private.batches WHERE id='{new_batch}'").stdout.strip(), '0')


class BatchCertificateAccess(unittest.TestCase):
    setUp = scope.CertificateScope.setUp
    payload = scope.CertificateScope.payload
    call = scope.CertificateScope.call
    reserve = scope.CertificateScope.reserve
    prepare = scope.CertificateScope.prepare
    commit = scope.CertificateScope.commit
    lookup = scope.CertificateScope.lookup
    denied = scope.CertificateScope.denied

    def change_role(self, role):
        sql(f"""SELECT 1 FROM registry_private.registry_state WHERE singleton FOR UPDATE;
            UPDATE registry_private.issuers SET role='{role}' WHERE user_id='{self.other}'""")

    def admin_reservation(self, request):
        self.change_role('registry_admin')
        self.batch = str(uuid.uuid4())
        reservation = self.reserve(request, actor=self.other)
        self.manifest = {'version': 1, 'intent_hash': reservation['intent_hash'], 'artifacts': [
            {'item_id': item['item_id'], 'certificate_id': item['certificate_id'], 'code_id': item['code_id'],
             'format': fmt, 'sha256': 'ab' * 32, 'byte_count': 100, 'filename': item['code'] + '.' + fmt}
            for item in reservation['items'] for fmt in reservation['formats']]}

    def assert_batch_operations_denied(self, request):
        operations = [('get_batch_status', None), ('get_reservation', None), ('reserve_batch', request),
                      ('commit_batch', self.manifest), ('abandon_reservation', None)]
        for operation, body in operations:
            with self.subTest(operation=operation):
                result = self.call(operation, body, actor=self.other, check=False)
                self.denied(result)
                self.assertEqual(result.stdout.strip(), '')
                self.assertNotIn(self.original['items'][0]['code'], result.stderr)

    def test_demoted_admin_cannot_read_commit_or_abandon_pending_mixed_batch(self):
        request = self.payload(2)
        self.admin_reservation(request)
        self.change_role('issuer')
        self.assert_batch_operations_denied(request)
        self.denied(self.lookup(self.other, False))
        self.denied(self.call('get_batch_status', check=False), 'BATCH_DENIED')
        self.assertTrue(self.lookup()['found'])
        self.assertEqual(self.call('get_batch_status', batch=self.owner_batch)['issuance_rows'],
                         self.original['issuance_rows'])

        self.change_role('registry_admin')
        pending = self.call('get_batch_status', actor=self.other)
        self.assertEqual(pending['state'], 'reserved')
        self.assertEqual([i['code_state'] for i in pending['items']], ['issued', 'reserved'])
        self.assertEqual(self.call('abandon_reservation', actor=self.other)['state'], 'abandoned')
        self.change_role('issuer')
        for operation, body in [('abandon_reservation', None), ('reserve_batch', request)]:
            with self.subTest(abandoned_operation=operation):
                self.denied(self.call(operation, body, actor=self.other, check=False))

    def test_demoted_admin_cannot_replay_completed_foreign_regeneration(self):
        self.admin_reservation(self.request)
        completed = self.commit(actor=self.other)
        self.assertEqual(completed['issuance_rows'], self.original['issuance_rows'])
        self.change_role('issuer')
        self.assert_batch_operations_denied(self.request)

        self.change_role('registry_admin')
        self.assertEqual(self.commit(actor=self.other)['issuance_rows'], self.original['issuance_rows'])

    def test_batch_listing_filters_foreign_regeneration_before_pagination(self):
        own_request = self.payload()
        own_request['items'][0]['email'] = 'own@example.com'
        own_batch = str(uuid.uuid4())
        self.reserve(own_request, actor=self.other, batch=own_batch)
        self.call('abandon_reservation', actor=self.other, batch=own_batch)
        # Reserve the restricted batch last so it sorts first before filtering.
        self.admin_reservation(self.request)
        restricted_batch = self.batch
        self.change_role('issuer')
        query = f"SET ROLE service_role; SELECT registry_api.list_batches('{self.other}','{self.generation}',1,0,NULL)"
        visible = json.loads(sql(query).stdout)
        self.assertEqual([row['id'] for row in visible['batches']], [own_batch])
        self.change_role('registry_admin')
        self.assertEqual([row['id'] for row in json.loads(sql(query).stdout)['batches']], [restricted_batch])

    def test_lowered_daily_allowance_still_allows_existing_only_regeneration(self):
        original_request = self.request
        new_request = self.payload()
        new_request['items'][0]['email'] = 'second-issued@example.com'
        self.batch = str(uuid.uuid4())
        self.prepare(payload=new_request)
        self.commit()
        sql('UPDATE registry_private.registry_state SET daily_new_code_limit=1')
        self.addCleanup(sql, 'UPDATE registry_private.registry_state SET daily_new_code_limit=500')
        self.batch = str(uuid.uuid4())
        self.prepare(payload=original_request)
        self.assertEqual(self.reservation['items'][0]['kind'], 'existing')
        self.assertEqual(self.commit()['issuance_rows'], self.original['issuance_rows'])

        for count in (1, 2):
            with self.subTest(count=count):
                request = self.payload(count)
                request['items'][-1]['email'] = 'over-quota@example.com'
                batch = str(uuid.uuid4())
                result = self.reserve(request, batch=batch, check=False)
                self.assertIn('DAILY_LIMIT', result.stderr)
                self.assertEqual(sql(f"SELECT count(*) FROM registry_private.batches WHERE id='{batch}'").stdout.strip(), '0')
        self.assertEqual(sql(f"SELECT count(*) FROM registry_private.certificate_codes WHERE event_id='{self.event}'").stdout.strip(), '2')


if __name__ == '__main__':
    unittest.main(verbosity=2)
