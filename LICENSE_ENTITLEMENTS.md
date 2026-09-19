# Subscription, Access & Device Model

Origami is sold as **one subscription at one price, covering the whole
product**. There are no tiers, no per-module add-ons, and no device
licences. This file describes what replaced them and what is still
enforced, because most of what used to be here was enforcement.

## What a customer may open: everything

There is no per-module entitlement check anywhere in the request path any
more. `require_module()` and the `EntitlementService` behind it are gone
from `app/auth/dependencies.py` — they asked whether a tenant had bought a
module, which now has exactly one answer, and left in place they would
have refused every customer created after the change, since nothing writes
entitlement rows.

Two checks remain, and they are the ones that were always doing the real
work:

- **Is this customer's account in good standing?**
  `get_tenant_context` refuses a `SUSPENDED` tenant (`TENANT_SUSPENDED`)
  or a `TERMINATED` one (`TENANT_TERMINATED`) before any route body runs.
  The tablet's own dependency chain (`app/farmos/deps.py`) does the same
  with a farmer-facing message about a paused subscription.
- **Is this person allowed to do this?**
  `app/farmos/deps.py:require_permission(module, action)` reads the
  person's own membership permission grid — set by their farm's owner or
  manager, not by us. An owner or manager has full access
  (`is_full_access_role`); everybody else has exactly what they were
  granted.

`GET /api/v1/modules/catalog` therefore returns every module with
`licensed_active: true` for every customer, including one with no
subscription recorded at all. That last part is deliberate: being unpaid
is a commercial state, and cutting a farm off mid-season is a decision
somebody makes, not a default the software applies while nobody is
looking. What an unpaid customer costs you is visible on the Business
screen instead.

`license_code` survives on `module_catalog`, demoted from a gate to a
description: it is how "Milk Production" is known to belong with `MILK`,
and `GET /platform/v1/licences` groups the catalog by it so the console
can show the product in readable sections.

## The one plan

`app/plans/subscription_plan.py` holds it: code `ORIGAMI`, created on
first read by `get_or_create_plan()`. Consequences:

- `GET /platform/v1/plans` returns a list of exactly one, and never
  returns the retired tiers — migration `f9a3c17e64b2` moved every
  subscription onto `ORIGAMI` and marked `STARTER`/`GROWTH`/etc.
  `ARCHIVED`.
- `POST /plans` and `PUT /plans/{id}/modules` are gone rather than
  guarded. A second plan is not a permission question; it is a thing the
  product no longer has.
- `PATCH /plans/{id}` remains, and is the only thing the console's
  Subscription screen does: set the price. Repricing is audited
  (`plan.updated`), because it changes what every customer contributes to
  recurring revenue.
- The plan is seeded **unpriced**. An invented price flows into the
  revenue dashboard and is indistinguishable from a real one there; an
  unpriced subscription is reported separately rather than counted as
  free.

`PATCH /platform/v1/tenants/{id}/subscription` records what a customer
pays and grants nothing. It still accepts `plan_id` and
`apply_plan_modules` from an older console build and ignores them, so a
customer is not un-subscribable during the minutes between the server
updating and somebody's browser reloading.

## Handing a customer their way in

`POST /platform/v1/tenants/{id}/licence` produces the whole handover in
one call. It used to return two credentials — a tablet pairing key and a
sign-in link — and issuing half of it was the mistake worth designing
out. There is one now:

| | What it is | Backed by |
|---|---|---|
| **Sign-in link** (`credential: "link"`) | `/welcome/?token=…` — the owner sets their own password | `membership_invitation`, hashed |
| **Password** (`credential: "password"`) | generated, read down a phone line | `user_identity.password_hash` |

Neither is stored in a readable form, so a lost handover is reissued
rather than recovered.

`credential: "password"` is the workable procedure when nothing can
deliver a link, and the whole handover then fits in a phone call. The
create-customer wizard (**Tenants → Create a customer**) does the whole
thing in one pass — company, farm, subscription, owner — and its last
screen is the handover itself. For a customer that already exists:

1. **Tenants → the customer → Handover → Hand over a password.**
2. Read them two things: their email address and the password.
3. They sign in on the tablet, and every module is there.

The password is generated from an alphabet with one of each confusable
pair removed (`0/O`, `1/I/L`, `5/S`, `8/B`), because it gets read down a
phone line. It is stored as a bcrypt hash like any other, and
`password_changed_at` is left null — the console keeps showing "not yet
changed by them" until the owner replaces it from the tablet
(`POST /api/v1/auth/change-password`). That endpoint exists precisely
because setting somebody's password is only defensible if they can take
it back. Every admin-set password is audited as
`tenant_user.password_set_by_admin` against the admin who did it, because
what matters is who could have known it.

`POST /platform/v1/tenants/{id}/memberships/{mid}/password` does the same
for any farm user, one at a time, from the Access tab.

When SMTP is configured the handover goes to the owner by email. When it
is not, the response says so plainly and the console shows it for an admin
to pass on; it never reports mail it did not send.

The old `/activate` page still forwards to `/welcome`, and `/welcome`
recognises a pasted `ORG-…` key and says that tablets are no longer
paired — keys handed out before this change are still on scraps of paper.

## Devices

A tablet puts itself on the list by signing in: `POST /api/v1/auth/login`
accepts an optional `installation_id` (plus `device_name` and
`app_version`) and records what it sees. Nothing is generated and nothing
is typed into the app.

The list therefore answers **which tablets is this customer using**, not
which ones they are permitted — which was always the question anybody
actually asked. Two rules:

- A tablet that sends no `installation_id` still signs in. A device list
  is worth having; it is not worth blocking a farm worker's morning over.
- A **revoked** device stays revoked. Revoking is how an operator says a
  tablet has been lost, and somebody signing in on it is the least
  convincing possible argument for undoing that.

Recording a device never fails a sign-in: the write is wrapped and rolled
back on any database error, because a device list is diagnostic and a
sign-in is not.

### What went with device licences

The signed offline lease (`app/devices/lease.py`), the RS256 keypair, the
one-time activation codes (`device_activation`), and
`scripts/generate_license_keys.py` are all deleted. Nothing signs a
lease any more, so a deployment no longer has a private key that must
survive every restart — one fewer thing to lose. The `license_lease` and
`device_activation` tables are left in place; the migration archives
plans, it does not drop history.

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
`transition_tenant_status`, which writes the audit event in the same
transaction as the change — a status change and its audit record can
never drift apart, because a rollback rolls back both.)

`SUSPENDED` tenants keep their data; the tablet loses write access but
the account is not destroyed. Terminating is deliberately a separate,
harder-gated action: `POST /platform/v1/tenants/{id}/status` with
`status: TERMINATED` requires `PLATFORM_SUPER_ADMIN` specifically, even
though `PLATFORM_COMMERCIAL_ADMIN` can suspend/reactivate/grace on their
own — see `app/platform/routes.py:change_tenant_status`. This is a real
authorization check, not a UI-only restriction (tested in
`test_platform_roles.py`).

## Platform roles

`PLATFORM_SUPER_ADMIN` (bypasses every specific role check),
`PLATFORM_COMMERCIAL_ADMIN` (customers, subscriptions, the price,
suspend/reactivate — not terminate, not raw restore),
`PLATFORM_SUPPORT_ADMIN` (devices, support sessions — not commercial
state), `PLATFORM_AUDITOR` (read-only everywhere; verified by attempting
a write and asserting `PLATFORM_ROLE_REQUIRED` in
`test_platform_roles.py::test_auditor_is_read_only`).
