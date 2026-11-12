"""Prepare offline by default. Execution requires explicit later approval."""
import argparse
from contextlib import ExitStack
import csv
import io
import json
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import subprocess
import sys
import tempfile
import uuid

import backup

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[2]
DEFAULT_PG = Path('C:/Program Files/PostgreSQL/17/bin')
FORMAT_SQL = "SET TIME ZONE 'UTC'; SET DateStyle='ISO, MDY'; SET bytea_output='hex'; SET extra_float_digits=3;\n"
HELPER_KILL_TIMEOUT = 5
STARTUP_WAIT_TIMEOUT = 30
STARTUP_PROCESS_TIMEOUT = 45
MAX_CAPTURE_BYTES = 16 * 1024 * 1024


def backup_directory():
    return Path(os.environ['LOCALAPPDATA']) / 'GDGoC/RegistryHostedDev/backups/20261111-132835'


def runs_directory():
    return Path(os.environ['LOCALAPPDATA']) / 'GDGoC/RegistryRecoveryRehearsals'


def safe_path(path):
    """Reject symlinks and Windows junctions before resolving any write/stop target."""
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.exists():
            backup.require(not part.is_symlink() and not part.is_junction(), 'Reparse path refused')
    return path.resolve()


def dependency_files(pg_bin):
    pg_bin = safe_path(pg_bin)
    for name in ('initdb.exe', 'pg_ctl.exe', 'pg_controldata.exe', 'psql.exe'):
        backup.require((pg_bin / name).is_file(), 'PostgreSQL executable missing')
    backup.require((pg_bin.parent / 'share/extension/pgcrypto.control').is_file(), 'pgcrypto dependency missing')
    return pg_bin


def inspect_backup(directory, pg_bin):
    prepared = backup.prepare(safe_path(directory))
    dependency_files(pg_bin)
    return prepared.summary | {'dependency_files_present': True,
                               'postgres_version_verified': False,
                               'approval_required_for_execution': True}


def child_environment(root):
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(('PG', 'PSQL'))}
    env.update(PGPASSFILE=str(root / 'pgpass.conf'),
               PGSERVICEFILE=str(root / 'unused-servicefile'),
               PGCONNECT_TIMEOUT='5', PGAPPNAME='gdg-registry-rehearsal', PGCLIENTENCODING='UTF8', LC_ALL='C')
    return env


def terminate_helper(child):
    """Reap only our directly spawned helper, never its PostgreSQL descendants."""
    try:
        child.kill()
    except OSError:
        pass
    try:
        child.wait(timeout=HELPER_KILL_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        # Retain run credentials; callers must treat startup/shutdown as ambiguous.
        pass


def process(argv, env, input_text=None, timeout=60, capture=True):
    """Bound the direct process wait without inherited PIPE EOF or communicate().

    Captured SQL input/output is spooled only in the protected run directory.
    Startup discards all three standard streams and creates no capture files.
    """
    command = [str(arg) for arg in argv]
    try:
        with ExitStack() as files:
            stdin = stdout = stderr = subprocess.DEVNULL
            if capture or input_text is not None:
                root = safe_path(Path(env['PGPASSFILE']).parent)
            if input_text is not None:
                stdin = files.enter_context(tempfile.TemporaryFile(dir=root))
                stdin.write(input_text.encode('utf8'))
                stdin.seek(0)
            if capture:
                stdout = files.enter_context(tempfile.TemporaryFile(dir=root))
                stderr = files.enter_context(tempfile.TemporaryFile(dir=root))
            child = subprocess.Popen(command, stdin=stdin, stdout=stdout, stderr=stderr,
                                     env=env, close_fds=True,
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try:
                child.wait(timeout=timeout)
            except BaseException:
                # Never call communicate(), including after kill on Windows. Its
                # output-reader threads can outlive pg_ctl while postgres owns a pipe.
                terminate_helper(child)
                raise
            output = []
            for handle in (stdout, stderr):
                if capture:
                    handle.seek(0)
                    raw = handle.read(MAX_CAPTURE_BYTES + 1)
                    backup.require(len(raw) <= MAX_CAPTURE_BYTES, 'Local subprocess output exceeded limit')
                    output.append(raw.decode('utf8'))
                else:
                    output.append('')
            return subprocess.CompletedProcess(command, child.returncode, *output)
    except (OSError, subprocess.TimeoutExpired):
        raise backup.PreparationError('Local subprocess failed or timed out') from None


def checked_process(argv, env, phase, input_text=None, timeout=60, capture=True):
    result = process(argv, env, input_text, timeout, capture=capture)
    backup.require(result.returncode == 0, phase + ' failed')
    return result.stdout


def protect_directory(root):
    """Restrict the new run directory before database files or a manifest exist."""
    backup.require(os.name == 'nt', 'Execution requires the reviewed Windows environment')
    env = child_environment(root)
    identity = checked_process(['whoami.exe', '/user', '/fo', 'csv', '/nh'], env, 'Windows identity lookup')
    rows = list(csv.reader(io.StringIO(identity)))
    backup.require(len(rows) == 1 and len(rows[0]) == 2 and
                   re.fullmatch(r'S-1-\d+(?:-\d+)+', rows[0][1]), 'Windows SID lookup failed')
    checked_process(['icacls.exe', root, '/inheritance:r', '/grant:r',
                     '*' + rows[0][1] + ':(OI)(CI)F', '*S-1-5-18:(OI)(CI)F'], env, 'Run directory protection')


def write_local_credentials(root, port):
    # Fresh local credentials are unrelated to Supabase and never enter argv or logs.
    password = secrets.token_urlsafe(48)
    with (root / 'initdb-password').open('x', encoding='utf8') as handle:
        handle.write(password + '\n')
    with (root / 'pgpass.conf').open('x', encoding='utf8') as handle:
        handle.write(f'127.0.0.1:{port}:*:postgres:{password}\n')


def erase_credential(root, name):
    """Overwrite, flush and unlink only a generated credential's original inode.

    Filesystem snapshots and SSD wear levelling can retain prior blocks. This
    shortens ordinary file exposure without promising forensic media erasure.
    """
    backup.require(name in ('initdb-password', 'pgpass.conf'), 'Unknown local credential file')
    path = safe_path(root) / name
    try:
        original = path.lstat()
    except FileNotFoundError:
        return
    try:
        backup.require(stat.S_ISREG(original.st_mode) and original.st_nlink == 1 and
                       not (getattr(original, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)
                       and original.st_size <= 4096, 'Unsafe local credential file')
        with path.open('r+b') as handle:
            opened = os.fstat(handle.fileno())
            backup.require((opened.st_dev, opened.st_ino, opened.st_nlink) ==
                           (original.st_dev, original.st_ino, 1), 'Local credential identity changed')
            handle.write(b'\x00' * opened.st_size)
            handle.flush()
            os.fsync(handle.fileno())
        current = path.lstat()
        backup.require((current.st_dev, current.st_ino, current.st_nlink) ==
                       (original.st_dev, original.st_ino, 1), 'Local credential identity changed')
        path.unlink()
    except OSError:
        raise backup.PreparationError('Local credential removal failed') from None


def initialize_cluster(root, pg_bin, port, env):
    try:
        write_local_credentials(root, port)
        checked_process([pg_bin / 'initdb.exe', '-D', root / 'data', '-U', 'postgres',
                         '--auth-host=scram-sha-256', '--auth-local=scram-sha-256',
                         '--pwfile=' + str(root / 'initdb-password'),
                         '--no-locale', '-E', 'UTF8'], env, 'New cluster initialization', timeout=120)
    finally:
        # initdb is the only consumer, including when initialization fails.
        erase_credential(root, 'initdb-password')


def validate_marker(state, root, run_id):
    backup.require(str(uuid.UUID(run_id)) == run_id, 'Invalid run identifier')
    root = safe_path(root)
    backup.require(state.get('run_id') == run_id and root.name == run_id, 'Run identity mismatch')
    backup.require(state.get('data_dir') == str(root / 'data'), 'Cluster path mismatch')
    backup.require(safe_path(root / 'data') == root / 'data', 'Cluster path escaped run directory')
    backup.require(state.get('database') == 'rehearsal_' + uuid.UUID(run_id).hex, 'Database name mismatch')
    backup.require(isinstance(state.get('port'), int) and 1024 <= state['port'] <= 65535, 'Invalid rehearsal port')
    backup.require(re.fullmatch(r'\d+', state.get('system_identifier', '')) is not None,
                   'Cluster system identifier missing')


class Target:
    def __init__(self, state, root, database=None):
        self.state, self.root = state, root
        self.database = database or state['database']
        self.pg_bin = dependency_files(Path(state['pg_bin']))
        self.env = child_environment(root)

    def sql(self, statement, phase='SQL validation', expect=None, transaction=True):
        argv = [self.pg_bin / 'psql.exe', '-X', '-w', '-h', '127.0.0.1', '-p', str(self.state['port']),
                '-U', 'postgres', '-d', self.database, '-v', 'ON_ERROR_STOP=1', '-v', 'VERBOSITY=terse',
                '-qAt']
        if transaction:
            argv.append('--single-transaction')
        argv.extend(['-f', '-'])
        result = process(argv, self.env, FORMAT_SQL + statement, timeout=120)
        if expect:
            backup.require(result.returncode == 3 and re.search(r'\b' + re.escape(expect) + r'\b', result.stderr),
                           'Expected SQL rejection missing')
            return ''
        backup.require(result.returncode == 0, phase + ' failed')
        return result.stdout.rstrip('\r\n')

    def identify(self):
        result = self.sql("SELECT json_build_object('data_dir',current_setting('data_directory'),"
                          "'port',current_setting('port'),'system_identifier',system_identifier::text) "
                          'FROM pg_control_system();', 'Target identity check')
        try:
            identity = json.loads(result)
        except (ValueError, TypeError):
            raise backup.PreparationError('Target identity response invalid') from None
        backup.require(safe_path(identity['data_dir']) == self.root / 'data' and
                       identity['port'] == str(self.state['port']) and
                       identity['system_identifier'] == self.state['system_identifier'],
                       'Connected PostgreSQL cluster identity mismatch')

    def copy_rows(self, table, columns, where=''):
        backup.require(table in backup.TABLES, 'Unknown fidelity table')
        backup.require(all(re.fullmatch(backup.IDENT, c) for c in columns), 'Unknown fidelity column')
        names = ','.join('"' + c + '"' for c in columns)
        output = self.sql(f'COPY (SELECT {names} FROM registry_private."{table}" {where}) TO STDOUT;',
                          'History comparison')
        return sorted(line.removesuffix('\r') for line in output.split('\n')) if output else []


def bootstrap_sql(prepared):
    references = ','.join("('" + identity + "')" for identity in prepared.auth_ids)
    return """CREATE ROLE anon NOLOGIN;
CREATE ROLE authenticated NOLOGIN;
CREATE ROLE service_role NOLOGIN BYPASSRLS;
CREATE ROLE authenticator NOLOGIN NOINHERIT;
GRANT anon,authenticated,service_role TO authenticator;
""" + prepared.roles_sql + """
CREATE SCHEMA auth AUTHORIZATION postgres;
CREATE TABLE auth.users(id uuid PRIMARY KEY);
CREATE SCHEMA extensions AUTHORIZATION postgres;
CREATE EXTENSION pgcrypto WITH SCHEMA extensions;
GRANT USAGE ON SCHEMA extensions TO registry_reserver;
GRANT EXECUTE ON FUNCTION extensions.gen_random_bytes(integer) TO registry_reserver;
""" + 'INSERT INTO auth.users(id) VALUES ' + references + ';\n'


def restore_sql(prepared):
    return bootstrap_sql(prepared) + '\n'.join(prepared.schema.definitions) + '\n' + \
        ''.join(prepared.data[t].sql for t in backup.TABLES) + '\n'.join(
            prepared.schema.foreign_keys + prepared.schema.security)


def consistency_sql():
    return 'SET plpgsql.check_asserts = on;\n' + (HERE / 'checks.sql').read_text(encoding='utf8')


def validate_restore(target, prepared):
    target.identify()
    for table in backup.TABLES:
        section = prepared.data[table]
        # PostgreSQL COPY text preserves bytea, bigint and microsecond timestamp precision.
        backup.require(target.copy_rows(table, section.columns) == sorted(section.rows), 'Restored data differs from backup')
    target.sql(consistency_sql(), 'Database security and consistency checks')
    target.sql("UPDATE registry_private.certificate_codes SET suffix=(suffix+1)%10000 WHERE state='issued';",
               expect='Code claims cannot be reassigned or terminal claims modified')
    target.sql("DELETE FROM registry_private.audit_events;", expect='Registry history is immutable')


def save_marker(root, state):
    # Only nonsecret cluster identity and externally generated rehearsal generation go here.
    temporary = root / 'run.next.json'
    with temporary.open('x', encoding='utf8') as handle:
        json.dump(state, handle, indent=2)
    temporary.replace(root / 'run.json')


def stop_identified(state, root):
    validate_marker(state, root, state['run_id'])
    pidfile = root / 'data/postmaster.pid'
    if not pidfile.exists():
        backup.require(state.get('shutdown_verified') is True,
                       'Postmaster state ambiguous; credentials retained for manual recovery')
        erase_credential(root, 'pgpass.conf')
        return
    lines = pidfile.read_text(encoding='utf8').splitlines()
    backup.require(len(lines) >= 4 and lines[0].isdigit() and
                   safe_path(lines[1]) == root / 'data' and int(lines[3]) == state['port'],
                   'Postmaster identity mismatch; automatic stop refused')
    backup.require(state.get('pid') == lines[0] and state.get('start_time') == lines[2],
                   'Postmaster process changed; automatic stop refused')
    target = Target(state, root, database='postgres')
    target.identify()
    checked_process([target.pg_bin / 'pg_ctl.exe', '-D', root / 'data', '-m', 'fast', '-w', 'stop'],
                    target.env, 'Identified cluster shutdown')
    backup.require(not pidfile.exists(), 'Shutdown outcome ambiguous; credentials retained for manual recovery')
    state['shutdown_verified'] = True
    save_marker(root, state)
    erase_credential(root, 'pgpass.conf')


def finish_run(state, root, startup_attempted):
    if not startup_attempted:
        erase_credential(root, 'pgpass.conf')
        return
    # A missing PID file does not prove that an interrupted startup never started
    # a server. Preserve credentials and require review when identity is incomplete.
    backup.require(state is not None and 'pid' in state and 'start_time' in state and
                   (root / 'data/postmaster.pid').exists(),
                   'Startup outcome ambiguous; credentials retained for manual recovery')
    stop_identified(state, root)


def stop_run(run_id):
    backup.require(str(uuid.UUID(run_id)) == run_id, 'Invalid run identifier')
    root = safe_path(runs_directory() / run_id)
    state = json.loads((root / 'run.json').read_text(encoding='utf8'))
    stop_identified(state, root)
    return {'run_id': run_id, 'identified_cluster_stopped': True,
            'cluster_files_deleted': False, 'connection_credentials_removed': True}


def startup_options(port):
    backup.require(isinstance(port, int) and 1024 <= port <= 65535, 'Invalid rehearsal port')
    return (f'-h 127.0.0.1 -p {port} -c log_statement=none -c log_min_messages=panic '
            '-c log_min_error_statement=panic -c log_error_verbosity=terse '
            '-c log_parameter_max_length_on_error=0')


def start_cluster(state, root):
    validate_marker(state, root, state['run_id'])
    pg_bin = Path(state['pg_bin'])
    checked_process([pg_bin / 'pg_ctl.exe', '-D', root / 'data', '-l', root / 'postgres.log',
                     '-o', startup_options(state['port']), '-w', '-t', str(STARTUP_WAIT_TIMEOUT), 'start'],
                    child_environment(root), 'New cluster startup', timeout=STARTUP_PROCESS_TIMEOUT,
                    capture=False)
    # Save the directory-specific postmaster identity before a database connection
    # or restore. A failed/timed-out/interrupted pg_ctl must not adopt an observed PID.
    pidlines = (root / 'data/postmaster.pid').read_text(encoding='utf8').splitlines()
    backup.require(len(pidlines) >= 4 and pidlines[0].isdigit() and int(pidlines[0]) > 0 and
                   pidlines[2].isdigit() and int(pidlines[2]) > 0 and
                   safe_path(pidlines[1]) == root / 'data' and pidlines[3] == str(state['port']),
                   'Startup postmaster identity mismatch; manual recovery required')
    state.update(pid=pidlines[0], start_time=pidlines[2])
    save_marker(root, state)


def execute(directory, pg_bin):
    prepared = backup.prepare(safe_path(directory))
    pg_bin = dependency_files(pg_bin)
    run_id = str(uuid.uuid4())
    parent = safe_path(runs_directory())
    source = safe_path(directory)
    backup.require(not parent.is_relative_to(REPOSITORY) and not parent.is_relative_to(source)
                   and not source.is_relative_to(parent), 'Unsafe run directory location')
    parent.mkdir(parents=True, exist_ok=True)
    root = parent / run_id
    root.mkdir(exist_ok=False)
    protect_directory(root)
    env = child_environment(root)
    for name in ('initdb.exe', 'psql.exe', 'pg_ctl.exe', 'pg_controldata.exe'):
        version = checked_process([pg_bin / name, '--version'], env, 'PostgreSQL version check')
        backup.require(re.search(r'\(PostgreSQL\) 17\.', version) is not None, 'PostgreSQL 17 required')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    state = None
    startup_attempted = False
    try:
        initialize_cluster(root, pg_bin, port, env)
        control = checked_process([pg_bin / 'pg_controldata.exe', root / 'data'], env, 'New cluster identity')
        system_id = re.search(r'^Database system identifier:\s*(\d+)\s*$', control, re.M)
        backup.require(system_id is not None, 'New cluster identity unavailable')
        state = dict(run_id=run_id, data_dir=str(root / 'data'), database='rehearsal_' + uuid.UUID(run_id).hex,
                     port=port, pg_bin=str(pg_bin), system_identifier=system_id[1],
                     external_generation=str(uuid.uuid4()), source_hashes=prepared.hashes)
        save_marker(root, state)
        print(json.dumps({'run_id': run_id, 'phase': 'prepared-new-cluster'}), flush=True)
        startup_attempted = True
        start_cluster(state, root)
        admin = Target(state, root, database='postgres')
        admin.identify()
        admin.sql(f'CREATE DATABASE "{state["database"]}" TEMPLATE template0;',
                  'New recovery database creation', transaction=False)
        target = Target(state, root)
        target.identify()
        target.sql(restore_sql(prepared), 'Registry-only restore')
        validate_restore(target, prepared)
        import controls
        controls.run(target, prepared, state['external_generation'])
        # Detect any source change during execution without overwriting source files.
        backup.require(backup.prepare(directory).hashes == prepared.hashes, 'Source backup changed during rehearsal')
        return {'run_id': run_id, 'restore_fidelity': True, 'security_checks': True,
                'recovery_control_checks': True, 'safe_to_reopen': False}
    finally:
        finish_run(state, root, startup_attempted)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('inspect', 'execute', 'stop'))
    parser.add_argument('--backup-dir', type=Path, default=None)
    parser.add_argument('--postgres-bin', type=Path, default=DEFAULT_PG)
    parser.add_argument('--approve-local-restore', action='store_true')
    parser.add_argument('--approve-stop', action='store_true')
    parser.add_argument('--run-id')
    args = parser.parse_args(argv)
    try:
        if args.mode == 'execute':
            backup.require(args.approve_local_restore, 'Explicit approval switch required for local restore')
            result = execute(args.backup_dir or backup_directory(), args.postgres_bin)
        elif args.mode == 'stop':
            backup.require(args.approve_stop and args.run_id, 'Explicit stop approval and run identifier required')
            result = stop_run(args.run_id)
        else:
            result = inspect_backup(args.backup_dir or backup_directory(), args.postgres_bin)
        print(json.dumps(result, indent=2))
        return 0
    except backup.PreparationError as error:
        print('Rehearsal stopped: ' + str(error), file=sys.stderr)
    except Exception:
        # Exception messages/tracebacks may contain SQL data or subprocess output.
        print('Rehearsal stopped: local preparation or safety check failed', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
