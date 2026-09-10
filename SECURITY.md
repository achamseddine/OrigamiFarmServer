# Security

## Threat model summary

Origami Server holds operational data (animal health, financial, farm visitor) for many
independent, mutually distrusting customer organizations behind one shared codebase and, by
default, one shared database. The primary threats this design targets:

1. **Cross-tenant data leakage** — one customer reading or writing another's records, whether by
   application bug, a stray/misconfigured connection, or a client attempting to supply its own
   tenant identifier.
2. **License/entitlement bypass** — a client enabling a module in its own UI that the tenant never
   purchased, or continuing to operate after a device is revoked or an account is suspended.
3. **Device compromise** — a lost/stolen tablet being used to keep accessing tenant data or minting
   valid offline credentials.
4. **Platform-insider risk** — an Origami staff account browsing customer data without a
   business reason, or without a record of having done so.
5. **Secret leakage** — credentials or signing keys ending up in source control, logs, or a
   client-shipped artifact.

## How each is addressed (with the file that does it)

| Threat | Mitigation | Where |
|---|---|---|
| Cross-tenant reads/writes | PostgreSQL RLS, `FORCE ROW LEVEL SECURITY`, fail-closed on missing tenant context | `api/migrations/tenant/versions/*`, TENANCY.md |
| Client-supplied tenant ID trusted | `tenant_id` resolved server-side from device row or verified membership, never from a request body/header value taken at face value | `app/auth/dependencies.py:get_tenant_context`, TENANCY.md |
| UI-only module gating | `EntitlementService` is called from the same authorization dependency chain used by every protected route, not just from the `/me/entitlements` response | `app/entitlements/service.py`, `app/auth/dependencies.py:require_module` |
| Object enumeration (cross-tenant guess) | RLS makes the row not exist for the wrong tenant; the API's only path is `NOT_FOUND` — no separate "forbidden" branch to leak existence | `app/tenant_api/routes.py:_load_animal_or_404` |
| Revoked device continuing to operate | Device status checked first, before membership/tenant lookups, in `get_tenant_context`; a revoked device is refused a new lease | `app/auth/dependencies.py`, `app/devices/routes.py` |
| Reused/expired activation codes | Codes stored only as SHA-256 hashes, single-use state machine, expiry checked and enforced server-side | `app/devices/service.py`, `app/devices/routes.py` |
| Offline lease forgery | RS256 asymmetric signing; private key never leaves the server or a client artifact | `app/devices/lease.py`, LICENSE_ENTITLEMENTS.md |
| Standing platform "god mode" | Support access is a time-boxed `support_session` row with `expires_at`, checked via `is_active()`, never a permanent grant | `app/support/models.py`, `app/support/routes.py` |
| Unaudited privileged actions | Every tenant status change, entitlement change, device revoke, membership grant, support session, export request writes an `audit_event` **in the same DB transaction** as the change | `app/audit/service.py`, called throughout `app/platform`, `app/entitlements/state_machine.py` |
| Secrets in git | `.env` files, `infrastructure/keys/*.pem` (license-lease signing keys) are gitignored; `.env.example` ships placeholders only | `.gitignore`, `.env.example` |
| Weak transport/headers | Security-header middleware on every response (HSTS, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Cache-Control: no-store`); TLS termination is expected at the reverse proxy in front of this app, not handled by the app itself | `app/common/logging.py:SecurityHeadersMiddleware` |
| Injection | SQLAlchemy Core/ORM parameterized queries throughout; the one raw-SQL RLS policy body is a fixed migration string with no user input interpolated into it | migrations, all route/service modules |
| Missing correlation for incident response | Every request gets an `X-Correlation-Id` (client-supplied or generated), echoed in the response and attached to every audit event it produces | `app/common/logging.py`, `app/audit/service.py` |

## Authentication

Three credentials can reach this API, and they are deliberately not interchangeable:

| Credential | Issued by | Verified by | Carries |
|---|---|---|---|
| Console session | `POST /platform/v1/auth/login` (email + bcrypt password) | `LocalSessionProvider` — HS256 over `APP_SECRET_KEY`, `typ=platform_session` required | An Origami staff identity; authority comes from `platform_role_assignment`, never the token |
| OIDC access token | An external provider (Keycloak or compatible) | `OIDCIdentityProvider` — RS256 via published JWKS, audience + issuer checked | The same, for organizations that front the console with their own IdP |
| FarmOS tablet token | `POST /api/v1/auth/login` | `app/farmos/security.py:decode_access_token` | One farm worker, scoped to one tenant |

`ChainedIdentityProvider` routes on the JWT's algorithm header — HS256 to the local verifier,
RS256 to OIDC — and each branch pins its own algorithm list and key source, so a token cannot be
steered into a verifier that would check it against the wrong key. The tablet token and the
console session are both HS256 over the same secret, which is exactly why the console session
carries a `typ` claim the verifier requires: without it, a tablet token would resolve to a
platform identity.

Sign-ins and password changes are audited (`platform.signed_in`, `platform.password_changed`).
Login answers identically for an unknown address, an account with no password, and a wrong
password, so it cannot be used to enumerate staff addresses.

`AUTH_DEV_MODE=true` additionally enables `POST /api/v1/auth/dev-login`, which mints a session
from an email address with **no password at all** — local and CI only. `app/main.py` refuses to
start with it enabled when `ENVIRONMENT=production`, enforced in code rather than documented.

Passwords are bcrypt (`app/auth/passwords.py`), minimum 12 characters, with the first staff
account created by `scripts/create_platform_admin.py`. MFA is not implemented for password
sign-in; deployments that need it should put an OIDC provider in front, where MFA is a realm
policy and this repo's only responsibility is validating the resulting token.

## Known gaps in this v0.1 pass (see ARCHITECTURE.md "What's real vs. scaffolded")

- No rate limiting is implemented yet (would sit at the reverse-proxy layer or as FastAPI
  middleware — not yet added).
- No CSRF middleware — the admin web uses bearer-token auth from `localStorage`, not cookies, so
  CSRF (which targets ambient cookie auth) does not apply to it as built; if cookie-based sessions
  are introduced later, CSRF protection must be added at that point.
- No malware/AV scanning on uploaded files — `POST /api/v1/files/presign-upload` validates
  authorization and records metadata, but content scanning is not implemented.
- Dependency/secret scanning in CI is configured (see `.github/workflows/ci.yml`) but has not been
  run against a real GitHub Actions runner in this environment.
- The `OIDCIdentityProvider` path is code-complete (standard PyJWT JWKS validation) but has not
  been integration-tested against a live Keycloak instance here (no Docker daemon in this sandbox).

## Reporting

This is a pre-launch foundation; there is no public-facing deployment yet. Treat any security
finding as you would for internal pre-production code — file it against this repository.
