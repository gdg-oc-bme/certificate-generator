"""Recovery exercises for a second, isolated copy. Imported only during execute."""
import json
import uuid

import backup


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def check_fence(target, actor, generation, batch, error):
    prefix = f"'{actor}','{generation}'"
    operations = (
        ('get_context', ''), ('list_batches', ''), ('lookup_certificate', ",'{}'::jsonb"),
        ('reserve_batch', f",'{batch}','{{}}'::jsonb"),
        ('get_batch_status', f",'{batch}'"), ('get_reservation', f",'{batch}'"),
        ('commit_batch', f",'{batch}','{{}}'::jsonb"), ('abandon_reservation', f",'{batch}'"),
    )
    for name, arguments in operations:
        target.sql(f'SET LOCAL ROLE service_role; SELECT registry_api.{name}({prefix}{arguments});',
                   expect=error)


def assert_original_history(target, prepared):
    # Compare original rows only. The control copy deliberately adds synthetic fixtures and audit events.
    for table in ('certificates', 'certificate_codes', 'batch_items', 'audit_events', 'batches'):
        section = prepared.data[table]
        columns = section.columns
        if table == 'batches':
            columns = tuple(c for c in columns if c != 'validated_generation')
        positions = [section.columns.index(c) for c in columns]
        expected = sorted('\t'.join(row.split('\t')[i] for i in positions) for row in section.rows)
        ids = [str(uuid.UUID(row.split('\t')[section.columns.index('id')])) for row in section.rows]
        where = 'WHERE id IN (' + ','.join(literal(i) for i in ids) + ')' if ids else 'WHERE false'
        backup.require(target.copy_rows(table, columns, where) == expected, 'Recovery changed original history')


def run(target, prepared, external_generation):
    """Exercise only a clone; the fidelity database remains an unchanged baseline."""
    database = 'controls_' + uuid.UUID(target.state['run_id']).hex
    admin = type(target)(target.state, target.root, database='postgres')
    admin.identify()
    admin.sql(f'CREATE DATABASE "{database}" TEMPLATE "{target.database}";',
              'Recovery control database creation', transaction=False)
    control = type(target)(target.state, target.root, database=database)
    control.identify()
    generation = str(uuid.UUID(control.sql('SELECT generation FROM registry_private.registry_state;')))
    operator = str(uuid.UUID(control.sql("SELECT user_id FROM registry_private.issuers "
                                        "WHERE role='registry_admin' ORDER BY user_id LIMIT 1;")))
    actor, event, batch, item, approval = [str(uuid.uuid4()) for _ in range(5)]
    control.sql(f"""SELECT 1 FROM registry_private.registry_state WHERE singleton FOR UPDATE;
        INSERT INTO auth.users(id) VALUES ('{actor}');
        INSERT INTO registry_private.issuers(user_id,active) VALUES ('{actor}',true);
        INSERT INTO registry_private.events(id,event_key,internal_label,created_by)
        VALUES ('{event}','rehearsal-{event}','Synthetic recovery fixture','{actor}');""", 'Control fixtures')
    request = dict(event_id=event, formats=['pdf'], items=[dict(
        client_item_id=item, name='Synthetic Rehearsal', email='rehearsal@example.com',
        template_key='synthetic', template_sha256='00' * 32, render_profile='rehearsal-v1')])
    control.sql(f"SET LOCAL ROLE service_role; SELECT registry_api.reserve_batch('{actor}',"
                f"'{generation}','{batch}',{literal(json.dumps(request))}::jsonb);", 'Synthetic pending reservation')
    pending = control.sql(f"SELECT count(*) FROM registry_private.certificate_codes "
                          f"WHERE reserving_batch_id='{batch}' AND state='reserved';")
    backup.require(pending == '1', 'Synthetic pending reservation missing')
    check_fence(control, actor, str(uuid.uuid4()), batch, 'GENERATION_CHANGED')
    begin = f"SELECT registry_private.begin_recovery('{operator}','{generation}',"
    control.sql(begin + f"'{generation}','{approval}');", expect='FRESH_GENERATION_REQUIRED')
    control.sql(begin + f"'{external_generation}',NULL);", expect='RECOVERY_APPROVAL_REQUIRED')
    control.sql(f"SELECT registry_private.begin_recovery('{operator}','{uuid.uuid4()}',"
                f"'{external_generation}','{approval}');", expect='GENERATION_CHANGED')
    for role in ('anon', 'authenticated', 'service_role', 'registry_reserver'):
        control.sql(f"SET LOCAL ROLE {role}; " + begin + f"'{external_generation}','{approval}');",
                    expect='permission denied')
    control.sql(begin + f"'{external_generation}','{approval}');", 'Begin recovery')
    backup.require(control.sql("SELECT mode='recovery' AND generation=" + literal(external_generation) +
                               '::uuid AND NOT EXISTS(SELECT 1 FROM registry_private.issuers WHERE active) '
                               'FROM registry_private.registry_state;') == 't', 'Recovery fence or deactivation failed')
    check_fence(control, actor, generation, batch, 'GENERATION_CHANGED')
    check_fence(control, actor, external_generation, batch, 'REGISTRY_RECOVERY')
    finish = f"SELECT registry_private.finish_recovery('{operator}','{external_generation}','{approval}',"
    for complete, reviewed in (('false', 'true'), ('true', 'false'), ('NULL', 'true')):
        control.sql(finish + complete + ',' + reviewed + ');', expect='RECOVERY_APPROVAL_REQUIRED')
    control.sql(f"SELECT 1 FROM registry_private.registry_state WHERE singleton FOR UPDATE; "
                f"UPDATE registry_private.issuers SET active=true WHERE user_id='{actor}';")
    control.sql(finish + 'true,true);', expect='RECOVERY_ISSUERS_STILL_ACTIVE')
    control.sql(f"SELECT 1 FROM registry_private.registry_state WHERE singleton FOR UPDATE; "
                f"UPDATE registry_private.issuers SET active=false WHERE user_id='{actor}';")
    control.sql(finish + 'true,true);', 'Finish recovery in synthetic control copy')
    backup.require(control.sql("SELECT mode='normal' AND NOT EXISTS(SELECT 1 FROM registry_private.issuers "
                               'WHERE active) FROM registry_private.registry_state;') == 't',
                   'Recovery completion reactivated issuers')
    backup.require(control.sql(f"SELECT state='abandoned' AND end_reason='recovery_review' "
                               f"FROM registry_private.batches WHERE id='{batch}';") == 't',
                   'Pending batch not retired')
    backup.require(control.sql(f"SELECT state='retired' AND retirement_reason='recovery_review' "
                               f"FROM registry_private.certificate_codes WHERE reserving_batch_id='{batch}';") == 't',
                   'Pending code claim not permanently occupied')
    backup.require(control.sql("SELECT bool_and(validated_generation=" + literal(external_generation) +
                               "::uuid) FROM registry_private.batches WHERE state='completed';") == 't',
                   'Completed history not revalidated')
    backup.require(control.sql(f"SELECT count(*) FROM registry_private.audit_events WHERE request_id='{approval}' "
                               "AND operation IN ('recovery_started','recovery_batch_retired',"
                               "'recovery_batch_validated','recovery_approved');") == '4', 'Recovery audit incomplete')
    check_fence(control, actor, external_generation, batch, 'ISSUER_DENIED')
    control.sql(finish + 'true,true);', expect='RECOVERY_NOT_ACTIVE')
    assert_original_history(control, prepared)
    control.sql("SET plpgsql.check_asserts = on; DO $$ BEGIN "
                "IF current_setting('plpgsql.check_asserts')::boolean IS DISTINCT FROM true THEN "
                "RAISE EXCEPTION 'REHEARSAL_ASSERTIONS_DISABLED'; END IF; "
                "ASSERT NOT EXISTS(SELECT 1 FROM registry_private.certificate_codes WHERE state='reserved'); "
                "PERFORM registry_private.completed_batch_status(id) FROM registry_private.batches WHERE state='completed'; END $$;",
                'Recovery final consistency checks')
    # The restore copy must also still match every source field after control exercises.
    for table in backup.TABLES:
        section = prepared.data[table]
        backup.require(target.copy_rows(table, section.columns) == sorted(section.rows),
                       'Control exercises changed the fidelity database')
