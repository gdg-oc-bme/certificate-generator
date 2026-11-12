"""Strict, offline selection of the reviewed 20261111-132835 backup.

The hash allowlist makes this a rehearsal of one reviewed snapshot, rather than
an arbitrary SQL executor. Nothing in this module starts processes or writes files.
"""
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import uuid


TABLES = ('registry_state', 'issuers', 'events', 'certificates', 'batches',
          'certificate_codes', 'batch_items', 'audit_events')
API_FUNCTIONS = frozenset(('abandon_reservation', 'commit_batch', 'get_batch_status',
                           'get_context', 'get_reservation', 'list_batches',
                           'lookup_certificate', 'reserve_batch'))
OWNER_FUNCTIONS = frozenset(('begin_recovery', 'finish_recovery', 'protect_batch_result',
                             'protect_code_claim', 'protect_item_snapshot', 'reject_history_change'))
PRIVATE_FUNCTIONS = OWNER_FUNCTIONS | frozenset((
    'authorize_reservation', 'batch_certificates_owned_by', 'completed_batch_status',
    'require_certificate_access', 'reservation_snapshot', 'validate_issuance_links'))
PINNED_HASHES = {
    'roles.sql': '752f06230453dc7fc7cdb4a8f60204c380eb4a5ae20813ab5502e8d373fea293',
    'schema.sql': 'a8c11c6ba664caa40752a12cb4518b19201b4f77130497f86f0512955714e976',
    'data.sql': '3b72b2392baaa077f3547f97487ec18b68103d0e79b15c03d0f7b8d59f613a81',
}
IDENT = r'[a-z_][a-z_0-9]*'
EXPECTED_ROLE_STATEMENTS = (
    'SET default_transaction_read_only = off;',
    "SET client_encoding = 'UTF8';",
    'SET standard_conforming_strings = on;',
    'CREATE ROLE registry_reserver;',
    'ALTER ROLE registry_reserver WITH NOINHERIT NOCREATEROLE NOCREATEDB NOLOGIN NOBYPASSRLS;',
    "ALTER ROLE anon SET statement_timeout TO '3s';",
    "ALTER ROLE authenticated SET statement_timeout TO '8s';",
    "ALTER ROLE authenticator SET statement_timeout TO '8s';",
    'RESET ALL;',
)


class PreparationError(Exception):
    """Messages must be fixed classifications, never SQL or source record contents."""


def require(condition, message):
    if not condition:
        raise PreparationError(message)


def split_sql(source):
    """Split statements outside SQL strings, identifiers, comments and dollar bodies."""
    statements, current = [], []
    pos = 0
    while pos < len(source):
        if source.startswith('--', pos):
            end = source.find('\n', pos)
            pos = len(source) if end < 0 else end + 1
            current.append(' ')
            continue
        if source.startswith('/*', pos):
            depth = 1
            pos += 2
            while pos < len(source) and depth:
                if source.startswith('/*', pos):
                    depth += 1
                    pos += 2
                elif source.startswith('*/', pos):
                    depth -= 1
                    pos += 2
                else:
                    pos += 1
            require(depth == 0, 'Unterminated SQL comment')
            current.append(' ')
            continue
        char = source[pos]
        if char in ("'", '"'):
            start = pos
            escaped = char == "'" and pos > 0 and source[pos - 1] in 'eE'
            pos += 1
            while pos < len(source):
                if escaped and source[pos] == '\\':
                    pos += 2
                elif source[pos] == char:
                    pos += 1
                    if pos < len(source) and source[pos] == char:
                        pos += 1
                    else:
                        break
                else:
                    pos += 1
            else:
                raise PreparationError('Unterminated SQL quoted value')
            current.append(source[start:pos])
            continue
        dollar = re.match(r'\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$', source[pos:]) if char == '$' else None
        if dollar:
            tag = dollar.group()
            end = source.find(tag, pos + len(tag))
            require(end >= 0, 'Unterminated SQL function body')
            current.append(source[pos:end + len(tag)])
            pos = end + len(tag)
            continue
        require(char != '\\', 'Unexpected psql command in schema')
        current.append(char)
        pos += 1
        if char == ';':
            statements.append(''.join(current).strip())
            current = []
    require(not ''.join(current).strip(), 'Unterminated SQL statement')
    return statements


def identifiers(source):
    # Used only for classification. Execution always uses the original statement.
    return re.sub(r'"([a-z_][a-z_0-9]*)"', r'\1', source)


def validate_roles(source):
    # Reject backslash commands even inside comments: psql's command handling is
    # separate from PostgreSQL's SQL lexer. This reviewed role file needs none.
    require('\\' not in source, 'Role export contains a prohibited psql command')
    statements = tuple(' '.join(identifiers(statement).split()) for statement in split_sql(source))
    require(statements == EXPECTED_ROLE_STATEMENTS, 'Role export statement allowlist mismatch')
    # Execute the canonical allowlist, never unvalidated role-file text.
    return '\n'.join(EXPECTED_ROLE_STATEMENTS) + '\n'


@dataclass
class Schema:
    definitions: list
    foreign_keys: list
    security: list
    columns: dict
    inventory: dict

    @property
    def sql(self):
        return '\n'.join(self.definitions + self.foreign_keys + self.security)


def select_schema(source, require_inventory=True):
    definitions, foreign_keys, security = [], [], []
    columns, functions, owners = {}, set(), {}
    inventory = dict(schemas=0, tables=0, foreign_keys=0, triggers=0, policies=0, indexes=0, keys=0)
    for statement in split_sql(source):
        normalized = identifiers(statement)
        target = None
        kind = None
        patterns = (
            ('schema', rf'^(?:CREATE SCHEMA(?: IF NOT EXISTS)?|ALTER SCHEMA) ({IDENT})'),
            ('function', rf'^(?:CREATE(?: OR REPLACE)?|ALTER) FUNCTION ({IDENT})\.({IDENT})\('),
            ('table', rf'^(?:CREATE TABLE(?: IF NOT EXISTS)?|ALTER TABLE(?: ONLY)?) ({IDENT})\.({IDENT})\b'),
            ('index', rf'^CREATE(?: UNIQUE)? INDEX {IDENT} ON ({IDENT})\.({IDENT})\b'),
            ('policy', rf'^CREATE POLICY {IDENT} ON ({IDENT})\.({IDENT})\b'),
            ('trigger', rf'^CREATE(?: OR REPLACE)?(?: CONSTRAINT)? TRIGGER {IDENT}[\s\S]*? ON ({IDENT})\.({IDENT})\b'),
            ('acl', rf'^(?:GRANT|REVOKE) [\s\S]*? ON (?:FUNCTION|TABLE) ({IDENT})\.({IDENT})\b'),
            ('schema_acl', rf'^(?:GRANT|REVOKE) [\s\S]*? ON SCHEMA ({IDENT})\b'),
        )
        for candidate, pattern in patterns:
            match = re.match(pattern, normalized)
            if match:
                kind, target = candidate, match.groups()
                break
        if not target or target[0] not in ('registry_api', 'registry_private'):
            # A registry object in an unknown statement form must never silently disappear.
            require(not re.search(r'\bregistry_(?:api|private)\b', normalized),
                    'Unrecognized registry DDL statement')
            continue
        schema = target[0]
        name = target[1] if len(target) > 1 else None
        if kind == 'function' or (kind == 'acl' and ' ON FUNCTION ' in normalized):
            expected = API_FUNCTIONS if schema == 'registry_api' else PRIVATE_FUNCTIONS
            require(name in expected, 'Unexpected registry function')
        elif name:
            require(schema == 'registry_private' and name in TABLES, 'Unexpected registry table')
        if kind == 'function' and normalized.startswith('CREATE'):
            functions.add((schema, name))
        if kind == 'function' and normalized.startswith('ALTER FUNCTION'):
            match = re.search(r' OWNER TO (\w+);$', normalized)
            require(match is not None, 'Function owner missing')
            owners[(schema, name)] = match[1]
        if kind == 'schema' and normalized.startswith('CREATE SCHEMA'):
            inventory['schemas'] += 1
        if kind == 'table' and normalized.startswith('CREATE TABLE'):
            inventory['tables'] += 1
            require(name not in columns, 'Duplicate registry table definition')
            columns[name] = tuple(re.findall(r'^\s+"(' + IDENT + r')"\s+', statement, re.M))
            require(bool(columns[name]), 'Registry columns missing')
        if kind == 'table' and ' FOREIGN KEY ' in normalized:
            inventory['foreign_keys'] += 1
            foreign_keys.append(statement)
        elif kind in ('trigger', 'policy', 'acl', 'schema_acl'):
            if kind in ('trigger', 'policy'):
                inventory['policies' if kind == 'policy' else 'triggers'] += 1
            security.append(statement)
        else:
            if kind == 'index':
                inventory['indexes'] += 1
            if kind == 'table' and re.search(r' ADD CONSTRAINT .*? (?:PRIMARY KEY|UNIQUE) ', normalized):
                inventory['keys'] += 1
            definitions.append(statement)
    if require_inventory:
        require(inventory == dict(schemas=2, tables=8, foreign_keys=15, triggers=7,
                                  policies=18, indexes=15, keys=18), 'Registry DDL inventory mismatch')
        require(functions == {('registry_api', n) for n in API_FUNCTIONS} |
                {('registry_private', n) for n in PRIVATE_FUNCTIONS}, 'Registry function inventory mismatch')
        for schema, name in functions:
            expected = 'postgres' if name in OWNER_FUNCTIONS else 'registry_reserver'
            require(owners.get((schema, name)) == expected, 'Unexpected function ownership')
    def definition_order(statement):
        normalized = identifiers(statement)
        if re.match(r'^(CREATE|ALTER) SCHEMA\b', normalized):
            return 0
        if re.match(r'^(CREATE|ALTER) TABLE\b', normalized):
            return 1
        if re.match(r'^CREATE(?: UNIQUE)? INDEX\b', normalized):
            return 2
        return 3

    # Unlike pg_dump's check_function_bodies=false approach, create the row types
    # before validating functions that declare registry table variables.
    definitions.sort(key=definition_order)
    return Schema(definitions, foreign_keys, security, columns, inventory)


@dataclass
class CopySection:
    table: str
    columns: tuple
    rows: list

    @property
    def sql(self):
        columns = ', '.join('"' + name + '"' for name in self.columns)
        return f'COPY "registry_private"."{self.table}" ({columns}) FROM stdin;\n' + \
            ''.join(row + '\n' for row in self.rows) + '\\.\n'


def extract_copies(source):
    """Consume COPY blocks, retaining only allowlisted registry data in memory."""
    sections = {}
    # COPY records are newline-delimited. Unicode line separators are field data.
    lines = iter(line.removesuffix('\r') for line in source.split('\n'))
    for line in lines:
        if not line.startswith('COPY '):
            continue
        match = re.fullmatch(r'COPY "(' + IDENT + r')"\."(' + IDENT +
                             r')" \(([^;]+)\) FROM stdin;', line)
        require(match is not None, 'Unsupported COPY header')
        schema, table, raw_columns = match.groups()
        keep = schema == 'registry_private'
        if keep:
            require(table in TABLES and table not in sections, 'Unexpected or duplicate registry COPY section')
        columns = tuple(re.findall(r'"(' + IDENT + r')"', raw_columns))
        require(', '.join('"' + c + '"' for c in columns) == raw_columns and
                len(set(columns)) == len(columns), 'Invalid COPY columns')
        rows = []
        for row in lines:
            if row == '\\.':
                break
            if keep:
                require(len(row.split('\t')) == len(columns), 'Malformed registry COPY row')
                rows.append(row)
        else:
            raise PreparationError('Unterminated COPY section')
        if keep:
            sections[table] = CopySection(table, columns, rows)
    require(set(sections) == set(TABLES), 'Required registry COPY section missing')
    return sections


@dataclass
class PreparedBackup:
    schema: Schema
    data: dict
    auth_ids: tuple
    hashes: dict
    roles_sql: str

    @property
    def summary(self):
        return dict(snapshot='20261111-132835', tables={t: len(self.data[t].rows) for t in TABLES},
                    objects=self.schema.inventory, api_functions=len(API_FUNCTIONS),
                    private_functions=len(PRIVATE_FUNCTIONS), auth_reference_fixtures=len(self.auth_ids),
                    source_hashes=self.hashes, execution_performed=False)


def prepare(directory):
    contents, hashes = {}, {}
    for name, expected in PINNED_HASHES.items():
        path = Path(directory) / name
        require(path.is_file() and not path.is_symlink(), 'Required backup file unavailable')
        raw = path.read_bytes()
        hashes[name] = hashlib.sha256(raw).hexdigest()
        require(hashes[name] == expected, 'Backup checksum mismatch')
        contents[name] = raw.decode('utf8')
    roles_sql = validate_roles(contents['roles.sql'])
    schema = select_schema(contents['schema.sql'])
    data = extract_copies(contents['data.sql'])
    for table in TABLES:
        expected = tuple(c for c in schema.columns[table] if table != 'certificate_codes' or c != 'code')
        require(data[table].columns == expected, 'Registry COPY column inventory mismatch')
    section = data['issuers']
    ids = tuple(str(uuid.UUID(row.split('\t')[section.columns.index('user_id')])) for row in section.rows)
    require(len(ids) > 0 and len(ids) == len(set(ids)), 'Auth reference fixtures invalid')
    require(len(data['registry_state'].rows) == 1, 'Registry singleton missing')
    return PreparedBackup(schema, data, ids, hashes, roles_sql)
