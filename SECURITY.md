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

Production/staging: standards-based OIDC against Keycloak (or any compatible provider) —
`app/auth/providers.py:OIDCIdentityProvider` validates via the provider's published JWKS (RS256,
audience + issuer checked). Local/test only: `AUTH_DEV_MODE=true` swaps in
`DevIdentityProvider`, which signs/verifies with `APP_SECRET_KEY` (HS256) and has no external
dependency. `app/main.py` refuses to start with `AUTH_DEV_MODE=true` when
`ENVIRONMENT=production` — this is enforced in code, not just documented.

MFA for platform admins is an IdP-side policy (Keycloak realm configuration); this repo's
responsibility is to require and correctly validate the resulting token, which it does uniformly
regardless of whether MFA was used upstream.

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
