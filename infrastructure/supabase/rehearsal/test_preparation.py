"""Offline tests only. Never invoke PostgreSQL or use hosted credentials."""
import contextlib
import io
import json
import os
import re
import sys
import time
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import backup
import rehearsal


class SubprocessStartupTests(unittest.TestCase):
    def test_harmless_python_descendant_cannot_hold_startup_wait_open(self):
        # Offline reproduction only: Python sleepers, no PostgreSQL or networking.
        script = ("import subprocess,sys,time; "
                  "subprocess.Popen([sys.executable,'-c','import time; time.sleep(4)'],close_fds=False); ")
        for timed_out in (False, True):
            with self.subTest(timed_out=timed_out), tempfile.TemporaryDirectory() as folder:
                env = rehearsal.child_environment(Path(folder))
                began = time.monotonic()
                if timed_out:
                    with self.assertRaises(backup.PreparationError):
                        rehearsal.process([sys.executable, '-c', script + 'time.sleep(2)'], env,
                                          timeout=0.2, capture=False)
                else:
                    result = rehearsal.process([sys.executable, '-c', script], env,
                                               timeout=2, capture=False)
                    self.assertEqual(result.returncode, 0)
                self.assertLess(time.monotonic() - began, 3.5)

    def test_process_does_not_wait_for_inherited_capture_pipe_eof(self):
        with tempfile.TemporaryDirectory() as folder, patch('subprocess.run') as old_run, \
                patch('subprocess.Popen') as launch:
            env = {'PGPASSFILE': str(Path(folder) / 'pgpass.conf')}
            child = launch.return_value
            child.returncode = 0
            result = rehearsal.process(['synthetic-helper'], env, timeout=2)
            old_run.assert_not_called()
            handles = launch.call_args.kwargs
            self.assertNotEqual(handles['stdout'], subprocess.PIPE)
            self.assertNotEqual(handles['stderr'], subprocess.PIPE)
            child.communicate.assert_not_called()
            child.wait.assert_called_once_with(timeout=2)
            self.assertEqual(result.returncode, 0)

    def test_timeout_and_kill_wait_are_bounded_without_communicate(self):
        with tempfile.TemporaryDirectory() as folder, patch('subprocess.Popen') as launch:
            env = {'PGPASSFILE': str(Path(folder) / 'pgpass.conf')}
            child = launch.return_value
            child.wait.side_effect = subprocess.TimeoutExpired('synthetic', 1)
            with self.assertRaises(backup.PreparationError):
                rehearsal.process(['synthetic-helper'], env, timeout=1)
            child.kill.assert_called_once()
            self.assertEqual(child.wait.call_args_list[0].kwargs, {'timeout': 1})
            self.assertEqual(child.wait.call_args_list[1].kwargs, {'timeout': rehearsal.HELPER_KILL_TIMEOUT})
            self.assertEqual(child.wait.call_count, 2)
            child.communicate.assert_not_called()

    def test_interrupted_helper_is_reaped_with_a_bounded_wait(self):
        with tempfile.TemporaryDirectory() as folder, patch('subprocess.Popen') as launch:
            env = {'PGPASSFILE': str(Path(folder) / 'pgpass.conf')}
            child = launch.return_value
            child.wait.side_effect = [KeyboardInterrupt(), 0]
            with self.assertRaises(KeyboardInterrupt):
                rehearsal.process(['synthetic-helper'], env)
            child.kill.assert_called_once()
            self.assertEqual(child.wait.call_args.kwargs, {'timeout': rehearsal.HELPER_KILL_TIMEOUT})
            child.communicate.assert_not_called()

    def test_file_backed_output_and_sql_input_are_captured_without_console_output(self):
        with tempfile.TemporaryDirectory() as folder, patch('subprocess.Popen') as launch:
            env = {'PGPASSFILE': str(Path(folder) / 'pgpass.conf')}
            child = launch.return_value
            child.returncode = 0

            def simulate_completion(timeout):
                handles = launch.call_args.kwargs
                self.assertEqual(handles['stdin'].read(), b'SYNTHETIC_SQL')
                handles['stdout'].write(b'synthetic-output\n')
                handles['stderr'].write(b'SYNTHETIC_PRIVATE_ERROR')

            child.wait.side_effect = simulate_completion
            with contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()) as errors:
                result = rehearsal.process(['synthetic-helper'], env, input_text='SYNTHETIC_SQL')
            self.assertEqual(result.stdout, 'synthetic-output\n')
            self.assertEqual(result.stderr, 'SYNTHETIC_PRIVATE_ERROR')
            self.assertEqual(output.getvalue() + errors.getvalue(), '')

    def test_startup_discards_stdio_and_records_pid_before_database_identity_probe(self):
        with tempfile.TemporaryDirectory() as folder, patch('subprocess.Popen') as launch:
            run_id = '12345678-1234-4234-8234-123456789abc'
            root = Path(folder) / run_id
            (root / 'data').mkdir(parents=True)
            (root / 'data/postmaster.pid').write_text(f'123\n{root / "data"}\n100\n55001\n')
            state = dict(run_id=run_id, data_dir=str(root / 'data'), port=55001,
                         system_identifier='1234', database='rehearsal_' + run_id.replace('-', ''),
                         pg_bin=str(root / 'synthetic-bin'))
            launch.return_value.returncode = 0
            with patch.object(rehearsal, 'Target') as target:
                rehearsal.start_cluster(state, root)
                target.assert_not_called()
            handles = launch.call_args.kwargs
            self.assertEqual((handles['stdin'], handles['stdout'], handles['stderr']),
                             (subprocess.DEVNULL,) * 3)
            argv = launch.call_args.args[0]
            self.assertIn('-t', argv)
            self.assertEqual(argv[argv.index('-t') + 1], str(rehearsal.STARTUP_WAIT_TIMEOUT))
            self.assertIn('-h 127.0.0.1', argv[argv.index('-o') + 1])
            marker = json.loads((root / 'run.json').read_text())
            self.assertEqual((marker['pid'], marker['start_time']), ('123', '100'))

    def test_startup_timeout_or_interrupt_does_not_adopt_pid_or_stop_server(self):
        for failure in (subprocess.TimeoutExpired('synthetic', 1), KeyboardInterrupt()):
            with self.subTest(), tempfile.TemporaryDirectory() as folder, \
                    patch('subprocess.Popen') as launch, patch.object(rehearsal, 'stop_identified') as stop:
                run_id = '12345678-1234-4234-8234-123456789abc'
                root = Path(folder) / run_id
                (root / 'data').mkdir(parents=True)
                (root / 'data/postmaster.pid').write_text(f'123\n{root / "data"}\n100\n55001\n')
                (root / 'pgpass.conf').write_text('SYNTHETIC_PASSWORD')
                state = dict(run_id=run_id, data_dir=str(root / 'data'), port=55001,
                             system_identifier='1234', database='rehearsal_' + run_id.replace('-', ''),
                             pg_bin=str(root / 'synthetic-bin'))
                launch.return_value.wait.side_effect = [failure, 0]
                with self.assertRaises((backup.PreparationError, KeyboardInterrupt)):
                    rehearsal.start_cluster(state, root)
                self.assertNotIn('pid', state)
                with self.assertRaises(backup.PreparationError):
                    rehearsal.finish_run(state, root, startup_attempted=True)
                self.assertTrue((root / 'pgpass.conf').exists())
                stop.assert_not_called()


class SqlSelectionTests(unittest.TestCase):
    def test_role_export_accepts_only_the_expected_ordered_statements(self):
        source = '\n'.join(backup.EXPECTED_ROLE_STATEMENTS)
        self.assertEqual(backup.validate_roles(source), source + '\n')
        for extra in ('ALTER ROLE postgres SUPERUSER;', 'SET ROLE postgres;',
                      'CREATE DATABASE unrelated;', "COPY x FROM PROGRAM 'command';",
                      '\\connect elsewhere\n', '\\! command\n', '\\i other.sql\n',
                      'SELECT 1 \\gexec\n', '\\c elsewhere\n'):
            with self.subTest(), self.assertRaises(backup.PreparationError):
                backup.validate_roles(source + '\n' + extra)
        for statements in (backup.EXPECTED_ROLE_STATEMENTS[:-1],
                           tuple(reversed(backup.EXPECTED_ROLE_STATEMENTS)),
                           backup.EXPECTED_ROLE_STATEMENTS + backup.EXPECTED_ROLE_STATEMENTS):
            with self.subTest(), self.assertRaises(backup.PreparationError):
                backup.validate_roles('\n'.join(statements))

    def test_role_export_rejects_metacommands_hidden_in_comments(self):
        # psql processes backslash commands independently of the SQL comment lexer.
        source = '\n'.join(backup.EXPECTED_ROLE_STATEMENTS)
        with self.assertRaises(backup.PreparationError):
            backup.validate_roles('/*\n\\! command\n*/\n' + source)

    def test_splitter_preserves_function_bodies_comments_and_quoted_semicolons(self):
        source = """-- ignored ;
        CREATE FUNCTION registry_private.example() RETURNS void AS $body$
        BEGIN PERFORM ';'; /* outer /* inner ; */ comment */ END;
        $body$ LANGUAGE plpgsql;
        SELECT 'it''s;safe', "quoted;identifier";
        """
        statements = backup.split_sql(source)
        self.assertEqual(len(statements), 2)
        self.assertIn("BEGIN PERFORM ';';", statements[0])
        self.assertIn("'it''s;safe'", statements[1])

    def test_splitter_rejects_unterminated_literals_and_psql_commands(self):
        for source in ["SELECT 'broken", 'SELECT $tag$broken', '/* broken', '\\connect elsewhere\n']:
            with self.subTest(source=source), self.assertRaises(backup.PreparationError):
                backup.split_sql(source)

    def test_copy_selection_discards_managed_data_and_replication_setting(self):
        source = 'SET session_replication_role = replica;\n'
        source += 'COPY "auth"."users" ("id", "encrypted_password") FROM stdin;\nSECRET_SENTINEL\tSECRET_SENTINEL\n\\.\n'
        source += 'COPY "auth"."refresh_tokens" ("token") FROM stdin;\nTOKEN_SENTINEL\n\\.\n'
        for table in backup.TABLES:
            source += f'COPY "registry_private"."{table}" ("id") FROM stdin;\nsynthetic-{table}\n\\.\n'
        sections = backup.extract_copies(source)
        selected = ''.join(section.sql for section in sections.values())
        self.assertEqual(set(sections), set(backup.TABLES))
        self.assertNotIn('SECRET_SENTINEL', selected)
        self.assertNotIn('TOKEN_SENTINEL', selected)
        self.assertNotIn('replica', selected)
        self.assertNotIn('auth', selected)

    def test_copy_parser_rejects_missing_duplicate_malformed_or_unknown_registry_data(self):
        valid = ''.join(f'COPY "registry_private"."{table}" ("id") FROM stdin;\n\\.\n' for table in backup.TABLES)
        for source in [valid.replace('"issuers"', '"unknown"'), valid + valid,
                       valid.replace('\\.\n', '', 1), valid.replace('"id"', '"id", "id"', 1)]:
            with self.subTest(), self.assertRaises(backup.PreparationError):
                backup.extract_copies(source)

    def test_schema_selection_places_foreign_keys_after_data(self):
        source = '''CREATE SCHEMA IF NOT EXISTS "registry_private";
        ALTER TABLE ONLY "registry_private"."batches" ADD CONSTRAINT "cycle"
          FOREIGN KEY ("supersedes_batch_id") REFERENCES "registry_private"."batches"("id");
        CREATE EXTENSION IF NOT EXISTS "supabase_vault" WITH SCHEMA "vault";
        CREATE OR REPLACE FUNCTION "public"."unrelated"() RETURNS void AS $$ BEGIN END; $$ LANGUAGE plpgsql;
        '''
        schema = backup.select_schema(source, require_inventory=False)
        self.assertEqual(len(schema.foreign_keys), 1)
        self.assertNotIn('FOREIGN KEY', '\n'.join(schema.definitions))
        self.assertNotIn('supabase_vault', schema.sql)
        self.assertNotIn('public', schema.sql)

    def test_policy_and_trigger_inventory_do_not_add_function_definitions(self):
        schema = backup.select_schema('''
            CREATE POLICY "example" ON "registry_private"."batches" FOR SELECT USING (true);
            CREATE OR REPLACE TRIGGER "example" BEFORE DELETE ON "registry_private"."batches"
              FOR EACH ROW EXECUTE FUNCTION "registry_private"."protect_batch_result"();
            ''', require_inventory=False)
        self.assertEqual(schema.inventory['policies'], 1)
        self.assertEqual(schema.inventory['triggers'], 1)
        self.assertEqual(len(schema.security), 2)

    def test_table_row_types_exist_before_function_validation(self):
        schema = backup.select_schema('''
            CREATE FUNCTION "registry_private"."reservation_snapshot"() RETURNS void
              LANGUAGE plpgsql AS $$ DECLARE row registry_private.batches; BEGIN END $$;
            CREATE TABLE "registry_private"."batches" (
              "id" uuid
            );
            CREATE SCHEMA "registry_private";
            ''', require_inventory=False)
        self.assertTrue(schema.definitions[0].startswith('CREATE SCHEMA'))
        self.assertTrue(schema.definitions[1].startswith('CREATE TABLE'))
        self.assertTrue(schema.definitions[2].startswith('CREATE FUNCTION'))

    def test_copy_preserves_unicode_line_separators_and_escaped_tabs(self):
        source = ''.join(f'COPY "registry_private"."{t}" ("id") FROM stdin;\r\n'
                         'synthetic\u2028value\\tmore\r\n\\.\r\n' for t in backup.TABLES)
        sections = backup.extract_copies(source)
        self.assertEqual(sections['issuers'].rows, ['synthetic\u2028value\\tmore'])

    def test_schema_selection_rejects_unknown_registry_objects_and_missing_inventory(self):
        for source in ['DROP SCHEMA registry_private CASCADE;',
                       'CREATE TABLE registry_private.unexpected(id uuid);',
                       'CREATE SCHEMA registry_private;']:
            with self.subTest(source=source), self.assertRaises(backup.PreparationError):
                backup.select_schema(source)

    def test_checksum_failure_does_not_print_source_contents(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in backup.PINNED_HASHES:
                (Path(folder) / name).write_text('SECRET_SENTINEL', encoding='utf8')
            with self.assertRaises(backup.PreparationError) as caught:
                backup.prepare(Path(folder))
            self.assertNotIn('SECRET_SENTINEL', str(caught.exception))


class ExecutionSafetyTests(unittest.TestCase):
    def test_assertions_are_enabled_before_checks_and_guard_is_not_an_assert(self):
        sql = rehearsal.consistency_sql()
        statements = backup.split_sql(sql)
        self.assertEqual(statements[0], 'SET plpgsql.check_asserts = on;')
        block = statements[1]
        self.assertLess(block.index("IF current_setting('plpgsql.check_asserts')"),
                        re.search(r'(?m)^\s*ASSERT\s', block).start())
        self.assertIn("RAISE EXCEPTION 'REHEARSAL_ASSERTIONS_DISABLED'", block)

    def test_generated_credential_is_removed_and_cannot_follow_links(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'initdb-password').write_text('SYNTHETIC_PASSWORD')
            rehearsal.erase_credential(root, 'initdb-password')
            self.assertFalse((root / 'initdb-password').exists())
            with self.assertRaises(backup.PreparationError):
                rehearsal.erase_credential(root, 'unrelated')

    def test_credential_removal_overwrites_and_flushes_before_unlink(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'initdb-password'
            path.write_bytes(b'SYNTHETIC_PASSWORD')
            unlink = Path.unlink

            def inspect_unlink(current):
                self.assertEqual(current.read_bytes(), b'\x00' * len(b'SYNTHETIC_PASSWORD'))
                unlink(current)

            with patch.object(Path, 'unlink', inspect_unlink), patch.object(os, 'fsync', wraps=os.fsync) as flush:
                rehearsal.erase_credential(root, 'initdb-password')
                flush.assert_called_once()

    def test_credential_removal_refuses_hardlinks(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            original = root / 'original'
            original.write_bytes(b'SYNTHETIC_PASSWORD')
            os.link(original, root / 'initdb-password')
            with self.assertRaises(backup.PreparationError):
                rehearsal.erase_credential(root, 'initdb-password')
            self.assertEqual(original.read_bytes(), b'SYNTHETIC_PASSWORD')

    def test_initialization_password_is_removed_after_success_or_failure(self):
        # initdb is mocked: no executable is launched and no cluster is created.
        for failure in (False, True):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as folder, \
                    patch.object(rehearsal.secrets, 'token_urlsafe', return_value='SYNTHETIC_PASSWORD'), \
                    patch('subprocess.run') as process:
                root = Path(folder)

                def initialization(*args, **kwargs):
                    self.assertTrue((root / 'initdb-password').exists())
                    if failure:
                        raise backup.PreparationError('Synthetic initialization failure')

                with patch.object(rehearsal, 'checked_process', side_effect=initialization):
                    if failure:
                        with self.assertRaises(backup.PreparationError):
                            rehearsal.initialize_cluster(root, Path('synthetic-bin'), 55001, {})
                    else:
                        rehearsal.initialize_cluster(root, Path('synthetic-bin'), 55001, {})
                self.assertFalse((root / 'initdb-password').exists())
                self.assertTrue((root / 'pgpass.conf').exists())
                rehearsal.finish_run(None, root, startup_attempted=False)
                self.assertFalse((root / 'pgpass.conf').exists())
            process.assert_not_called()

    def test_shutdown_identity_and_outcome_control_passfile_lifetime(self):
        for outcome in ('identity-failed', 'stop-failed', 'pid-remains', 'verified'):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as folder, \
                    patch('subprocess.run') as process:
                run_id = '12345678-1234-4234-8234-123456789abc'
                root = Path(folder) / run_id
                (root / 'data').mkdir(parents=True)
                pidfile = root / 'data/postmaster.pid'
                pidfile.write_text(f'123\n{root / "data"}\n100\n55001\n')
                (root / 'pgpass.conf').write_text('SYNTHETIC_PASSWORD')
                state = dict(run_id=run_id, data_dir=str(root / 'data'), port=55001,
                             system_identifier='1234', database='rehearsal_' + run_id.replace('-', ''),
                             pid='123', start_time='100')

                def stop(*args, **kwargs):
                    self.assertTrue((root / 'pgpass.conf').exists())
                    if outcome == 'stop-failed':
                        raise backup.PreparationError('Synthetic stop failure')
                    if outcome == 'verified':
                        pidfile.unlink()

                with patch.object(rehearsal, 'Target') as target, \
                        patch.object(rehearsal, 'checked_process', side_effect=stop) as stop_process:
                    if outcome == 'identity-failed':
                        target.return_value.identify.side_effect = backup.PreparationError('Synthetic identity mismatch')
                    if outcome == 'verified':
                        rehearsal.stop_identified(state, root)
                        self.assertTrue(state['shutdown_verified'])
                        self.assertFalse((root / 'pgpass.conf').exists())
                    else:
                        with self.assertRaises(backup.PreparationError):
                            rehearsal.stop_identified(state, root)
                        self.assertTrue((root / 'pgpass.conf').exists())
                        self.assertNotIn('shutdown_verified', state)
                    if outcome == 'identity-failed':
                        stop_process.assert_not_called()
                    else:
                        stop_process.assert_called_once()
            process.assert_not_called()

    def test_missing_pid_requires_previously_verified_shutdown_for_credential_cleanup(self):
        for verified in (False, True):
            with self.subTest(verified=verified), tempfile.TemporaryDirectory() as folder, \
                    patch('subprocess.run') as process:
                run_id = '12345678-1234-4234-8234-123456789abc'
                root = Path(folder) / run_id
                (root / 'data').mkdir(parents=True)
                (root / 'pgpass.conf').write_text('SYNTHETIC_PASSWORD')
                state = dict(run_id=run_id, data_dir=str(root / 'data'), port=55001,
                             system_identifier='1234', database='rehearsal_' + run_id.replace('-', ''),
                             shutdown_verified=verified)
                if verified:
                    rehearsal.stop_identified(state, root)
                    self.assertFalse((root / 'pgpass.conf').exists())
                else:
                    with self.assertRaises(backup.PreparationError):
                        rehearsal.stop_identified(state, root)
                    self.assertTrue((root / 'pgpass.conf').exists())
            process.assert_not_called()

    def test_cleanup_before_start_removes_connection_credential_without_processes(self):
        with tempfile.TemporaryDirectory() as folder, patch('subprocess.run') as process:
            root = Path(folder)
            (root / 'pgpass.conf').write_text('SYNTHETIC_PASSWORD')
            rehearsal.finish_run(None, root, startup_attempted=False)
            self.assertFalse((root / 'pgpass.conf').exists())
        process.assert_not_called()

    def test_ambiguous_startup_retains_credentials_and_refuses_automatic_stop(self):
        for pid_present in (False, True):
            with self.subTest(pid_present=pid_present), tempfile.TemporaryDirectory() as folder, \
                    patch.object(rehearsal, 'stop_identified') as stop, patch('subprocess.run') as process:
                root = Path(folder)
                (root / 'data').mkdir()
                (root / 'pgpass.conf').write_text('SYNTHETIC_PASSWORD')
                if pid_present:
                    (root / 'data/postmaster.pid').write_text('123\n')
                with self.assertRaises(backup.PreparationError):
                    rehearsal.finish_run({'run_id': 'synthetic'}, root, startup_attempted=True)
                self.assertTrue((root / 'pgpass.conf').exists())
            stop.assert_not_called()
            process.assert_not_called()

    def test_failed_verified_shutdown_preserves_connection_credentials(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(rehearsal, 'stop_identified', side_effect=backup.PreparationError('Stop failed')) as stop:
            root = Path(folder)
            (root / 'data').mkdir()
            (root / 'data/postmaster.pid').write_text('123\n')
            (root / 'pgpass.conf').write_text('SYNTHETIC_PASSWORD')
            with self.assertRaises(backup.PreparationError):
                rehearsal.finish_run({'pid': '123', 'start_time': '100'}, root, startup_attempted=True)
            self.assertTrue((root / 'pgpass.conf').exists())
            stop.assert_called_once()

    def test_startup_options_bind_only_ipv4_loopback(self):
        options = rehearsal.startup_options(55001)
        self.assertEqual(options.split()[:4], ['-h', '127.0.0.1', '-p', '55001'])
        self.assertNotIn('0.0.0.0', options)

    def test_execute_requires_explicit_approval_before_preparation_or_processes(self):
        with patch.object(rehearsal, 'execute') as execute, patch('subprocess.run') as process:
            with contextlib.redirect_stderr(io.StringIO()):
                result = rehearsal.main(['execute'])
        self.assertNotEqual(result, 0)
        execute.assert_not_called()
        process.assert_not_called()

    def test_inspection_never_launches_processes_or_creates_directories(self):
        prepared = SimpleNamespace(summary={'execution_performed': False})
        with patch.object(backup, 'prepare', return_value=prepared), \
                patch.object(rehearsal, 'dependency_files'), \
                patch('subprocess.run') as process, patch.object(Path, 'mkdir') as mkdir, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(rehearsal.main(['inspect']), 0)
        process.assert_not_called()
        mkdir.assert_not_called()

    def test_local_credentials_are_unique_inputs_outside_command_arguments(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(rehearsal.secrets, 'token_urlsafe', return_value='SYNTHETIC_PASSWORD'), \
                patch('subprocess.run') as process:
            root = Path(folder)
            rehearsal.write_local_credentials(root, 55001)
            self.assertEqual((root / 'initdb-password').read_text(), 'SYNTHETIC_PASSWORD\n')
            self.assertEqual((root / 'pgpass.conf').read_text(), '127.0.0.1:55001:*:postgres:SYNTHETIC_PASSWORD\n')
            with self.assertRaises(FileExistsError):
                rehearsal.write_local_credentials(root, 55001)
        process.assert_not_called()

    def test_sql_commands_are_atomic_except_database_creation(self):
        target = object.__new__(rehearsal.Target)
        target.pg_bin = Path('synthetic-bin')
        target.state = {'port': 55001}
        target.database, target.env = 'synthetic', {}
        result = subprocess.CompletedProcess([], 0, stdout='', stderr='')
        with patch.object(rehearsal, 'process', return_value=result) as process:
            target.sql('SELECT 1;')
            self.assertIn('--single-transaction', process.call_args.args[0])
            target.sql('CREATE DATABASE synthetic;', transaction=False)
            self.assertNotIn('--single-transaction', process.call_args.args[0])

    def test_process_errors_are_redacted_in_cli_reports(self):
        with patch.object(rehearsal, 'inspect_backup', side_effect=RuntimeError('SECRET_SENTINEL')), \
                contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(rehearsal.main(['inspect']), 1)
        self.assertNotIn('SECRET_SENTINEL', errors.getvalue())

    def test_mismatched_postmaster_refuses_stop_before_process_launch(self):
        with tempfile.TemporaryDirectory() as folder, patch('subprocess.run') as process:
            run_id = '12345678-1234-4234-8234-123456789abc'
            root = Path(folder) / run_id
            (root / 'data').mkdir(parents=True)
            (root / 'data/postmaster.pid').write_text(f'123\n{root / "data"}\n100\n55001\n')
            state = dict(run_id=run_id, data_dir=str(root / 'data'), port=55001,
                         system_identifier='1234', database='rehearsal_' + run_id.replace('-', ''),
                         pid='999', start_time='100')
            with self.assertRaises(backup.PreparationError):
                rehearsal.stop_identified(state, root)
        process.assert_not_called()

    def test_stop_requires_approval_and_valid_run_identifier(self):
        with patch.object(rehearsal, 'stop_run') as stop, patch('subprocess.run') as process:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertNotEqual(rehearsal.main(['stop', '--run-id', 'not-a-uuid']), 0)
        stop.assert_not_called()
        process.assert_not_called()

    def test_target_guard_rejects_external_or_mismatched_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            run_id = '12345678-1234-4234-8234-123456789abc'
            expected = root / run_id
            for state in [dict(run_id=run_id, data_dir=str(root / 'existing')),
                          dict(run_id='wrong', data_dir=str(expected / 'data'))]:
                with self.subTest(), self.assertRaises(backup.PreparationError):
                    rehearsal.validate_marker(state, expected, run_id)


if __name__ == '__main__':
    unittest.main(verbosity=2)
