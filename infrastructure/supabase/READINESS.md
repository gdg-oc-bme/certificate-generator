# Issue #15 operational readiness and acceptance evidence

This checklist covers centralized issuance infrastructure. The hosted Supabase
project is a separate development environment with six migrations reported applied.
`REGISTRY_ENABLED=false` remains its current required state. No production opening
or deployment authorization is implied by checked items.

Evidence below distinguishes source/test results observed during this work session
from the operator's latest rehearsal report. The documentation pass did not query
hosted Supabase or rerun database tests. Preserve protected execution logs and the
reviewed source revision separately; keep only sanitized summaries in Git.

## Implemented features and local evidence

- [x] Eight-table private registry, stable event/email/type identities, unique
  `GDG-YYYY-XXXX` claims and leading-zero preservation.
- [x] Cryptographic allocation with rejection sampling, serialized concurrent
  reservations, annual capacity enforcement and permanent retired claims.
- [x] Canonical intent/manifest hashing, identical replay, conflicting-retry denial,
  atomic commit/rollback and unchanged issued codes/original UTC timestamps.
- [x] Live issuer authorization and original-certificate ownership across lookup,
  status, replay, commit, abandonment and listing. Migration six covers demotion.
- [x] Current limits for new work, reservation replay after a lower batch limit,
  and existing-only regeneration above a newly lowered daily allowance.
- [x] Seven Edge operations, Auth-server validation, fixed privileged RPC transport,
  redacted errors, bounded requests/timeouts and authoritative completed rows.
- [x] Stale reservation review without automatic expiration, explicit abandonment,
  old-year retirement, closed-event restrictions and original-snapshot regeneration.
- [x] Private recovery controls, independent expected-generation fencing,
  external maintenance gate and inactive issuers after recovery completion.
- [x] Administrator/event/backup/deployment/recovery procedures and repeatable
  isolated rehearsal tooling with guarded cleanup.

| Recorded validation | Result | Boundary |
| --- | --- | --- |
| Complete disposable PostgreSQL runner after migration six | 91 tests passed, exit 0, plus schema/role assertions | Synthetic native PostgreSQL; includes two handler/SQL tests with substituted Auth/HTTP |
| Node offline Edge API suite | 58 tests passed, exit 0 | Network mocked; Auth mock validates synthetic HMAC tokens |
| Deno type check | Exit 0 | Type validation of entry point/imports, without proving hosted runtime behavior |
| Offline rehearsal preparation suite after Windows startup fix | 38 tests passed, exit 0 | Parser/security tests and harmless Python descendants; no PostgreSQL startup |
| Real local PostgREST probes | Both server-key transports reached expected inactive-issuer denial; public key denied and private schema excluded | Proves local routing/transport, not successful authenticated hosted issuance |
| Populated synthetic upgrade through migration six | All eight table contents preserved, exit 0 | Local forward-only upgrade and role-boundary checks |

The full database runner is a mutating disposable test, not a read-only check.
Its last successful result is recorded here rather than being rerun as part of
documentation work. Commands and test boundaries are in [README.md](README.md).

## Development environment and recovery evidence

- [x] Prior local verification observed real Auth password login and Edge runtime
  loading separately. These observations alone do not establish the complete chain.
- [x] Operator reports all six migrations applied to the separate hosted development
  database. No migration replay is required there.
- [x] Operator confirms hosted `REGISTRY_ENABLED=false` remains closed.
- [x] Operator reports successful isolated PostgreSQL recovery rehearsal
  `08ae202f-7b00-460c-8976-da95f27e5fa9`, exit 0, with `restore_fidelity=true`,
  `security_checks=true`, `recovery_control_checks=true` and `safe_to_reopen=false`.
  See the [rehearsal evidence record](rehearsal/README.md#verified-rehearsal-result).
- [ ] Attach or retain a sanitized hosted Auth → Edge → PostgREST acceptance record
  for the reviewed six-migration revision and selected JWT/server-key mode. Include
  all seven operations, unauthorized/revoked users, demotion, retries/conflicts,
  maintenance and generation mismatches. Existing supplied evidence does not
  enumerate this complete matrix, so this document does not mark it verified.
- [ ] Demonstrate hosted credential revocation, old-worker/session draining and
  blocked privileged API access during a separately authorized development drill.
- [ ] Confirm credential/artifact disposition for completed and interrupted rehearsal
  runs. The earlier interrupted run's outstanding passfile cleanup has no subsequent
  completion evidence in this session; do not silently mark it complete.
- [ ] Confirm acceptance of Issue #15's capacity-warning requirement. Current code
  reports capacity/limits and the runbook defines manual warning thresholds at
  2,000/500 available codes. Automated alerts and desktop warning presentation are
  not implemented; agree the operational coverage or record an explicit follow-up.

The rehearsal validates registry-only restoration using synthetic UUID-only Auth
references. It does not prove complete Supabase Auth recovery, recover session
credentials, establish hosted role compatibility, or validate stored artifact files.

## Production ownership and approval requirements

- [ ] GDGoC BME production organization/project ownership, region and hosting choice
  approved, with at least two trusted administrators and attributable access.
- [ ] Primary/secondary backup owners, restricted encrypted/off-site storage,
  retention, backup verification, recovery objectives and drill schedule assigned.
- [ ] Complete recovery scope agreed for Auth, role memberships, migrations,
  configuration, artifacts and acknowledged records beyond a backup snapshot.
- [ ] Independent generation/change records retained outside database restoration;
  component-key rotation, worker draining and uncertain-outcome procedures rehearsed.
- [ ] Production issuer/event allowlists, quotas and operational capacity monitoring
  reviewed. The code returns capacity, but warning/alert delivery is operational work.
- [ ] Reviewed source, target settings/grants, TLS/JWT compatibility and release
  evidence accepted. Old workers and privileged credentials cannot return after recovery.
- [ ] Explicit issuance/reopening approval recorded under the recovery runbook.
  Neither `mode=normal` nor matching generations establishes historical completeness.

Production deployment is outside Issue #15's authorization. Two-computer desktop,
rendering and CSV/journal acceptance belongs to Issue #16 and deployment readiness.

## Outstanding risks and limits

- An unannounced restore to the same generation can roll back history without being
  detected. Operators must close the gate, drain/revoke old actors and preserve a
  fresh expected generation independently before restoration.
- A consistent backup cannot prove preservation of acknowledgments outside its
  snapshot. Missing acknowledged claims/identities require reconciliation; safe
  issuance must remain blocked when completeness cannot be established.
- Three logical export files do not by themselves prove a coordinated snapshot or
  full Auth/platform recovery. The inspected backup includes sensitive Auth data,
  while rehearsal deliberately excludes it and creates no migration-history ledger.
- Hash/size attestations do not establish artifact existence, visual correctness or
  delivery. Filesystem publication and SQL commit are separate operations.
- All protected SQL operations serialize on one registry-state row. Capacity and
  throughput are bounded; live load/timeout expectations require target validation.
- The existing schema-test runner uses synthetic trust authentication and has a
  port-selection race. The recovery runner uses protected SCRAM credentials and
  bounded Windows process waits. Neither should target a shared database.
- Recovery output is spooled in the protected run directory. Its 16 MiB accepted
  output limit is checked after a helper exits; spool files can grow larger while
  it runs. Retain adequate local disk space and treat resource failures as failed
  rehearsals requiring verified cleanup. Credential overwriting cannot guarantee
  forensic erasure from snapshots or SSD storage.
- Repository automation currently includes dependency review, without a dedicated
  infrastructure test workflow. CI wiring is an improvement opportunity, not an
  added acceptance requirement for this documentation task.

## Mapping to Issue #15 acceptance criteria

| Requirement group | Implementation and evidence | Remaining completion evidence |
| --- | --- | --- |
| Secure/unique IDs, collisions, concurrency and stable identities | Migrations 1–3; reservation/schema tests | Target-level review of grants and compatibility |
| Replay/conflicts, atomic rollback, immutable issuance and authoritative rows | Migrations 2–3 and 6; commit/review/handler tests | Sanitized complete hosted API matrix above |
| Individual Auth, authorization and revocation | Auth-validating handler, live SQL checks, scoped ownership and demotion tests | Hosted revocation and end-to-end acceptance record |
| Event administration, limits, stale review, abandonment and UTC exhaustion | Locked/audited owner workflow, transactional RPCs and synthetic tests | Capacity-warning acceptance and production operator assignments/monitoring before use |
| Recovery controls and prevention of silent reuse under the documented restore procedure | Migration 5, fencing tests, successful registry-only rehearsal | Hosted draining/revocation drill and complete-history reconciliation responsibilities |
| Infrastructure/API tests and administrator/recovery documentation | Local results above; API contract, deployment/runbook and rehearsal guide | Review and retain evidence; live-state assertions are not supplied by documentation |

No missing requirement is being filled with GUI, rendering, CSV export, public
website verification or production deployment in this task. The repository supplies
the Issue #15 implementation and local evidence for review. Completion of the
unverified hosted acceptance items must be resolved explicitly before claiming
full platform acceptance. Production readiness remains a separate approval track.
