# Isolated registry recovery rehearsal

Preparation and inspection are available now. Running `execute` requires separate
explicit authorization. SQL consistency and a successful rehearsal do not establish
that all acknowledged history was backed up or authorize reopening issuance.

The hosted Supabase project remains a separate development environment with
`REGISTRY_ENABLED=false`. The reported successful local run below changes no
hosted setting. See [operational readiness](../READINESS.md) for the remaining gates.

## Verified rehearsal result

The operator supplied the following successful isolated PostgreSQL recovery result
for this documentation update. It was not rerun or independently re-executed during
the documentation pass.

```json
{
  "run_id": "08ae202f-7b00-460c-8976-da95f27e5fa9",
  "restore_fidelity": true,
  "security_checks": true,
  "recovery_control_checks": true,
  "safe_to_reopen": false
}
```

The reported process exit code was `0`. Retain the original sanitized result and
its source revision in the external rehearsal record. `execute` produces a success
result only after its cleanup `finally` returns, but this report does not independently
inspect the run directory or authorize any residual-file deletion.

| Result | Implemented validation represented by success |
| --- | --- |
| `restore_fidelity` | Selected COPY fields round-trip unchanged across all eight tables, with original code inputs, timestamps, fingerprints, artifacts and audit rows |
| `security_checks` | Restored owners, grants, role boundaries, RLS, keys, validated foreign keys and enabled history protection |
| `recovery_control_checks` | Separate control database exercises fencing, deactivation, approvals, pending retirement and unchanged original completed history |
| `safe_to_reopen=false` | No decision about complete acknowledged history, hosted runtime readiness or issuance approval is made |

The source snapshot has no pending reservations. Pending-retirement coverage comes
from an added synthetic reservation in the separate control copy. No original
record is inferred from that fixture. Supabase Auth credentials/sessions are
excluded, with UUID-only `auth.users` references used instead. This registry-only
backup rehearsal does not prove complete Supabase Auth recovery or guarantee
preservation of acknowledgments outside the backup snapshot.

This tooling supports only the reviewed `20261111-132835` snapshot. All three
source hashes are pinned in `backup.py`. A changed backup requires a fresh review
and an intentional allowlist update. Original exports are read-only inputs.
The role export must also match nine explicitly allowlisted statements in order.
Missing, duplicate, reordered, or additional statements fail preparation. Backslash
commands are rejected everywhere in that file, including inside comments. The
restore uses canonical validated statements rather than the raw role-file text.

## Preparation commands

Run from the repository root in PowerShell. These commands create no cluster,
connect to no database, and invoke no PostgreSQL executables.

```powershell
& .\.venv\Scripts\python.exe -B infrastructure\supabase\rehearsal\test_preparation.py
& .\.venv\Scripts\python.exe -B infrastructure\supabase\rehearsal\rehearsal.py inspect
```

Inspection checks the three backup hashes, selects the registry object inventory,
checks COPY column lists, and verifies dependency file availability. It reports
counts and hashes only. It does not verify the server version by running binaries.
The default backup path is
`%LOCALAPPDATA%\GDGoC\RegistryHostedDev\backups\20261111-132835`.
Use `--postgres-bin` if the existing PostgreSQL 17 installation is elsewhere.

## Future execution, only after explicit approval

```powershell
& .\.venv\Scripts\python.exe -B infrastructure\supabase\rehearsal\rehearsal.py execute --approve-local-restore
```

The switch records the operator's deliberate choice. It is not an authorization
service or proof that approval has been granted in a conversation.

Execution performs these steps without contacting Supabase or using Docker.

1. Verify source hashes and dependency files again. Create a unique run directory
   under `%LOCALAPPDATA%\GDGoC\RegistryRecoveryRehearsals\<run-uuid>`. Reject links,
   junctions, repository destinations, and overlap with the backup directory.
   Restrict inherited permissions with `icacls` to the current user and SYSTEM
   before writing database files. All run artifacts remain outside Git.
2. Verify all four PostgreSQL executables report version 17. Initialize an entirely
   new cluster and choose a free loopback port. Bind only to `127.0.0.1`. A port
   collision fails the run. The script does not retry or attach to another cluster.
   Require SCRAM authentication using a fresh per-run local password. Password and
   passfile inputs live only in the restricted run directory. No hosted credential
   is used or copied, and no password enters command arguments or reports.
   Overwrite, flush, and remove the initialization-password file immediately after
   initdb returns or fails. Remove the connection passfile after a failure before
   any server startup attempt, or after positively verified shutdown.
3. Record a nonsecret identity manifest containing the data directory, port,
   system identifier, PID/start time, source hashes, and fresh external rehearsal
   generation. Verify the connected server's identity before creating a unique
   database from `template0`. Startup uses `pg_ctl -w -t 30` with a 45-second
   outer process timeout. All three standard streams use `DEVNULL`; PostgreSQL
   logs go only to the protected `-l` destination. After pg_ctl succeeds, validate
   and persist the directory-specific postmaster PID/start time immediately,
   before the first SQL identity probe. Failed or interrupted startup never adopts
   an observed PID or guesses a shutdown target.
4. Create standard Supabase contract roles as non-login stand-ins, including
   `service_role` with BYPASSRLS and `authenticator` with NOINHERIT. Apply the
   reviewed role export and restore ownership to `postgres` and the restricted
   `registry_reserver`. Create `auth.users(id uuid PRIMARY KEY)` with only UUIDs
   referenced by restored issuers. No credential or Auth session is imported.
5. Install the existing bundled `pgcrypto` into `extensions`, granting its
   required usage to the non-login function owner. Exclude unrelated extension
   declarations, the public auto-RLS function, and managed-schema objects.
6. In one error-stopping transaction, create schemas and tables before functions
   that declare registry row types, then restore only the eight registry
   COPY sections, the original foreign keys, and the remaining security objects.
   Primary/unique/check constraints are retained. Foreign keys are added after all
   rows exist, with normal validation. The `batches` self-reference therefore
   requires no trigger disabling or constraint weakening. The dump's `replica`
   setting and all managed data are discarded. Function bodies remain intact.
7. Compare every restored COPY field against the export, including exact original
   timestamps, bytea values, fingerprints, snapshots, and audit rows. Validate the
   generated codes, all fifteen foreign keys, RLS, keys, function owners and
   privileges, eighteen policies, and seven enabled history triggers. Exercise
   history-protection rejection paths. Unexpected success fails the rehearsal.
   Explicitly set `plpgsql.check_asserts=on` in the checking connection. A separate
   `IF/RAISE` guard in the checks rejects disabled assertions independently of
   PostgreSQL's `ASSERT` handling. The final recovery assertion block has the same
   enable-and-verify safeguard.
8. Clone the fidelity database inside the same disposable cluster. Add one synthetic
   pending reservation in the control copy. Test generation mismatch across all
   seven operations and the reservation alias, restricted recovery execution,
   invalid approvals, issuer deactivation, blocked recovery-mode operations,
   completion prerequisites, permanent pending-claim retirement, recovery audit
   events, and preservation of every original historical field. Only the completed
   batch validation-generation marker may change in original batch rows. Both
   reconciliation flags are rehearsal attestations about this synthetic copy.
9. Check source hashes and the unchanged fidelity database again. Stop only the
   identified cluster in `finally`. Retain protected files and never automatically
   delete clusters, databases, or source exports. Remove the connection passfile
   only after verified shutdown. A result always includes
   `safe_to_reopen=false`.

SQL input and captured subprocess output use temporary regular files inside the
protected run directory. They are closed and removed after each helper invocation.
Startup uses no capture files. The output reader accepts at most 16 MiB per
stream and rejects larger output after the helper exits. Temporary spool files can
grow beyond that limit while the helper runs. Captured output never reaches the
console. Generated local password files remain
inside the restricted run directory only for their required
lifetime. Removal verifies a regular, single-link file, overwrites its bytes,
flushes to storage, and unlinks it. Filesystem snapshots, backups, and SSD wear
levelling can retain old blocks; this does not guarantee forensic media erasure.
Subprocess errors are reduced to fixed classifications. PostgreSQL statement
logging and ordinary error logging are suppressed for this short-lived target.
Never enable psql echo or publish the protected cluster directory.

## Identified-cluster cleanup

Normal completion and ordinary failures attempt one verified shutdown. For a run
left running after an interruption, take the UUID from its protected run directory
and use the following command only with cleanup authorization.

```powershell
$rehearsalRunId = 'REPLACE-WITH-THE-REHEARSAL-RUN-UUID'
& .\.venv\Scripts\python.exe -B infrastructure\supabase\rehearsal\rehearsal.py stop --run-id $rehearsalRunId --approve-stop
```

Cleanup requires the exact run-directory layout, data directory, PostgreSQL system
identifier, loopback port, PID, and postmaster start time. It refuses a restarted
or mismatched server. It uses one `pg_ctl -m fast -w stop` against the identified
cluster, never process-name termination. It retains cluster files and removes only
the generated connection credential after verified shutdown. A nonsecret marker
records that verification, permitting an authorized cleanup to finish credential
removal if a previous removal failed.

An ambiguous startup, missing PID file without recorded shutdown verification,
identity mismatch, or failed/ambiguous stop retains the connection credential in
the protected directory for authorized manual recovery. It receives no guessed PID,
weaker identity check, or automatic stop retry. Even a missing PID file alone is
insufficient evidence that an interrupted startup never launched PostgreSQL.

## Windows subprocess timeout handling

Python 3.12 on Windows calls `communicate()` again without a timeout after killing
a timed-out child in `subprocess.run()`. A descendant holding captured output pipe
handles can keep that call waiting after the direct child has exited. A harmless
Python-only descendant simulation reproduced this mechanism on the local machine.
It is the most likely explanation for the observed pg_ctl hang; no PostgreSQL
startup was executed to reproduce that specific incident during this fix.

The shared helper now uses `Popen.wait(timeout=...)`, no `PIPE` streams and no
`communicate()` calls. Timeout or interruption terminates only the helper it just
launched, followed by one additional wait bounded to five seconds. It does not
terminate PostgreSQL descendants or use process-name/PID guessing. Startup output
is discarded, so inherited handles cannot hold a capture reader open. The outer
startup wait plus helper-reaping wait is bounded to 50 seconds. OS process creation
and filesystem stalls themselves cannot be given an absolute wall-clock guarantee.

## Interrupted run 4e43866f-fddc-4d1d-a8d9-0be9939d31dc

Read-only inspection found a matching run ID/data-directory marker and port 59740.
There was no postmaster PID file and no initialization-password file. The protected
connection passfile remains. The manifest has neither a recorded PID/start time nor
verified-shutdown evidence. The operator reported an independently verified manual
shutdown. No cleanup was executed as part of the startup fix.

For a later explicitly approved cleanup:

1. Confirm the original Python rehearsal controller has ended. Reconfirm the exact
   run directory, saved system identifier, and independent directory-specific
   graceful-shutdown record. Inspect local process/listener metadata without
   disclosing credentials. A reused PID or port must not be treated as this run.
2. With cleanup authorization, use a bounded, directory-specific `pg_ctl status`
   and read-only control-file inspection to corroborate the stopped cluster and
   system identifier. Capture/redact tool output. If evidence is incomplete or
   contradictory, retain credentials and investigate. Do not start the cluster.
3. After positive manual verification, use the existing `safe_path`,
   `validate_marker`, and `erase_credential` helpers in an authorized local session
   to remove only `pgpass.conf` under this exact run directory. Retain nonsecret
   state and cluster files as evidence. Do not fabricate `pid`, `start_time`, or
   `shutdown_verified` merely to make the automated `stop` command accept the run.

The normal `stop --approve-stop` path intentionally continues to refuse this
incomplete manifest. Credential erasure and any later cluster-file disposal
require explicit authorization and were not performed during preparation.

## Scope and unverified behavior

The implementation has recorded offline parser/safety tests, harmless Python
subprocess timeout simulations and backup inspection, followed by the operator's
successful isolated execution reported above. The result covers that selected
registry snapshot and local target. It does not establish future backup coverage,
the state of another run, or hosted compatibility. Offline test passes alone still
do not establish restore success.

The `postgres` role is a trusted superuser in this disposable native cluster.
Runtime API and helper ownership/permissions remain restricted. This does not model
hosted administrator restrictions or prove platform compatibility. Auth fixtures
model referential integrity only. No Auth login, Edge runtime, PostgREST, credential
revocation, worker draining, backup completeness, or artifact existence is tested.
No Supabase migration-history ledger is created. No setting enables hosted issuance.
