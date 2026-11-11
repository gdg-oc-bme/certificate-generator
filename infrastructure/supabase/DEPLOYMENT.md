# Registry deployment and configuration

The existing hosted Supabase project is a separate development environment.
Its six migrations are reported applied and `REGISTRY_ENABLED=false` remains the
required current state. This procedure records future authorized actions. It
does not authorize deployment, issuer activation, generation rotation or reopening.
Track evidence and approvals in [READINESS.md](READINESS.md).

## Target selection and prerequisites

1. Identify the approved organization, project reference, environment, operator,
   reviewed source revision and change record. Confirm the target independently
   of a remembered CLI link. Use synthetic data in development. Production needs
   its own ownership, access, retention, recovery and issuance approvals.
2. Verify PostgreSQL 16+ permissions and `pgcrypto` in `extensions`. The native
   recovery rehearsal specifically requires PostgreSQL 17. Supabase supplies Auth
   tables and standard roles; never run `tests/bootstrap.sql` on a Supabase project.
3. Compare the target's real migration history with the six timestamped source
   files and inspect any drift. Apply only genuinely pending reviewed migrations
   in timestamp order. All six are already applied on the current development
   project. Preserve applied files and use a seventh migration for future changes.
4. Keep the external gate closed while checking database generation/mode, API
   exposure and grants. Existing history needs reconciliation under the recovery
   runbook before any generation change. Initial setup must establish that the
   namespace is unused before its private initialization controls are approved.

The SQL migration identity needs the ownership/CREATEROLE authority described in
the [infrastructure README](README.md). Temporary SET/INHERIT membership in
`registry_reserver` must be revoked after each migration. Ordinary API roles must
never receive that membership.

## Deployment sequence

1. Complete the local checks in [README.md](README.md) and retain sanitized results.
   Review a backup and recovery plan for the approved target. Record migration
   hashes, object owners and the expected external generation outside restoration.
2. Through the approved Supabase administrative workflow, install only pending
   migrations. Stop on errors and inspect the actual state before proceeding.
   Do not reset a populated target, repair its migration ledger merely to silence
   drift, or reapply migrations already recorded as applied.
3. Set the hosted Data API exposed schemas to only `registry_api`. `config.toml`
   defines the local configuration and does not itself change hosted exposure.
   Refresh the PostgREST schema cache after DDL and verify effective grants rather
   than assuming the target's default privileges match the test bootstrap.
4. Provision the four custom registry settings in the protected Edge configuration
   below. Keep `REGISTRY_ENABLED=false`. Verify the supplied HTTPS origin, keys,
   expected generation and signing-key mode belong to this same project.
5. Deploy only `registry-v1` from `infrastructure/supabase/functions/registry-v1/`,
   retaining `verify_jwt=true` from `config.toml`. Keep the external gate closed.
6. Validate missing/invalid JWT rejection, maintenance gating, private-schema
   exclusion and ordinary-role denial. Review local and hosted error responses
   without publishing headers, SQL details or participant snapshots.
7. For a separately approved development test window, validate real user login,
   Auth-server validation, all seven operations, selected server-key transport,
   cross-issuer denial, administrator demotion, revocation, retries and generation
   fencing. Successful calls need a deliberately authorized gate/issuer state.
   This document does not change the current hosted `false` setting. Return any
   approved development test window to the closed state and record the outcome.
8. Recheck the owner/grant matrix and retention/recovery responsibilities. Production
   opening is a separate decision. Neither deployment success nor SQL consistency
   establishes backup completeness or permits official issuance.

For CLI v3, the following are command shapes for an explicitly approved target.
Verify installed CLI help and the resolved target before use. A project reference
is an identifier, not a credential. Authentication belongs in protected CLI/platform
storage; never put a database URL or password in these examples.
Review the entire CLI plan, including any role, seed or vault effects, before
confirming an approved operation.

```powershell
$deploymentProjectRef = 'REPLACE_WITH_EXPLICITLY_APPROVED_PROJECT_REF'
supabase --workdir .\infrastructure db push --remote=$deploymentProjectRef --dry-run
# Only after the reviewed plan and target are approved:
supabase --workdir .\infrastructure db push --remote=$deploymentProjectRef
supabase --workdir .\infrastructure functions deploy registry-v1 --remote=$deploymentProjectRef
```

No JWT-verification bypass or function-pruning flag is part of this procedure.
Review CLI-version differences instead of falling back to an implicit target.
[Supabase CLI reference](https://supabase.com/docs/reference/cli)

## Runtime settings and secret handling

| Setting | Source and handling | Failure behavior |
| --- | --- | --- |
| `SUPABASE_URL` | Supabase-injected HTTPS project origin; no path, query, credentials or trailing slash | Invalid origin becomes `SERVICE_UNAVAILABLE` once earlier gates pass |
| `REGISTRY_PUBLISHABLE_KEY` | Project application key used for `/auth/v1/user`; custom protected runtime setting | Missing key becomes `SERVICE_UNAVAILABLE` once earlier gates pass |
| `REGISTRY_SERVICE_KEY` | Server-only secret API key or compatible legacy service-role JWT; prefer a separately revocable component key | Missing key becomes `SERVICE_UNAVAILABLE` once earlier gates pass |
| `REGISTRY_GENERATION` | Expected UUID recorded independently of database backups and provided by trusted administration | Missing/invalid UUID returns `RECOVERY_REQUIRED` before Auth or RPC calls |
| `REGISTRY_ENABLED` | Custom gate; exactly the string `true` enables the handler | Missing or any other value returns `REGISTRY_MAINTENANCE` after generation syntax validation |

Use `registry.env.example` as a reference for names and placeholder formats. The
`SUPABASE_` prefix is reserved by the platform; do not upload the example wholesale
as custom secrets or attempt to override the injected URL. Set only the custom
`REGISTRY_*` values through the protected project secrets interface.
[Supabase environment documentation](https://supabase.com/docs/guides/functions/secrets)

Keep real values, Auth exports, SQL connection strings and backup data outside Git,
shared terminals and reports. The application key's public nature does not make a
mixed environment file safe to publish. Report configuration presence and validation
results instead of values. Do not place a service key in a desktop build.

`index.ts` reads configuration when constructing its handler. A secret update alone
does not prove that old workers or in-flight transactions have stopped. Recovery
requires independent credential revocation, worker/session inspection and draining
as described in [OPERATIONS.md](OPERATIONS.md).

The user JWT is sent only to Auth for verification. Fixed RPC calls use the server
key with `Content-Profile` and `Accept-Profile` set to `registry_api`. New secret
keys are sent on `apikey` alone; a legacy JWT server key also supplies its own
Authorization bearer. The user's bearer is never forwarded as the privileged key.
Verify this transport on the selected gateway before accepting compatibility.

## Role and permission verification

| Identity | Intended authority |
| --- | --- |
| `anon`, `authenticated` | No registry API execution or private-table access; a signed-in user alone is insufficient |
| `service_role` | Execute the eight narrow `registry_api` routines; no direct private-table access or recovery execution |
| `registry_reserver` | Restricted NOLOGIN routine owner, explicit table/column grants and RLS policies, empty function search paths; no schema CREATE or caller memberships |
| Active `issuer` row | SQL business authority for owned batches and original issued certificates; does not grant direct database access |
| Active `registry_admin` row | Cross-issuer business authority, checked live; does not grant database-owner recovery privileges |
| Trusted migration/database owner | DDL and separately authorized private recovery administration; can bypass safeguards and is within the trust boundary |

Check schema USAGE, all effective function/table/column privileges, role memberships,
function ownership/security/search paths, RLS policies, constraints and history
triggers. Check both positive API execution and negative direct-table/recovery paths.
The native rehearsal uses a trusted `postgres` superuser, so it does not establish
the hosted administrator's platform restrictions.

The privileged RPC boundary trusts the server-supplied actor UUID. Possession of
the component service key permits asserting an actor directly; the Edge Auth check
protects ordinary callers, not a compromised privileged credential. Protect and
revoke that credential independently of issuer business-role changes.

## HTTPS, JWT and fail-closed checks

The gateway performs preliminary JWT checks with `verify_jwt=true`. The handler
also requires an authenticated, unexpired user token whose issuer exactly matches
`SUPABASE_URL + /auth/v1`, then obtains the verified user from Auth. It rejects
anonymous Auth users, forged identities, Origin-bearing requests and user-supplied
actor overrides. API keys identify applications and never substitute for issuer
identity. Verify the target's actual signing-key behavior with verification enabled.
[Supabase authorization headers](https://supabase.com/docs/guides/functions/auth-headers)

The default local Supabase API is HTTP. A complete handler integration requires
trusted HTTPS reachable inside the Edge container and a matching Auth issuer.
A proxy alone does not align the issuer or install its CA inside the runtime.
Use an isolated hosted development project or an explicitly reviewed local TLS
configuration. Maintain certificate validation and JWT verification.

The handler's gate order is generation syntax, external enable flag, then required
keys/HTTPS origin, followed by user validation. PostgreSQL independently checks
generation, recovery mode and live issuer authority under its singleton lock.
Server/database generation mismatches return sanitized 503 `RECOVERY_REQUIRED`;
recovery mode returns 503 `REGISTRY_RECOVERY_MODE`. A stale client generation
header returns 409 `GENERATION_CHANGED`. Auth/gateway failures may use platform
JSON before the handler runs. Timeout or 503 cannot prove rollback: inspect the
same batch UUID and retry identical intent only.
