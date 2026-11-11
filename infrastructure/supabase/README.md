# Centralized certificate issuance registry — Issue #15

The current hosted project is a separate development environment. The operator
reports all six migrations applied and `REGISTRY_ENABLED=false`. The isolated
registry-only recovery rehearsal succeeded with `safe_to_reopen=false`; its result
and evidence boundaries are recorded in [rehearsal/README.md](rehearsal/README.md).
This documentation update makes no live project check or deployment change.

Use [DEPLOYMENT.md](DEPLOYMENT.md) for deployment and environment configuration,
[OPERATIONS.md](OPERATIONS.md) for administration, backups and recovery, and
[READINESS.md](READINESS.md) for the Issue #15 acceptance and readiness checklist.

## Architecture

| Component | Responsibility |
| --- | --- |
| Individual Supabase Auth account | Supplies a signed-in user's access JWT; Auth validates it through `/auth/v1/user` |
| `functions/registry-v1/index.ts` | Reads trusted runtime configuration once when the worker constructs its handler |
| `functions/registry-v1/handler.ts` | Validates requests and verified identity; invokes fixed, execute-restricted RPCs |
| `registry_api` | Seven business operations plus the compatible `get_reservation` status alias; eight SQL entry points |
| `registry_private` | Eight tables, restricted helper functions, RLS, constraints, history protection and private recovery controls |
| `rehearsal/` | Selects a pinned backup, restores an isolated PostgreSQL 17 target and tests recovery in a separate local copy |

Requests carry a user JWT to the Edge Function. The handler verifies the user with
Auth and uses that derived actor UUID and trusted external generation in its fixed
PostgREST RPC calls, using a server-only key. PostgreSQL locks the registry singleton before
checking generation, mode, live issuer privileges, ownership and transaction rules.
Rendering and network/file operations occur outside these SQL transactions.

| Migration | Implemented responsibility |
| --- | --- |
| `20261109000100_registry_foundation.sql` | Eight tables, identities, code constraints, RLS and immutable history |
| `20261109000200_reservations.sql` | Secure allocation, quotas, canonical intent hashing, reservation lookup and abandonment |
| `20261109000300_issuance_commits.sql` | Atomic manifest commit, authoritative issuance rows and completed-history protection |
| `20261109000400_edge_read_operations.sql` | Context/catalog, scoped certificate lookup and bounded batch listing |
| `20261109000500_recovery_controls.sql` | Private generation rotation, issuer deactivation, reconciliation and pending retirement |
| `20261109000600_batch_access_and_limits.sql` | Current certificate ownership after administrator demotion, replay after lowered batch limits and zero-allocation regeneration |

Preserve all six applied migration files. Apply future corrections through a new
forward-only migration. The foundation assumes
Supabase's `auth.users` table and `anon`, `authenticated`, `service_role` roles.
It needs PostgreSQL 16+ and the bundled pgcrypto extension. The second migration
enables pgcrypto in `extensions`; no packages/tools are installed. If pgcrypto
already exists in another schema, initialization fails rather than using an
unexpected random source. Resolve its placement administratively before applying.
Do not apply it to a shared or production project as part of local testing.

## Tables

All eight tables live in `registry_private`, which must stay outside the Data
API exposed schemas:

| Table | Responsibility |
| --- | --- |
| registry_state | Singleton generation, maintenance information, recovery mode (initial default), global transaction lock |
| issuers | Auth UUID, explicit role, inactive by default |
| events | Stable internal UUID/key, catalog label, optional displayed title/date |
| certificates | Stable event/normalized-email/type identity; initially participation only |
| batches | Caller-persisted operation UUID, fingerprints, formats, generation and lifecycle timestamps |
| certificate_codes | Permanent numeric suffix claims and derived public code |
| batch_items | Source record/item mapping, rendering/delivery snapshot and bounded JPG/PDF hash/size pairs |
| audit_events | Append-only actor/entity history with tightly limited metadata |

## Foundation invariants (first migration)

- Unique event/email/type identities, year/suffix claims and generated public
  codes. Suffix `0` becomes `0000`. Only 0..9999 are accepted per four-digit year.
- One reserved or issued code per identity; retired claims remain occupied.
- Composite foreign keys keep items, identities, codes and batches in the same
  event and prevent pairing a code with the wrong certificate.
- At most one originating `new` item per code; unique participant and ordinal
  within each batch. Restrictive foreign keys retain referenced history.
- State vocabulary, state/timestamp shape, SHA-256 lengths, positive artifact
  sizes and complete hash/size pairs. Issued timestamps must match the UTC code
  year. Twenty-four hours is a review timestamp, not an automatic expiry.
- Code claim identity cannot change, even while reserved. Claims cannot be
  deleted/truncated. Issued and retired rows cannot be updated. Certificate
  identities and audit history cannot be updated/deleted; audit cannot truncate.
- Audit metadata admits only a reason-code token and numeric client version;
  names, emails, request bodies and credentials do not belong in audit data.

## Foundation security boundary

RLS is enabled on every table with no client policies. Schema, table, sequence
and function privileges are explicitly revoked from PUBLIC, anon, authenticated
and service_role. No API schema, exposed functions or permissive policies are
provided. Trigger functions are invoker-security with an empty search path.

Later server routines need explicitly reviewed grants and actor/role checks.
Supabase service_role bypasses RLS; RLS alone does not restrict it. Owners and
superusers remain trusted administrators and can disable safeguards. Future
migrations must revoke EXECUTE explicitly on each new function: schema-scoped
default privileges cannot cancel PostgreSQL's global PUBLIC EXECUTE default.
Likewise, recheck effective grants after every migration, including Supabase
project defaults. The test bootstrap models roles, not the complete platform.

Migrations 2..6 temporarily grant their trusted migration identity SET/INHERIT
membership in registry_reserver for ownership transfers, replacement and grants,
then revoke that membership before commit. PostgreSQL 16+ retains a separate,
bootstrap-granted ADMIN membership with SET/INHERIT disabled; it is not a runtime
assumption path. No membership is granted to API roles or authenticator. The
migration identity needs CREATEROLE, database/schema creation and REFERENCES on
auth.users, as well as authority to manage its own objects. A hardened pgcrypto
installation must explicitly permit registry_reserver to execute gen_random_bytes.
See [Supabase ownership guidance](https://supabase.com/kb/troubleshooting/cant-revoke-a-role-membership-granted-by-supabase_admin-d08922).

## Migration 1 boundary

Migration 1 alone supplies the foundation. Migrations 2 through 6 implement the
allocator, live authorization and generation checks, quotas, replay, immutable
snapshots, atomic commit, year-boundary retirement and authoritative results
described below. Client artifact hashes remain attestations and do not prove
rendering quality or continuing file availability.

Recovery mode is initially closed, but a column alone cannot stop a database
owner or implement restore fencing. External maintenance/generation checks and
backup reconciliation are described in Subtask 4 below. Never resume allocation after
an old restore until historical claims and identities are reconciled.

## Local schema tests (Windows)

Use the project Python 3.12+ virtual environment, Node 24 for the offline handler
tests, Deno for type checking, and existing PostgreSQL 17 binaries with pgcrypto
for native database tests/rehearsals. The rehearsal tools use Python's standard
library. Docker and Supabase CLI are needed for local platform testing, while the
native disposable runner does not require a running Supabase stack.

Keep synthetic fixtures separate from participant inputs and real credentials.
For separately authorized local platform work, use the `infrastructure` workdir
and its `supabase/config.toml`, a dedicated project/network, and localhost-only
published ports. Inventory existing services before starting anything. Protect any
CLI output that contains local keys. Full Edge testing also needs the HTTPS/issuer
alignment described in [DEPLOYMENT.md](DEPLOYMENT.md).

With existing PostgreSQL binaries, from the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File infrastructure/supabase/tests/run-schema-tests.ps1
```

`-ExecutionPolicy Bypass` applies only to this process; no machine policy changes.
Override `-PostgresBin` if necessary. No database URL or real credentials are
used. The runner creates a new cluster under TEMP, selects a free loopback port,
creates synthetic Supabase contract fixtures, applies migrations, executes SQL
assertions with ON_ERROR_STOP, and stops the cluster in finally. It installs
nothing and never connects to an existing server. TEMP clusters/logs are retained
for inspection. The free-port selection can race; startup then fails safely.
These tests use local trust authentication only on that disposable cluster;
never use the bootstrap or trust configuration in production.

The cluster superuser only bootstraps platform fixtures and synthetic test data.
All six migrations execute from an empty registry as registry_migration, a LOGIN
CREATEROLE role with neither SUPERUSER nor BYPASSRLS. Tests check effective grants,
non-assumable owner membership after each migration, and recovery helper grants.
This models relevant PostgreSQL 16+ permissions, not hosted Supabase compatibility.

Tests cover actual inserts/updates, uniqueness, relationships, lifecycle checks,
terminal history, RLS and effective grants. They do not replace platform tests
against development Supabase. Foundation assertion fixtures roll back; integration
fixture writes remain confined to the disposable cluster.

## Transactional reservations (second migration)

Three execute-only operations in `registry_api` are available to service_role:

- `reserve_batch(actor uuid, generation uuid, batch uuid, request jsonb)`
- `get_reservation(actor uuid, generation uuid, batch uuid)`
- `abandon_reservation(actor uuid, generation uuid, batch uuid)`

The Edge Function validates the real user JWT through Auth, derives the actor from
the verified user, and supplies the externally expected registry generation.
A service_role caller can assert an actor: this is a trusted
server boundary, not user authentication. No operation is granted to PUBLIC,
anon or authenticated. service_role still has no private schema/table access.
Keep private schemas off the Data API. `config.toml` exposes only registry_api
locally; a hosted project needs its matching Data API setting configured separately.

The function owner `registry_reserver` has NOLOGIN, no caller memberships, no
schema CREATE privileges, and explicit table grants/RLS policies limited to its
work. Row-locking columns need UPDATE privilege, but no RPC exposes configuration,
issuer or event changes. SECURITY DEFINER entry points pin search_path to empty;
private helpers are not callable by clients or service_role. No dynamic SQL is
used by reservation routines. Existing ordinary-client deny boundaries remain.

Each operation first locks the singleton registry row and checks current normal
mode/generation and live issuer authorization. The caller transaction retains
this lock until commit/rollback: invoke one RPC per short transaction, never
keep a transaction open during rendering/network/filesystem work. The statement
is all-or-nothing; errors roll back every identity, claim, item and audit write.
Deployments should configure bounded statement/lock timeouts on server calls.

Request version 1 has exactly event_id, formats (jpg/pdf/both as array), and items.
Each item includes client_item_id UUID, name, email, template_key,
template_sha256 (64 hex digits), render_profile; optional source_row (>=2),
display_title and ISO display_date. Type is participation only. Items retain
input order. Formats are sorted; missing optional values and typed UUID/date/
integer fields are normalized before server SHA-256 hashing of canonical jsonb.
Delivery email is trimmed; identity uses its lowercase form. ASCII mailbox
validation rejects controls, malformed domains and formula-leading addresses.
Unknown keys, duplicate identities and duplicate client item IDs are rejected.
The canonicalization version is persisted; changing it requires an explicit
future compatibility migration. No client-supplied fingerprint is trusted.

An identical operation UUID/intent returns the stored mappings (including a
terminal abandoned result) even after the configured batch limit decreases;
the limit still applies to new batches. Changed intent raises IDEMPOTENCY_CONFLICT. Never
create a new UUID to resolve a timeout. One reserved batch per issuer is enforced
by a unique index. Competing identities raise CERTIFICATE_BUSY. Existing issued
identities reuse their original new-item rendering/delivery snapshot and timestamp;
a different participant name raises CONTENT_CONFLICT, not a second certificate.

Allocation computes the unclaimed 0..9999 array for the current UTC year, excluding
every reserved, issued and retired claim. For m remaining values, two secure bytes
from pgcrypto select an index using rejection below floor(65536/m)*m, then modulo m.
This removes modulo bias. Each draw allows at most 32 attempts; entropy failure
aborts the request. The chosen value is removed from the available array. Unique
constraints remain final backstops: any unexpected constraint collision aborts
the entire call; retry the same UUID, never reuse a claim. No sequential/random()
allocation, clock overrides, issuance transition, HTTP call or file operation exists.

Registry-state columns max_batch_items and daily_new_code_limit default to 500
and are administrator-controlled. max_batch_items must be 1..500, matching the
Edge ceiling; daily_new_code_limit must be 1..10000. Both requested formats fit
the maximum 1000-artifact manifest. Context reports the effective configured
participant limit, never a value above 500. Daily usage counts all new
claims reserved by the actor in the current UTC day, including retired claims;
retries and existing certificates consume no extra allowance. An existing-only
regeneration is allowed even if current usage exceeds a lowered daily allowance.
Capacity results count
reserved/issued/retired and available values for the current UTC year. These counts
are snapshots, not promises to other operations. The future UI can warn below
2000/500 remaining; insufficient capacity raises YEAR_EXHAUSTED atomically.

After 24 hours, lookup returns stale=true, without automatic expiration. Same-year
identical retries validate authorization, generation and request again and keep
their IDs. Old-year reserved batches remain discoverable (year_valid=false), but
reservation replay raises YEAR_ROLLOVER. Explicit owner/admin abandonment retires
all owned unissued claims, logs once, and is idempotent. It frees the issuer's open
batch slot, never annual code capacity. Old-generation abandonment is denied;
administrator restore reconciliation remains separate. Existing issued items are
never retired by abandoning another rendering batch.

Lookups require normal mode and owner/admin authorization. The third migration
extends the existing lookup with authoritative completed-batch rows and adds the
commit routine described below. End-to-end official issuance still requires
Edge JWT verification and the authenticated desktop integration. External restore
fencing is implemented in Subtask 4; an administration UI and CSV writing remain out of scope.

The runner checks the original foundation immediately after migration 1, applies
the remaining five migrations, and runs Python unittest tests using stdlib
subprocess and the existing venv (no driver install). Tests use real psql sessions, including
concurrent callers, exhaustion/0000, daily limits, injected mid-insert failure,
staleness, permissions, idempotency and issued-record reuse. Time-boundary and
issued fixtures are created by the isolated test owner, not production RPCs.

## Transactional issuance (third migration)

`registry_api.commit_batch(actor uuid, generation uuid, batch uuid, manifest jsonb)`
uses the same singleton mutation lock and live authorization as reservations.
Owner or active registry_admin may complete a reserved batch; administrators are
audited as the actual actor without replacing the original creator. Mode and
generation are rechecked even for retries. No caller-supplied actor is trustworthy
unless the Edge Function derives it from a verified JWT. service_role has
execute-only access; ordinary desktop/anon/authenticated roles have none.

Manifest version 1 is an object with exactly `version` (integer 1), `intent_hash`
(the reservation SHA-256 hex), and `artifacts` (array). Each artifact has exactly:

| Field | Required value |
| --- | --- |
| item_id | Stored batch-item UUID |
| certificate_id | That item's certificate UUID |
| code_id | That item's allocated code UUID |
| format | Requested `jpg` or `pdf` |
| sha256 | Exactly 64 hexadecimal characters (case normalized) |
| byte_count | Positive JSON integer within signed bigint range |
| filename | Exactly `<allocated public code>.<format>`, no path |

The item/format pairs must cover the entire batch exactly once. Unknown keys,
duplicate entries, extra/missing formats, incorrect associations and client time
fields are rejected. PostgreSQL inspects no files, images, PDFs or uploads; hash
and size are **authenticated client attestations**, not proof of visual accuracy,
file existence, or continuing availability. Issue #16 must validate actual files.

Canonicalization sorts artifacts by stored item ordinal and format, types UUIDs
and sizes, and lowercases checksums. SHA-256 includes fingerprint version 1 and
the original intent hash. `commit_fingerprint_version` persists that contract;
future canonicalization changes require explicit compatibility work. Artifact
array/key order and checksum case do not change the fingerprint. Altered content
after completion raises COMMIT_CONFLICT. Invalid attempts leave commit_hash null
and do not prevent later valid completion. Concurrent identical calls serialize
and return the same original issuance rows/time, with no new IDs or audit writes.

Validation checks code ownership, full origin mappings, normalized recipient
identity, and original issued snapshots for regeneration. Snapshot fields cannot
be changed/deleted after reservation; only artifact columns can be updated while
reserved. Completed batch results/artifacts are immutable. Migration 5's private
recovery procedure revalidates only validated_generation; no recovery RPC is
provided. Pending administrative review timestamps remain adjustable.

The transaction records all attestations and transitions all owned reserved codes
with one PostgreSQL clock_timestamp(), then completes the batch and audits once.
That timestamp is the logical issuance transition inside a successful transaction,
not transaction start, client time, response arrival, or exact physical WAL commit
time. Any ordinary exception rolls back the whole operation, including metadata,
claims and audit. Existing issued records are never updated: existing-only and
mixed batches retain original public codes, issued_at and rendering/delivery data.

If new claims have an old UTC year, the operation retires every unissued claim
owned by the batch, marks it expired, audits YEAR_ROLLOVER and returns a JSON domain
error (`error: YEAR_ROLLOVER`) with no issuance_rows. It deliberately does not
throw: throwing would roll back retirement. The caller must commit this successful
SQL result and handle its domain error; it must not silently create a new batch.
An exceptionally narrow rollover during attestation writes raises
YEAR_ROLLOVER_RETRY and rolls back; retry the same operation to retire safely.
Completed retries are handled before year checks, so historical issuance is not
invalidated by a new year. Abandoned/expired batches cannot commit.

`registry_api.get_batch_status(actor,generation,batch)` and the existing
`get_reservation` return the snapshot plus confirmed_at, commit_hash, fingerprint
version, recovery_validation_required, and issuance_rows. Only a completed batch
validated against the current generation returns rows; other states return `[]`.
Each row has exactly `code`, `email`, `issued_at`, ordered by original batch ordinal.
Email comes from the originating successful snapshot and timestamps are serialized
as `YYYY-MM-DDTHH:MM:SS.ffffffZ` without JavaScript precision loss. Mixed/regenerated
batches include original timestamps. No CSV or local-file inference is involved.
Capacity/staleness are current status metadata, not immutable historical results.

No data is public: helper functions are private invoker-security functions, API
functions are SECURITY DEFINER with an empty search_path, qualified references,
no dynamic SQL and execute grants only to service_role. Artifact UPDATE privilege
is limited to the four metadata columns for the non-login routine owner. Database
owners/superusers are trusted and can bypass/disable triggers; no public interface
provides direct DML. The Edge handler redacts database error context and
maps domain outcomes, bounds request sizes/timeouts, verifies JWTs and enforces external
restore fencing. Local/remote filesystem publication is not a database transaction.

The runner preserves all foundation/reservation tests and adds `test_commits.py`
for real single/multi/mixed commits, manifest rejection, replay, authorization,
concurrent commits, UTC precision/year checks, export order and immutable history.

## Authenticated Edge API (Subtask 3)

`functions/registry-v1/index.ts` is the sole Deno entry point; `handler.ts` contains
the Web-standard router, request validation, Auth verification and fixed RPC
transport. `config.toml` keeps platform `verify_jwt=true` and exposes only the
existing execute-restricted `registry_api` schema. No registry tables or private
helpers become exposed. Migration 4 adds only get_context, lookup_certificate and
list_batches, owned by the existing non-login registry_reserver role; only
service_role receives EXECUTE. No additional RPC bridge or broad grants are needed.

### Authentication and configuration

Official references reviewed:

- [Edge authentication](https://supabase.com/docs/guides/functions/auth)
- [Authorization headers](https://supabase.com/docs/guides/functions/auth-headers)
- [Server-validated user](https://supabase.com/docs/reference/javascript/auth-getuser)
- [Database functions](https://supabase.com/docs/guides/database/functions)
- [Testing guidance](https://supabase.com/docs/guides/functions/unit-test)

This implementation uses the Auth-server user-validation boundary:
GET /auth/v1/user with the bearer JWT and project publishable key (the network
operation underlying getUser(jwt)). The router uses Web-standard fetch without
an SDK dependency. Offline tests exercise it using the installed Node 24 runtime
and network mocks. Supabase Auth performs signature and
user validation. The router's preliminary JWT shape/expiry/issuer/role checks only
reject tokens; decoded claims alone NEVER authorize a privileged RPC. The verified
Auth user ID must match sub, role must be authenticated, and anonymous Auth users
are denied. Auth signing keys are not implemented or stored by this function.

Missing/forged/expired tokens, publishable-only keys, secret-only keys and
service-role bearer JWTs cannot establish issuer identity. A real signed-in user
is still not an issuer until the database live-authorization check succeeds.
All seven operations recheck issuer authorization through SQL; role/admin scope
is never read from client JSON or accepted as a client override. API keys must
be on apikey, never used as a desktop bearer identity. Platform pre-handler 401
responses may have the platform's JSON shape; handler errors use the contract below.

The safe placeholder configuration is `functions/registry-v1/registry.env.example`.
Required server settings:

| Setting | Purpose |
| --- | --- |
| SUPABASE_URL | Platform-injected trusted HTTPS project origin, without path/query/trailing slash; reserved name, not a custom secret |
| REGISTRY_PUBLISHABLE_KEY | Project application key used only for Auth validation |
| REGISTRY_SERVICE_KEY | Server-only secret API key (or compatible legacy service-role key) for fixed RPC calls |
| REGISTRY_GENERATION | Expected UUID stored outside database backup restoration |
| REGISTRY_ENABLED | Exactly true to admit requests; missing/false fails closed |

Keep privileged settings in protected function secrets, never desktop builds,
logs, repository files or responses. Incoming apikey/actor/user_id/role values do
not become server credentials. Auth and RPC requests go only to the configured
origin, reject redirects, validate TLS and have 5s/15s timeouts respectively.
The function never logs headers, bodies, participant lists, raw SQL or errors.
New secret API keys are sent only on apikey. A configured legacy service-role
JWT is also sent as the server-side RPC Authorization bearer; the user's bearer
is never substituted for the privileged RPC credential. Both transports have
offline tests and reached issuer authorization through real local PostgREST probes.
Recheck the selected transport on each approved hosted target.

### Headers and routes

Desktop calls use `Authorization: Bearer <user-access-JWT>` and the project's
publishable `apikey`. For every operation except context also send
`X-Registry-Generation: <generation-from-context>`. POST requires
`Content-Type: application/json`. No CORS headers are enabled; browser Origin
requests are rejected. Unsupported routes/methods and extra query parameters fail.
Requests use `/functions/v1/registry-v1` (the runtime-short `/registry-v1` path is
also supported). There is no event/issuer administration endpoint.

| Method/path | Body or parameters | Database operation |
| --- | --- | --- |
| GET /context | None | get_context(actor,generation) |
| POST /certificates/lookup | `{code}` OR `{event_id,email,certificate_type?}` | lookup_certificate(actor,generation,query) |
| POST /batches | `{batch_id,request}`; request is the reservation contract above | reserve_batch(actor,generation,batch,request) |
| GET /batches | limit 1..100 (default 25), offset 0..10000 (default 0), optional valid state | list_batches(actor,generation,page_limit,page_offset,filter_state) |
| GET /batches/{uuid} | None | get_batch_status(actor,generation,batch) |
| POST /batches/{uuid}/commit | Versioned manifest above, without an outer wrapper | commit_batch(actor,generation,batch,manifest) |
| POST /batches/{uuid}/abandon | Empty JSON object | abandon_reservation(actor,generation,batch) |

Successful responses are `{data: <authoritative RPC result>, request_id: <uuid>}`.
Each response has X-Request-ID, Cache-Control:no-store and nosniff. Request IDs
are server-generated, not authorization/idempotency tokens. Context returns user,
role, normal mode/generation, formats/types, capacity/limits and up to 200 catalog
entries, with events_truncated if necessary. Batch listing returns bounded summary
data, no names/emails, and next_offset. Issuers see their own batches when they also
own every existing certificate in the batch. Administrators can see other batches
for recovery. The same certificate ownership check protects status, reservation
replay, commit and abandonment. Certificate lookup returns issued originals
only to active registry administrators or the issuer owning the original issuing
batch. Reservation reuse and regeneration enforce the same private authorization
helper before comparing a name or copying an original snapshot. A later batch
association, including an abandoned regeneration batch, never transfers ownership.
Unknown/unissued identities return found:false. Knowledge of a code, email or name
does not grant snapshot access. Event catalog access reveals no participants.

Closed events allow authorized existing-only regeneration, preserving original
codes, snapshots and timestamps. New or mixed issuance reservations are rejected
atomically; a closed event does not relax certificate authorization.

Reservation, commit and abandonment each call exactly one transactional RPC.
There is no TypeScript ID allocation, implicit replacement, or automatic retry.
Generation comes from fixed server configuration, matched against the client header
and then checked in PostgreSQL. A timeout/503 is not evidence of rollback: query the
same batch UUID before retrying identical input. Database errors are redacted rather
than forwarding SQL message details, stack/context or connection information.

Completed status includes authoritative issuance_rows, each with only code, email
and issued_at. Timestamp strings are passed through without Date conversion, retaining
PostgreSQL microseconds. Pending/unsuccessful statuses have no successful rows. CSV
serialization remains Issue #16. The handler maps the commit routine's successfully
committed YEAR_ROLLOVER retirement result to HTTP 409; this does not undo retirement.

### HTTP/domain errors

Handler failures are `{error:{code:<stable-code>},request_id:<uuid>}` with no raw
database message. Common codes:

| HTTP | Codes |
| --- | --- |
| 400 | INVALID_REQUEST (malformed JSON, unsupported routes/methods, invalid shape/types) |
| 401 | UNAUTHENTICATED |
| 403 | FORBIDDEN (including revoked/inactive issuers, ownership denial, Origin requests) |
| 404 | BATCH_NOT_FOUND |
| 409 | IDEMPOTENCY_CONFLICT, COMMIT_CONFLICT, CERTIFICATE_BUSY, ISSUER_BUSY, BATCH_COMPLETED, BATCH_EXPIRED_OR_YEAR_CHANGED, YEAR_EXHAUSTED, GENERATION_CHANGED, EVENT_UNAVAILABLE, CONTENT_CONFLICT |
| 413 | REQUEST_TOO_LARGE |
| 422 | INCOMPLETE_MANIFEST, INVALID_REQUEST (database validation) |
| 429 | ALLOCATION_LIMIT (database DAILY_LIMIT) |
| 503 | RECOVERY_REQUIRED, REGISTRY_MAINTENANCE, REGISTRY_RECOVERY_MODE, SERVICE_UNAVAILABLE |

The router bounds actual streamed JSON bytes to 2 MiB even without Content-Length,
body-read time to 10s, nesting to 12 levels, items to 500 and artifacts to 1000.
Unknown keys prevent actor/role/time/code injection. Manifest byte counts must be
positive safe JSON integers; larger values cannot be forwarded with silent numeric
rounding. The database remains responsible for email/business validation, exact
artifact coverage, fingerprints, uniqueness, lifecycle and atomicity. Unexpected
database messages and connection failures become generic SERVICE_UNAVAILABLE.

### Local validation and remaining prerequisites

Run the offline handler suite with installed Node 24 (no downloads):

```powershell
node --test infrastructure/supabase/functions/registry-v1/api.test.mjs
node --check infrastructure/supabase/functions/registry-v1/index.ts
```

The tests mock fetch, as recommended by Supabase testing guidance. The Auth mock
verifies synthetic JWT HMAC signatures and expiry using WebCrypto; the production
Auth transport, reject-only claim checks and verified-user binding are exercised.
RPC mocks verify the trusted actor/generation, fixed origin and privileged headers;
they are complemented by actual disposable PostgreSQL authorization/lifecycle tests,
including test_edge_rpc.py. They do not claim to test the real gateway/Auth service.
The database runner also executes api.integration.test.mjs against the disposable
cluster: the real handler calls real SQL RPCs through an authenticator/service_role
test transport. Auth and HTTP/PostgREST remain substituted. This covers cross-issuer
lookup/reservation/abandonment, owner/admin closed-event regeneration, 500 participants,
1000 both-format artifacts and 501-item rejection without claiming hosted coverage.
The database runner executes all existing tests plus the new RPC tests. No shared
database, live Auth user or Supabase credential is used.

The local toolchain now includes Deno, Supabase CLI, Docker and PostgreSQL 17.
The prior work session executed Deno type checking and real local PostgREST
profile/key-transport probes. Current evidence and remaining hosted verification
are tracked in [READINESS.md](READINESS.md). Node syntax checking alone does not
establish Deno type correctness.

Additional local validation commands from the repository root are:

```powershell
deno check --no-config --cached-only infrastructure/supabase/functions/registry-v1/index.ts
& .\.venv\Scripts\python.exe -B infrastructure/supabase/rehearsal/test_preparation.py
& .\.venv\Scripts\python.exe -B infrastructure/supabase/rehearsal/rehearsal.py inspect
```

The rehearsal preparation tests use synthetic files and harmless Python child
processes, without launching PostgreSQL. Inspection reads the pinned local backup
and reports counts/hashes only. The separate schema-test runner and rehearsal
`execute` mode start/write disposable databases and require authorization for that
activity. They are not read-only validation commands.

Current code requires HTTPS even in development. A local HTTP Supabase stack can
exercise SQL and gateway behavior separately, but the full handler requires a
trusted HTTPS origin that also matches the Auth JWT issuer. Keep JWT checks and
TLS validation enabled. See [DEPLOYMENT.md](DEPLOYMENT.md).

Remaining operational work includes production ownership/backup readiness and
hosted revocation/draining validation. Desktop integration and file/CSV validation
belong to Issue #16. Public certificate verification, certificate revocation or
correction, full-history export, and filesystem inspection are outside this API.

## External recovery fencing and administration (Subtask 4)

See [OPERATIONS.md](OPERATIONS.md) for initial setup, issuer/event administration,
capacity review, backups, restoration, deployment prerequisites and safety limits.
No live deployment or recovery was performed by adding these files.

Every Edge operation passes the protected REGISTRY_GENERATION to PostgreSQL;
authorize_reservation compares it to registry_state under the singleton lock before
accessing operational data. This preserves the check inside the transaction rather
than doing a race-prone separate context preflight. A database/batch generation
mismatch maps to sanitized RECOVERY_REQUIRED (503); missing/invalid external UUID
also fails closed before network calls. A stale client header is still
GENERATION_CHANGED (409). With a syntactically valid expected generation,
REGISTRY_ENABLED=false returns REGISTRY_MAINTENANCE (503).
Mode, live authorization, ownership and platform JWT checks remain in place.

Migration 5 adds only two private database-owner SECURITY INVOKER procedures:

- begin_recovery(operator_id,expected_database_generation,external_generation,approval_id)
  compare-and-swaps generation, enters recovery, disables all issuers and audits.
- finish_recovery(operator_id,expected_generation,approval_id,historical_complete,
  permissions_and_pending_reviewed) requires inactive issuers and explicit operator
  attestations; retires reviewed pending claims, structurally validates completed
  batches and revalidates their export generation, then sets normal mode atomically.
  It leaves issuers inactive. Codes, original timestamps/snapshots and request
  fingerprints are never rewritten. Failure leaves recovery mode unchanged.

These are not RPCs, are not granted to any client/server API role, and expose no
override endpoint. Approval UUIDs link audit rows to external administrator records.
They cannot prove completeness of absent records or manipulate external settings.
Unrecoverable acknowledged history requires remaining blocked. The external gate,
fresh expected generation, revoked old worker credentials and draining in-flight
transactions must precede restore; a database cannot detect all rollback of itself.

test_recovery.py runs in a separate pristine clone within the disposable cluster
so global history validation is not polluted by deliberate negative fixtures in
older tests. It covers all RPC generation checks, private controls/grants, history
preservation, pending retirement, corrupt-history rollback and deliberate approval.
The offline API suite checks mismatches across all seven routes and missing/invalid
external configuration, with sanitized errors. Neither suite proves hosted runtime
or gateway compatibility. The successful isolated restore drill is recorded in
[rehearsal/README.md](rehearsal/README.md); hosted platform recovery remains separate.
