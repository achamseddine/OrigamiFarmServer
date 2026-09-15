# License, Entitlement & Device Model

## Module entitlements

`tenant_entitlement` holds one row per `(tenant_id, module_code)` — the current effective right for
that tenant to use that module. `EntitlementService.is_module_active(tenant_id, module_code)`
(`app/entitlements/service.py`) is the only function anywhere in the codebase that answers "is this
allowed right now"; it is called both by `GET /api/v1/me/entitlements` (client-facing, for UI
rendering) and by `require_module()` in the authorization chain (server-enforced, on every
protected route). There is deliberately no second code path — a client cannot get a module enabled
in its UI without the server also enforcing it, because it's the same check.

`is_module_active` returns `false` if any of these hold:

- the tenant's own status disallows modules entirely (`SUSPENDED`/`TERMINATED` — see below), or
- there is no entitlement row for that module, or
- its `status` isn't `ACTIVE` or `TRIAL`, or
- `effective_from` is in the future, or `effective_until` has passed.

### Entitlement state machine

```
INACTIVE → TRIAL, ACTIVE
TRIAL → ACTIVE, EXPIRED, INACTIVE
ACTIVE → SCHEDULED_DISABLE, SUSPENDED, INACTIVE
SCHEDULED_DISABLE → INACTIVE, ACTIVE
SUSPENDED → ACTIVE, INACTIVE
EXPIRED → ACTIVE, TRIAL, INACTIVE
```

Enforced by `app/entitlements/state_machine.py:transition_entitlement`, which rejects any
transition not in this map (`CONFLICT`) and writes an `audit_event` (actor, before/after status,
reason) in the same transaction as the change — a status change and its audit record can never
drift apart, because a rollback rolls back both.

**Deactivation never deletes data.** `deactivate_module` in `app/platform/routes.py` only moves the
entitlement to `INACTIVE`; the farm-data-plane rows (e.g. `animal`) are completely untouched.
Access is blocked going forward (`MODULE_NOT_ENTITLED` on the next request) but the historical
record is exactly as it was. Proven by
`api/tests/test_entitlements.py::test_module_deactivation_preserves_data_but_blocks_access`, which
reads the row directly through `TenantDataRouter` after deactivation and asserts it's unchanged.


## Issuing a licence to a customer

`POST /platform/v1/tenants/{id}/licence` produces the whole handover in one
call, because issuing half of it is the mistake worth designing out — a
customer with a tablet key and no way to sign in, or an account with
nothing to pair a device with, and no screen saying which half is missing.

It returns, exactly once:

| | What it is | Backed by |
|---|---|---|
| **Pairing key** | `ORG-XMRH-M3ND-XFR6` — typed into the app to pair one device | `device_activation`, hashed |
| **Sign-in link** | `/welcome/?token=…` — the owner sets their own password | `membership_invitation`, hashed |

Neither is stored in a readable form, so a lost pack is reissued rather
than recovered. Issuing again revokes the outstanding key and supersedes
the outstanding invitation: a customer never has two live keys.

The two are deliberately named apart. They were both called "activation"
once — the tablet code and the account page — and an admin duly pasted a
pairing key into `/activate/?token=`, which is a reasonable thing to do
when the product uses one word for two credentials. The page is now
`/welcome`, `/activate` forwards to it so links already sent survive, and
`/welcome` recognises a pasted `ORG-…` key and says what it actually is.

The key's alphabet excludes one of each confusable pair (`0/O`, `1/I/L`,
`5/S`, `8/B`) because it gets read down a phone line, and redemption
normalises case and dashes for the same reason. Codes issued before this
format still redeem exactly as they did — the lookup tries the literal
string first.

### No mail server? Issue with a password

`credential: "password"` sets the owner's password directly and returns it
instead of a sign-in link, which is the workable procedure when nothing can
deliver a link. The whole handover then fits in a phone call:

The create-customer wizard (**Tenants → + Create Tenant**) does all of this
in one pass — company, farm, plan, owner — and its last screen is the
handover itself, so a customer created there needs nothing afterwards. For
a customer that already exists:

1. **Tenants → the customer → Licensing → Issue with a password.**
2. Read them three things: their email address, the password, and the
   pairing key.
3. They sign in on the tablet with the first two, and type the third in
   when the app asks.

The password is generated from the same unambiguous alphabet as the key but
in lower case with no `ORG-` prefix, so the two are never confused while
being read out together. It is stored as a bcrypt hash like any other, and
`password_changed_at` is left null — the console keeps showing "not yet
changed by them" until the owner replaces it from the tablet
(`POST /api/v1/auth/change-password`). That endpoint exists precisely
because setting somebody's password is only defensible if they can take it
back. Every admin-set password is audited as
`tenant_user.password_set_by_admin` against the admin who did it, because
what matters is who could have known it.

`POST /platform/v1/tenants/{id}/memberships/{mid}/password` does the same
for any farm user, one at a time, from the Access tab.

When SMTP is configured both halves go to the owner in a single email.
When it is not, the response says so plainly and the console shows both
for an admin to send on; it never reports mail it did not send.

## Tenant status

```
ONBOARDING → TRIAL, ACTIVE, TERMINATED
TRIAL      → ACTIVE, SUSPENDED, TERMINATED
ACTIVE     → GRACE, SUSPENDED, TERMINATED
GRACE      → ACTIVE, SUSPENDED, TERMINATED
SUSPENDED  → ACTIVE, GRACE, TERMINATED
TERMINATED → (terminal)
```

(`app/common/enums.py:TENANT_STATUS_TRANSITIONS`, enforced by
`transition_tenant_status` — same audit-in-the-same-transaction guarantee as entitlements.)

`SUSPENDED` tenants keep their data; `EntitlementService` simply stops returning modules as active,
so the tablet loses write access to protected APIs but the account isn't destroyed. Terminating an
account is deliberately a separate, harder-gated action: `POST /platform/v1/tenants/{id}/status`
with `status: TERMINATED` requires `PLATFORM_SUPER_ADMIN` specifically, even though
`PLATFORM_COMMERCIAL_ADMIN` can suspend/reactivate/grace a tenant on their own — see
`app/platform/routes.py:change_tenant_status` and `_require_role`. This is the "commercial admin
cannot perform a super-admin-only operation" boundary from the product brief, and it's a real
authorization check, not a UI-only restriction (tested in `test_platform_roles.py`).

## Offline license lease

`app/devices/lease.py`. Issued by `POST /api/v1/license/refresh` (an authenticated tenant user
plus an `X-Device-Id` header for a non-revoked device) and at the end of
`POST /api/v1/device/activate`. Structure:

```json
{
  "lease_id": "...", "tenant_id": "...", "device_id": "...",
  "farm_ids": ["..."], "modules": ["CORE", "ANIMALS", "FEED"],
  "permission_profile_hash": "...", "issued_at": "...", "expires_at": "...",
  "policy_version": 1
}
```

Signed as a JWS (RS256 via PyJWT) with a private key that never leaves the server
(`LICENSE_LEASE_PRIVATE_KEY_PATH`, generated once by `scripts/generate_license_keys.py` and never
committed — see `.gitignore`). A tablet holds only the public key
(`LICENSE_LEASE_PUBLIC_KEY_PATH`) and can verify a lease entirely offline; it cannot mint or alter
one. `modules` and `farm_ids` in the lease are computed server-side from the tenant's *current*
entitlements and the device's farm scope at issuance time — never trusted from the device.

A revoked device (`device.status != ACTIVE`) is refused a new lease
(`DEVICE_REVOKED`, checked before anything else — see TENANCY.md) but leases already issued to it
remain valid until their own `expires_at`; there is no separate "revoke this specific lease"
mechanism in v0.1. `LICENSE_LEASE_DEFAULT_TTL_HOURS` (default 72h) bounds how long a lost/stolen
device can keep operating offline before it must check in again — configurable per deployment,
matching the product brief's "no sudden lockout, but not unlimited either" requirement.

## Device activation

`device_activation` rows hold only a **SHA-256 hash** of the one-time code
(`app/devices/service.py:hash_activation_code`) — never the plaintext — plus tenant/farm scope,
`expires_at`, and single-use status (`PENDING` → `USED`, or → `EXPIRED`). `POST
/api/v1/device/activate` requires no bearer token (the code itself is the credential); it:

1. Hashes the supplied code and looks up the row by that hash.
2. Rejects `USED` (`ACTIVATION_CODE_ALREADY_USED`) or past-`expires_at` (`ACTIVATION_CODE_EXPIRED`,
   also flipping the row to `EXPIRED`) before creating anything.
3. Creates the `device` row, marks the activation `USED` with a back-reference, writes an audit
   event, and issues the device's first license lease in the same request.

Both reuse and expiry are enforced against real state transitions, not just a time comparison at
read time — see `api/tests/test_devices.py`.

## Platform roles

`PLATFORM_SUPER_ADMIN` (bypasses every specific role check), `PLATFORM_COMMERCIAL_ADMIN` (plans,
subscriptions, entitlements, suspend/reactivate — not terminate, not raw restore),
`PLATFORM_SUPPORT_ADMIN` (devices, support sessions — not commercial state), `PLATFORM_AUDITOR`
(read-only everywhere; verified by attempting a write and asserting `PLATFORM_ROLE_REQUIRED` in
`test_platform_roles.py::test_auditor_is_read_only`).
