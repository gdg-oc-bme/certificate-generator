"""Issuance lifecycle assertions against the runner's disposable PostgreSQL."""
import concurrent.futures
import copy
import json
import unittest
import uuid
import test_reservations as reservation_tests

literal, sql = reservation_tests.literal, reservation_tests.sql


class Commits(unittest.TestCase):
    setUp = reservation_tests.Reservations.setUp
    payload = reservation_tests.Reservations.payload
    call = reservation_tests.Reservations.call
    reserve = reservation_tests.Reservations.reserve

    def prepare(self, count=1, formats=None, payload=None):
        self.request = payload or self.payload(count)
        if formats:
            self.request['formats'] = formats
        self.reservation = self.reserve(self.request)
        self.manifest = {'version': 1, 'intent_hash': self.reservation['intent_hash'], 'artifacts': [
            {'item_id': item['item_id'], 'certificate_id': item['certificate_id'], 'code_id': item['code_id'],
             'format': fmt, 'sha256': 'ab' * 32, 'byte_count': 100,
             'filename': item['code'] + '.' + fmt}
            for item in self.reservation['items'] for fmt in self.reservation['formats']]}
        return self.manifest

    def commit(self, manifest=None, **kwargs):
        return self.call('commit_batch', manifest if manifest is not None else self.manifest, **kwargs)

    def status(self):
        return self.call('get_batch_status')

    def reject(self, manifest):
        self.assertNotEqual(self.commit(manifest, check=False).returncode, 0)
        state = self.status()
        self.assertEqual(state['state'], 'reserved')
        self.assertEqual(state['issuance_rows'], [])
        self.assertTrue(all(item['code_state'] == 'reserved' for item in state['items']))

    def test_single_commit(self):
        self.prepare()
        result = self.commit()
        self.assertEqual(result['state'], 'completed')
        self.assertEqual(len(result['issuance_rows']), 1)
        self.assertEqual(set(result['issuance_rows'][0]), {'code', 'email', 'issued_at'})

    def test_multi_commit(self):
        self.prepare(4)
        result = self.commit()
        self.assertEqual(len(result['issuance_rows']), 4)
        self.assertEqual(len({r['issued_at'] for r in result['issuance_rows']}), 1)

    def test_jpg(self):
        self.prepare(formats=['jpg'])
        self.assertEqual(self.commit()['state'], 'completed')

    def test_pdf(self):
        self.prepare(formats=['pdf'])
        self.assertEqual(self.commit()['state'], 'completed')

    def test_both(self):
        self.prepare()
        self.assertEqual(len(self.manifest['artifacts']), 2)
        self.assertEqual(self.commit()['state'], 'completed')

    def test_500_participants_and_1000_artifacts(self):
        self.prepare(500)
        self.assertEqual(len(self.manifest['artifacts']), 1000)
        self.assertEqual(len(self.commit()['issuance_rows']), 500)
        self.assertEqual(sql(f"SELECT count(*) FROM registry_private.batch_items WHERE batch_id='{self.batch}' AND jpg_bytes=100 AND pdf_bytes=100").stdout.strip(), '500')

    def test_missing_artifact(self):
        self.prepare(2)
        self.manifest['artifacts'].pop()
        self.reject(self.manifest)

    def test_wrong_format(self):
        self.prepare()
        self.manifest['artifacts'][0]['format'] = 'png'
        self.reject(self.manifest)

    def test_wrong_identity(self):
        self.prepare()
        self.manifest['artifacts'][0]['certificate_id'] = str(uuid.uuid4())
        self.reject(self.manifest)

    def test_bad_hash(self):
        self.prepare()
        self.manifest['artifacts'][0]['sha256'] = 'ab'
        self.reject(self.manifest)

    def test_bad_size(self):
        self.prepare()
        for value in (0, -1, None, '100', 1.5, 10**30):
            with self.subTest(value=value):
                self.manifest['artifacts'][0]['byte_count'] = value
                self.reject(self.manifest)

    def test_duplicate_artifact(self):
        self.prepare()
        self.manifest['artifacts'][1] = copy.deepcopy(self.manifest['artifacts'][0])
        self.reject(self.manifest)

    def test_filename(self):
        self.prepare()
        self.manifest['artifacts'][0]['filename'] = '../certificate.jpg'
        self.reject(self.manifest)

    def test_identical_retry_and_order_normalization(self):
        self.prepare(3)
        first = self.commit()
        self.manifest['artifacts'].reverse()
        second = self.commit()
        self.assertEqual(first['issuance_rows'], second['issuance_rows'])
        self.assertEqual(first['confirmed_at'], second['confirmed_at'])
        self.assertEqual(first['commit_hash'], second['commit_hash'])

    def test_conflicting_retry(self):
        self.prepare()
        self.commit()
        self.manifest['artifacts'][0]['byte_count'] += 1
        self.assertIn('COMMIT_CONFLICT', self.commit(check=False).stderr)

    def test_invalid_then_valid(self):
        self.prepare()
        invalid = copy.deepcopy(self.manifest)
        invalid['artifacts'].pop()
        self.reject(invalid)
        self.assertEqual(self.commit()['state'], 'completed')

    def test_abandoned(self):
        self.prepare()
        self.call('abandon_reservation')
        self.assertIn('BATCH_NOT_ISSUABLE', self.commit(check=False).stderr)

    def test_unknown_issuer(self):
        self.prepare()
        self.assertIn('ISSUER_DENIED', self.commit(actor=str(uuid.uuid4()), check=False).stderr)

    def test_disabled(self):
        self.prepare()
        sql(f"UPDATE registry_private.issuers SET active=false WHERE user_id='{self.actor}'")
        self.assertIn('ISSUER_DENIED', self.commit(check=False).stderr)

    def test_cross_issuer(self):
        self.prepare()
        other = str(uuid.uuid4())
        sql(f"INSERT INTO auth.users VALUES ('{other}'); INSERT INTO registry_private.issuers(user_id,active) VALUES ('{other}',true)")
        self.assertIn('BATCH_DENIED', self.commit(actor=other, check=False).stderr)
        self.assertIn('BATCH_DENIED', self.call('get_batch_status', actor=other, check=False).stderr)

    def test_admin_recovery(self):
        self.prepare()
        other = str(uuid.uuid4())
        sql(f"INSERT INTO auth.users VALUES ('{other}'); INSERT INTO registry_private.issuers(user_id,active,role) VALUES ('{other}',true,'registry_admin')")
        self.assertEqual(self.commit(actor=other)['state'], 'completed')

    def test_regeneration_and_mixed(self):
        self.prepare()
        original = self.commit()['issuance_rows'][0]
        self.batch = str(uuid.uuid4())
        payload = self.payload(2)
        self.prepare(payload=payload)
        self.assertEqual([i['kind'] for i in self.reservation['items']], ['existing', 'new'])
        result = self.commit()
        self.assertEqual(result['issuance_rows'][0], original)
        self.assertEqual(len(result['issuance_rows']), 2)

    def test_existing_only(self):
        self.prepare()
        first = self.commit()['issuance_rows']
        self.batch = str(uuid.uuid4())
        self.prepare()
        self.assertEqual(self.commit()['issuance_rows'], first)

    def test_rollover_retires_without_issuance(self):
        self.prepare()
        sql(f"""ALTER TABLE registry_private.certificate_codes DISABLE TRIGGER protect_code_claim;
        UPDATE registry_private.certificate_codes SET year=year-1 WHERE reserving_batch_id='{self.batch}';
        ALTER TABLE registry_private.certificate_codes ENABLE TRIGGER protect_code_claim;""")
        # Refresh filename to the actual old code, so this tests the year check.
        code = self.call('get_reservation')['items'][0]['code']
        for a in self.manifest['artifacts']:
            a['filename'] = code + '.' + a['format']
        result = self.commit()
        self.assertEqual(result['error'], 'YEAR_ROLLOVER')
        self.assertEqual(result['state'], 'expired')
        self.assertEqual(result['issuance_rows'], [])
        self.assertTrue(all(i['code_state'] == 'retired' for i in result['items']))

    def test_utc_timestamp(self):
        self.prepare()
        row = self.commit()['issuance_rows'][0]
        self.assertRegex(row['issued_at'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$')
        self.assertEqual(row['code'][4:8], row['issued_at'][:4])

    def test_recovery_mode(self):
        self.prepare()
        sql("UPDATE registry_private.registry_state SET mode='recovery'")
        self.assertIn('REGISTRY_RECOVERY', self.commit(check=False).stderr)

    def test_generation(self):
        self.prepare()
        self.generation = str(uuid.uuid4())
        self.assertIn('GENERATION_CHANGED', self.commit(check=False).stderr)

    def test_status_and_export_order(self):
        self.prepare(3)
        self.assertEqual(self.status()['issuance_rows'], [])
        self.commit()
        rows = self.status()['issuance_rows']
        self.assertEqual([r['email'] for r in rows], ['person0@example.com', 'person1@example.com', 'person2@example.com'])
        self.assertEqual(rows, self.call('get_reservation')['issuance_rows'])

    def test_concurrent_duplicate_commit(self):
        self.prepare(2)
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(self.commit) for _ in range(2)]
            results = [f.result() for f in futures]
        self.assertEqual(results[0]['issuance_rows'], results[1]['issuance_rows'])
        self.assertEqual(results[0]['confirmed_at'], results[1]['confirmed_at'])

    def test_timestamp_and_code_immutability(self):
        self.prepare()
        self.commit()
        for assignment in ("issued_at=issued_at+interval '1 second'", 'suffix=(suffix+1)%10000'):
            result = sql(f"UPDATE registry_private.certificate_codes SET {assignment} WHERE reserving_batch_id='{self.batch}'", False)
            self.assertNotEqual(result.returncode, 0)

    def test_snapshot_immutability(self):
        self.prepare()
        result = sql(f"UPDATE registry_private.batch_items SET participant_name='Changed' WHERE batch_id='{self.batch}'", False)
        self.assertNotEqual(result.returncode, 0)
        self.commit()
        result = sql(f"UPDATE registry_private.batch_items SET jpg_bytes=999 WHERE batch_id='{self.batch}'", False)
        self.assertNotEqual(result.returncode, 0)

    def test_unexpected_manifest_data(self):
        self.prepare()
        self.manifest['issued_at'] = '2026-01-01T00:00:00Z'
        self.reject(self.manifest)

    def test_rpc_permissions(self):
        self.prepare()
        for role in ('anon', 'authenticated'):
            result = sql(f"SET ROLE {role}; SELECT registry_api.commit_batch('{self.actor}','{self.generation}','{self.batch}',{literal(json.dumps(self.manifest))}::jsonb)", False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('42501:', result.stderr)
            self.assertIn('permission denied', result.stderr)
            self.assertEqual(sql(f"SELECT has_function_privilege('{role}','registry_api.commit_batch(uuid,uuid,uuid,jsonb)','EXECUTE')").stdout.strip(), 'f')
        self.assertEqual(self.commit()['state'], 'completed')

    def test_late_failure_rolls_back_mixed(self):
        self.prepare()
        original = self.commit()['issuance_rows'][0]
        self.batch = str(uuid.uuid4())
        self.prepare(2)
        sql(f"""CREATE FUNCTION public.fail_commit_test() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
        IF NEW.batch_id='{self.batch}' AND NEW.operation='batch_completed' THEN RAISE EXCEPTION 'synthetic failure'; END IF;
        RETURN NEW; END $$;
        CREATE TRIGGER fail_commit_test BEFORE INSERT ON registry_private.audit_events
        FOR EACH ROW EXECUTE FUNCTION public.fail_commit_test();""")
        try:
            self.assertNotEqual(self.commit(check=False).returncode, 0)
            status = self.status()
            self.assertEqual(status['state'], 'reserved')
            self.assertIsNone(status['commit_hash'])
            self.assertEqual([i['code_state'] for i in status['items']], ['issued', 'reserved'])
            self.assertEqual(status['items'][0]['code'], original['code'])
            self.assertEqual(sql(f"SELECT count(*) FROM registry_private.batch_items WHERE batch_id='{self.batch}' AND jpg_bytes IS NOT NULL").stdout.strip(), '0')
        finally:
            sql('DROP TRIGGER fail_commit_test ON registry_private.audit_events; DROP FUNCTION public.fail_commit_test();')
        self.assertEqual(self.commit()['issuance_rows'][0], original)

    def test_closed_event(self):
        self.prepare()
        sql(f"UPDATE registry_private.events SET state='closed' WHERE id='{self.event}'")
        self.assertIn('EVENT_UNAVAILABLE', self.commit(check=False).stderr)

    def test_historical_completed_replay(self):
        self.prepare()
        # An owner-built historical fixture has a valid canonical manifest/hash.
        # No client-controlled clock or test hook is exposed by production RPCs.
        sql(f"""ALTER TABLE registry_private.certificate_codes DISABLE TRIGGER protect_code_claim;
        UPDATE registry_private.certificate_codes SET year=year-1,state='issued',issued_at=clock_timestamp()-interval '1 year'
        WHERE reserving_batch_id='{self.batch}';
        ALTER TABLE registry_private.certificate_codes ENABLE TRIGGER protect_code_claim;""")
        code = self.call('get_reservation')['items'][0]['code']
        canonical = []
        for artifact in self.manifest['artifacts']:
            artifact['filename'] = code + '.' + artifact['format']
            canonical.append(dict(artifact, ordinal=1))
        canonical.sort(key=lambda a: a['format'])
        fingerprint_input = {'version': 1, 'intent_hash': self.manifest['intent_hash'], 'artifacts': canonical}
        sql(f"""UPDATE registry_private.batch_items SET jpg_sha256=decode(repeat('ab',32),'hex'),jpg_bytes=100,
        pdf_sha256=decode(repeat('ab',32),'hex'),pdf_bytes=100 WHERE batch_id='{self.batch}';
        UPDATE registry_private.batches SET state='completed',confirmed_at=clock_timestamp(),validated_generation=registry_generation,
        commit_hash=sha256(convert_to(({literal(json.dumps(fingerprint_input))}::jsonb)::text,'UTF8')) WHERE id='{self.batch}';""")
        rows = self.commit()['issuance_rows']
        self.assertEqual(rows[0]['code'], code)
        self.assertEqual(rows, self.status()['issuance_rows'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
