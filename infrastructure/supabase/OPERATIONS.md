# Registry administration and recovery

These procedures are for a later, separately authorized setup/deployment. They
authorize no live change. Use synthetic development data. GDGoC BME must own the
project; at least two trusted administrators need organization, deployment and
database recovery access. Do not share desktop administrator credentials.

The current hosted project is a separate development environment with six
migrations reported applied and `REGISTRY_ENABLED=false`. Keep that setting closed.
The operator-reported isolated rehearsal succeeded with `safe_to_reopen=false`.
See [READINESS.md](READINESS.md) for evidence and outstanding work,
[DEPLOYMENT.md](DEPLOYMENT.md) for configuration/deployment, and the
[rehearsal result](rehearsal/README.md#verified-rehearsal-result) for its scope.

## Development setup and deployment prerequisites

Database tests need existing PostgreSQL 16+ binaries with pgcrypto, PowerShell and
the project's Python venv. The README runner creates its own disposable loopback
cluster. Apply timestamped migrations in order to an isolated development database.
The complete chain runs locally as a non-superuser CREATEROLE migration identity;
the bootstrap superuser is used only for synthetic platform fixtures/test setup.
Owner-changing migrations temporarily grant that trusted identity SET/INHERIT
membership in registry_reserver, revoke it before commit and verify the boundary.
Hosted role/schema/extension grants need target-specific validation and recorded evidence.
Never apply tests/bootstrap.sql to a real Supabase project: Auth tables/roles are
provided by Supabase. Verify pgcrypto is in `extensions` before migration 2.

Deno/Supabase CLI are required for real Edge runtime/type/gateway validation;
Node 24 runs offline handler tests only. Install tools only with authorization.
Use the separate development project for approved integration work. Agree region,
hosting, backup retention, recovery objectives, administrators and data access for
any production target before official issuance.

Keep registry-v1 `verify_jwt=true`. Expose only `registry_api` in the Data API;
never expose registry_private. Match hosted settings to config.toml (the file does
not itself configure a hosted project). Refresh PostgREST's schema cache after
migrations and inspect effective grants, including inherited/default grants.
registry_api contains only narrow RPCs, no tables/views. Only service_role executes
them. Ordinary authenticated/anon clients cannot. The non-login registry_reserver
owns transactional routines; never grant membership in it to a client/login role.
service_role has no direct private table access, despite bypassing RLS.

Use functions/registry-v1/registry.env.example as placeholders only. Supabase
injects SUPABASE_URL; its reserved prefix cannot be provisioned as a custom secret.
Store REGISTRY_PUBLISHABLE_KEY, REGISTRY_SERVICE_KEY, REGISTRY_GENERATION and
REGISTRY_ENABLED in protected Edge configuration outside database restoration and
Git. Prefer a separate revocable component server key. Never distribute it to
desktop clients. The generation UUID is a fence, not an authentication credential;
authorized context returns the matching registry generation and supported limits.
Keep REGISTRY_ENABLED=false until initialization and readiness checks succeed.

Before issuance authorization: validate Deno types, platform JWT signing-key
compatibility, Auth-server verification, chosen API-key transport, custom-schema
RPC routing/permissions and revocation. The isolated registry-only drill has a
reported successful result; hosted revocation/draining remains a separate drill.
Complete two-computer desktop issuance/retry/recovery acceptance under Issue #16
before official use. Client
hashes do not prove artifact quality. Desktop/artifact validation remains Issue #16.

## Restricted administrative workflow

Use an individually attributable trusted database-owner session or protected
Supabase SQL console. No recovery function is exposed or granted to service_role,
registry_reserver, authenticated or anon. Both functions are SECURITY INVOKER with
empty search_path; a registry_admin JWT alone grants no execution. Database owners
remain trusted and can bypass safeguards; rogue owners are outside this guarantee.

Obtain an approval/reference UUID for every administrative operation. Keep an
external change record identifying operator, approver, database session, generation,
reason and outcome, with no participant data or credentials. Recovery audit rows
record the responsible registry_admin as actor_id and approval UUID as request_id.
The supplied operator UUID attributes an action; SQL privileges authenticate it.
Preserve database/platform administrator logs with the external record. An older
snapshot can lose audit rows, so retain pre-restore evidence separately.

Issuer, event and limit changes use reviewed parameterized SQL in one transaction:
lock registry_state's singleton FOR UPDATE first; make the approved change; insert
audit_events with actor_id, operation token, applicable event_id and request_id;
commit. Metadata supports only reason_code tokens and numeric client_version:
never put names/emails/keys/free-form request dumps in it. The external approval
record identifies issuer permission targets. This manual workflow avoids an
administration UI, extra service and public recovery override.

## Issuer administration

1. With separate authorization, invite/create individual Supabase Auth accounts
   administratively. Verify ownership and retain the Auth UUID securely. Account
   existence alone does not grant issuer authorization.
2. Bootstrap the first registry_admin row using protected SQL, initially inactive.
   Only for bootstrap, that same new row can be its audit actor. Never fabricate
   auth.users rows on a real project. Account provisioning is not implemented here.
3. Add approved members to issuers inactive by default; assign `issuer` normally.
   Separately approve `registry_admin` for cross-issuer recovery access. A business
   registry_admin is distinct from a project/database administrator.
4. During recovery keep **all** issuers inactive. After reconciliation, enable only
   a deliberately reviewed allowlist while the external gate is still closed.
   Verify roles/current roster; never blindly restore old active flags.
5. Revoke by setting active=false in the locked/audited transaction, then revoke
   Auth sessions/disable accounts as appropriate. Live SQL checks reject remaining
   JWTs. A transaction already authorized may finish before revocation obtains the
   lock: account for its outcome. Retain referenced issuer history. Protected
   Windows token storage and login UI remain Issue #16.
6. Demote by setting role='issuer' under the same lock and auditing the change.
   Batches containing certificates from other issuers become inaccessible to the
   demoted user, including pending batches. Have a current registry administrator
   review pending batches and resolve them through the normal workflow.

## Event administration

Search the catalog's event_key/internal_label, including closed events, before
creating anything. Agree one lower-case hyphenated key, e.g.
`synthetic-workshop-2026-11`, then create one stable UUID with that key, a clear
internal label and created_by issuer UUID. Unique keys prevent exact duplicates;
administrators must also catch differently named duplicates. After an uncertain
creation, look up the original UUID/key rather than inventing another event.

Displayed title (empty allowed) and date (NULL allowed) remain optional. Neither
defines event identity or issuance time. Clients select the catalog UUID and
confirm its label/key before associating participants. Each repeat event gets a
distinct UUID/key. Close via state='closed' under the administrative lock and audit:
new or mixed certificate issuance stops; existing-only regeneration remains
available to the original issuing batch owner or an active registry_admin and
retains original identity/snapshots/timestamps. Knowing an email/name or creating
a later batch association never grants cross-issuer access. Review pending batches
first. Do not delete or silently reopen.

## Capacity monitoring

Authorized context reports the current UTC year's issued/reserved/retired/available
counts and limits. All claims consume the annual 10,000 values. Defaults: 500 items
per batch, 500 new codes per issuer per UTC day, one reserved batch per issuer.
The participant limit is configurable only within 1..500 and context reports it;
both-format manifests support up to 1000 artifacts. The daily new-code allowance
is configurable within 1..10000. Administrators may lower limits under the
lock/audit workflow; review capacity before raising them within these bounds.
Warn operationally below 2,000 available and urgently below
500. Insufficient capacity rejects the whole request, including concurrent calls.

Review reservations after 24 hours; do not automatically expire them. Check the
original batch UUID, authoritative status and local journal. Same-year recovery
reuses the UUID/payload; an uncertain response never warrants new IDs. Explicit
abandonment retires new unissued claims permanently, leaving existing issued
records unchanged. A failed 100-person new batch holds 100 claims: recover it, or
abandon and consume those 100 as retired. Retrying its original request consumes
zero more. A replacement could consume another 100; review that cost first.
New old-year reservations cannot issue after UTC rollover; never edit code years.

## Backup responsibilities

Assign primary/secondary backup owners, encrypted off-site copies, retention,
restricted access and periodic restore drills. Back up the **whole** registry:
issued/reserved/retired claims, identities, batch/item snapshots, timestamps,
fingerprints and audit, plus migrations, required Auth references and role setup.
CSV exports are not complete backups. Protect deployment configuration and the
external generation/change record separately from database snapshots.

Check the target's actual backup retention, recovery coverage and PITR availability.
Database backups do not include Storage object bytes. Backups/PITR do **not**
guarantee zero loss of acknowledged records; assess the gap to the last recoverable
commit. Preserve artifacts separately when they are part of the recovery objective.
[Supabase backup documentation](https://supabase.com/docs/guides/platform/backups)

### Authorized logical-backup procedure

1. Confirm the explicitly approved project reference and backup scope. Select a
   new restricted directory outside Git. Refuse an existing destination and leave
   previous backups unchanged. Never place a database URL/password in shell history.
2. Quiesce registry writes through the external block and worker/session drain when
   a coordinated recovery point is required. Record its boundaries and unresolved
   in-flight outcomes. Separate role/schema/data commands do not establish a shared
   transaction snapshot merely because they share a timestamped directory.
3. Use the approved installed CLI and explicit remote target to export custom roles,
   registry schema and data into separate files. The relevant options are
   `db dump --role-only`, `db dump --schema registry_api,registry_private`, and
   `db dump --data-only --use-copy --schema registry_private`, each with an explicit
   target and protected output file. Review installed help before use.
   [Supabase dump reference](https://supabase.com/docs/reference/cli/supabase-db-dump)
4. Check every export's exit code and protected diagnostic output. Verify all eight
   registry definitions/data sections, function owners/grants/RLS, triggers and
   constraints. Inspect warnings, row counts and dependency coverage without printing
   raw records. Record SHA-256 checksums and the source revision in an external manifest.
5. Inventory actual export contents. The inspected `20261111-132835` data export
   includes Auth users, sessions and refresh-token records despite assumptions about
   default exclusions. Treat raw files as sensitive. The rehearsal selector discards
   all managed data and derives only UUID reference fixtures from registry issuers.
6. Preserve or separately plan recovery of required Auth account mappings, standard
   role memberships, migration history, project settings, secret configuration and
   artifacts. A standard logical export does not establish coverage of these items.
   Do not fabricate a Supabase migration-history ledger from a restored registry.
7. Encrypt and restrict retained/off-site copies under assigned ownership. Keep the
   fresh expected generation and recovery approvals outside database restoration.
   Retain evidence of acknowledged operations needed to detect history after the
   backup snapshot. A filename or CSV is not authority to invent an issued record.
8. Rehearse into a new isolated target. Current tooling pins exactly the reviewed
   `20261111-132835` files and role statements. A new backup needs intentional review
   and allowlist updates before using it with the tooling. Preserve original exports.

The successful rehearsal restores only registry data and objects with UUID-only
Auth fixtures. It does not prove complete Supabase Auth recovery or guarantee
preservation of acknowledgments outside the backup snapshot. Missing acknowledged
history keeps issuance blocked even when every SQL consistency check succeeds.

## Restoration: external block first

1. Obtain approval, open an external recovery record, preserve current backups and
   audit evidence. Tell issuers to retain journals and stop official issuance.
   Offline drafts/previews can continue.
2. Set REGISTRY_ENABLED=false outside PostgreSQL. Disable/revoke the old component
   RPC credential and stop/drain old workers, including cached configuration.
   Block direct privileged Data API access during the window. A secret update or
   HTTP timeout does **not** prove transactions stopped. Inspect database sessions
   and locks; wait for completion or administratively cancel/terminate identified
   registry transactions. Reconcile uncertain outcomes. Do not restore until old
   workers and in-flight transactions cannot write. Independently verify the block.
3. Generate a fresh UUID in trusted administration; record it externally and set
   REGISTRY_GENERATION while closed. Preserve it through restoration. Never derive
   expected generation automatically from a restored database or roll back the
   Edge configuration with the database.
4. Via trusted SQL call begin_recovery(operator_id, expected_database_generation,
   external_generation, approval_id). It compare-and-swaps the observed generation
   under the singleton lock, sets recovery mode, disables every issuer and audits.
   It waits behind earlier registry transactions: review their final outcomes.
   It cannot change external settings or drain workers; step 2 remains essential.

## Restoration and safe reactivation

1. Keep the external gate closed, old credentials revoked and workers drained.
   Perform the authorized restore. Inspect migrations/grants. Do not trust restored
   issuer permissions or a restored normal mode.
2. Immediately call begin_recovery with the restored database's observed generation
   and the **already recorded new external generation**. Restored active issuers
   are disabled. Re-entry is allowed in recovery; normal-mode rotation to the same
   UUID is rejected. After a lost response inspect state/audit while still closed.
3. Reconcile every historical issued, retired and exposed reserved claim from
   authoritative recoverable history, plus identities, completed/pending batches,
   original snapshots/timestamps, idempotency results and journals from all PCs.
   Journals/emailed CSVs are evidence to investigate, not authorization to invent
   records. Filenames prove no identity or issuance. Missing claims require a
   separately reviewed restoration preserving original values and audit; no
   generic import/rewrite API is provided. Do not disable constraints to pass.
4. Review pending operations and explicitly approve permanent abandonment during
   recovery. Normal same-year recovery without rotation reuses reservations; a
   restore invalidates old-generation pending operations. Never replay them into
   the new generation or silently issue replacements.
5. If any acknowledged record/exposed claim is unaccounted for, **remain blocked**,
   even if schema constraints pass or a new year starts. Missing IDs are not unused
   capacity. Never invent issuance timestamps. If completeness cannot be established
   after unrecoverable loss, safe new issuance cannot be guaranteed and must not
   resume under this registry. Escalate; there is no override/reset-to-empty path.
6. After reconciliation and documented approval call finish_recovery(operator_id,
   expected_generation, approval_id, historical_complete,
   permissions_and_pending_reviewed), both attestations true. It requires all issuers
   inactive, permanently retires reviewed pending claims, validates completed
   mappings/artifact attestations and revalidates export generation in one atomic
   transaction. Issued IDs, timestamps, snapshots, fingerprints and original batch
   generation remain unchanged. Any failure rolls back and leaves recovery mode.
   The booleans are accountable human attestations, not proof of missing history.
7. Database mode is now normal, but issuers remain inactive and the external gate
   closed. Deliberately enable the reviewed allowlist. Provision a fresh component
   key, verify old workers/credentials cannot return, start fresh workers with the
   aligned expected generation. Reopen only with lead/two-administrator approval
   and a completed drill. Development tests should not consume official capacity.
   Historical completed batches can now be read/exported, never silently reissued;
   explicit new regeneration batches retain original identity/time.

Initial empty-registry initialization follows the same controls with explicit
confirmation that the namespace is genuinely unused. finish_recovery is not a
replayable operation: after a lost response inspect mode/audit rather than starting
another rotation automatically. Recovery functions do not set external secrets.

## Guarantee boundaries

- Automatic: every protected RPC checks the Edge-configured expected generation
  under the singleton lock, normal mode and live issuer authorization. Missing/
  invalid configuration fails closed. A mismatch blocks all seven API operations,
  including sensitive reads. Sanitized 503 codes: RECOVERY_REQUIRED,
  REGISTRY_RECOVERY_MODE, REGISTRY_MAINTENANCE. A stale client header yields 409
  GENERATION_CHANGED; it cannot override the server's expected value.
- Administrator: enforce/drain the external block, preserve the fresh generation,
  reconcile complete history, review permissions and approve reactivation. Private
  SQL functions enforce transitions and structural consistency, not backup truth.
- Cannot guarantee: detection of an unannounced restore to the **same** generation,
  reconstruction of lost authoritative records, or zero loss from periodic backups.
  A database cannot detect all rollback of its own state. If the external procedure
  was skipped, block immediately and reconcile. Matching generations alone never
  prove historical completeness. No extra permanent service is introduced.

Official guidance reviewed: [function secrets](https://supabase.com/docs/guides/functions/secrets),
[JWT checks](https://supabase.com/docs/guides/functions/auth),
[custom API schemas](https://supabase.com/docs/guides/api/using-custom-schemas).
Hosted credential revocation, worker draining, gateway and restoration behavior
still require a separately authorized development drill. The isolated PostgreSQL
rehearsal result does not establish those hosted behaviors.
