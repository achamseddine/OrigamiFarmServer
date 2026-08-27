# API Error Codes

Every error response has the shape:

```json
{ "error": { "code": "MODULE_NOT_ENTITLED", "message": "Module ANIMALS is not entitled" } }
```

`code` is stable and part of the API contract (`app/common/errors.py:ErrorCode`) — safe to branch
on in client code. `message` is a human-readable default and may change wording between versions;
build UI copy against `code`, not `message`.

| Code | HTTP status | Meaning |
|---|---|---|
| `UNAUTHENTICATED` | 401 | No/empty bearer token. |
| `INVALID_TOKEN` | 401 | Token present but failed verification (bad signature, expired, wrong issuer/audience). |
| `TENANT_SUSPENDED` | 403 | Tenant status is `SUSPENDED`; write access blocked, per LICENSE_ENTITLEMENTS.md. |
| `TENANT_TERMINATED` | 403 | Tenant status is `TERMINATED`. |
| `MODULE_NOT_ENTITLED` | 403 | The tenant has no active/trial entitlement for the module this endpoint requires. |
| `PERMISSION_DENIED` | 403 | Module is entitled, but the caller's membership lacks the specific permission (or role) required. |
| `DEVICE_REVOKED` | 403 | The device named by `X-Device-Id` is not `ACTIVE`. |
| `DEVICE_NOT_FOUND` | 404 | `X-Device-Id` doesn't resolve to a known device. |
| `LICENSE_LEASE_EXPIRED` | 403 | Reserved for tablet-side enforcement of an expired offline lease (server-side lease issuance always mints a fresh one; this code is for client use when validating a stored lease offline). |
| `FARM_SCOPE_DENIED` | 403 | The caller's membership doesn't include the farm the request targets. |
| `PLATFORM_ROLE_REQUIRED` | 403 | The caller lacks a required platform role (e.g. a commercial admin attempting a super-admin-only action). |
| `MEMBERSHIP_NOT_FOUND` | 403 | No active tenant membership resolves for this caller (or the requested `X-Membership-Id` doesn't belong to them). |
| `ACTIVATION_CODE_INVALID` | 400 | Code doesn't match any known (hashed) activation record. |
| `ACTIVATION_CODE_EXPIRED` | 400 | Code matched but its `expires_at` has passed. |
| `ACTIVATION_CODE_ALREADY_USED` | 400 | Code has already activated a device (single-use). |
| `NOT_FOUND` | 404 | Generic not-found — deliberately also used for "exists, but in another tenant" so existence is never leaked. |
| `VALIDATION_ERROR` | 422 | Request-shape validation failure beyond standard Pydantic 422s (e.g. multiple memberships with no `X-Membership-Id` hint). |
| `CONFLICT` | 409 | A state-machine transition isn't allowed from the current state (tenant status, entitlement status), or a uniqueness constraint was violated (duplicate `company_code`, duplicate membership, etc). |
| `IDEMPOTENCY_REPLAY` | 200 | Reserved for future non-sync idempotency-key use; sync's own replay signal is the per-change `"REPLAYED"` status, not this top-level code — see SYNC_PROTOCOL.md. |
| `SYNC_CONFLICT` | 409 | Reserved top-level code; in practice sync conflicts are returned as a per-change `"CONFLICT"` result inside a 200 response (see SYNC_PROTOCOL.md) so one bad change in a batch doesn't fail the whole push. |
| `RATE_LIMITED` | 429 | Reserved — no rate limiter is wired up yet (see SECURITY.md, Known gaps). |
| `INTERNAL_ERROR` | 500 | Unhandled server error. |

Adding a new code: append to `ErrorCode` and `_STATUS_BY_CODE` in `app/common/errors.py` and this
table in the same change. Never repurpose an existing code's meaning — clients may already branch
on it.
