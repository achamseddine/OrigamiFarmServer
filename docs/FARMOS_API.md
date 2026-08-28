# FarmOS Tablet API Contract

`app/farmos/` implements the exact REST API the Origami FarmOS tablet app calls — 92 endpoints
across 19 functional groups, reverse-engineered and verified against a real reference backend (81
examples from a demo snapshot, 11 captured live from the running app). This is a **fixed external
contract**, not free-form internal API design: field names, types, status codes, and response
shapes match the app's expectations exactly, even where that diverges from this codebase's own
conventions elsewhere (see "Deliberate divergences from the rest of this codebase" below).

Every route lives under `/api/v1`, mounted in `app/main.py`, one router module per functional
group (`app/farmos/routes_*.py`). Farm-data-plane models live in `app/farmos/*_models.py`
(`farm_models.py`, `production_models.py`, `finance_models.py`, `mouneh_models.py`,
`visits_models.py`) plus the pre-existing `app/tenant_api/models.py` (`Animal`, `Task`, `Field`,
`InventoryItem`/`InventoryMovement` — extended, not replaced, since `app/sync/` already depended on
them). All of it is RLS-protected exactly like the rest of the tenant data plane — see TENANCY.md.

## Four things worth knowing before touching this code

**`GET /me/access` drives the whole UI.** Navigation and every Add/Edit/Delete button in the app is
shown or hidden by this one response — not a role name, a many-to-many permission grid: one row per
user × module, eight independent action flags (`view`/`create`/`edit`/`delete`/`approve`/`export`/
`assign`/`configure`) across the 20 fixed module codes in `app/farmos/permissions.py:MODULE_CODES`.
Owners and managers always get `full_access: true` (every flag, every module) computed from
`TenantMembership.role`, never stored — see `app/farmos/deps.py:is_full_access_role` — so a farm can
never lock itself out by misconfiguring a permission row. The server enforces the identical rules on
every write route via `require_permission(module_code, action)`; the `/me/access` response is what
the app draws, never the security boundary itself.

**`GET /health` is unauthenticated, at the server root, and cheap.** Not under `/api/v1` — that
prefixed path (`/api/v1` + the `animal_health` module's own routes) is a different thing entirely.
The tablet app pings root `/health` to decide whether it's online at all; it must never require a
token and must never do real work (see `app/main.py`).

**Idempotency is mandatory for offline safety.** The tablet queues writes while offline and retries
them once connectivity returns; a retry must never re-run a purchase, a treatment, or a sale twice.
Any mutating request may carry an `Idempotency-Key` header — `app/farmos/idempotency.py` stores the
first successful (2xx) response against `(key, user)` and replays it verbatim on any later request
with the same key, without re-running the handler. A key is remembered only after success: a retry
of something that was rejected (permission denied, validation failure) gets a fresh attempt, since
whatever blocked it may no longer apply. `POST /visit-bookings` additionally accepts a *body-level*
`idempotency_key` field — a second, independent mechanism for that one endpoint's own offline-safety
needs, unrelated to the header (see `app/farmos/routes_visit_bookings.py`).

**`detail` is shown to the farmer verbatim.** Every error response is `{"detail": "..."}` (see
"Deliberate divergences" below) and that string reaches the tablet screen as-is — write it for
someone standing in a barn deciding what to do next, not for a log line. Compare
`"Please sign in again."` (`app/farmos/deps.py`) against this codebase's usual
`{"error": {"code": "AUTH_INVALID_TOKEN", "message": "..."}}` shape (API_ERROR_CODES.md) — the
FarmOS contract's `detail` string is the only thing a human ever sees.

## Auth: a second, independent system

The FarmOS tablet app authenticates with `POST /auth/login` (email + password) and a bearer JWT —
deliberately **not** the OIDC/dev-login system the rest of this codebase (`app/auth/`, platform
admin, admin-web) uses. The two systems share only the `UserIdentity` table (a `password_hash`
column was added for this purpose); a FarmOS employee has no session in the platform's own auth
system and vice versa. See `app/farmos/security.py` (bcrypt hashing, HS256 JWT issuance/decoding)
and `app/farmos/deps.py:get_access_context` (the single dependency every FarmOS route ultimately
depends on — decodes the token, loads the membership, checks tenant/membership status, builds the
`AccessContext` every handler receives).

`AccessContext.tenant_id` is serialized on the wire as `farm_id` everywhere — see the next section.

## `farm_id` = `Tenant.id`

The contract's `farm_id` concept is this codebase's `Tenant.id`, spelled the app's way — a
deliberate architectural mapping, not a new entity. Every response that includes `farm_id` sets it
to `str(access.tenant_id)`; every request that accepts a client-supplied `farm_id` (query or body)
is validated against the caller's *own* tenant via `app/farmos/deps.py:check_farm_id`, which raises
**404, not 403**, on a mismatch — so a client can't use a wrong `farm_id` to enumerate other farms'
existence. `farm_id` is never trusted as authorization; it's checked, never branched on.

One collision this creates: `SyncedEntityMixin` (used by every farm-data-plane table) already has
its *own* `farm_id` column — an unrelated, still-mostly-unused multi-farm-within-a-tenant concept
from the generic sync protocol (`app/sync/`). Every response schema that has a wire `farm_id` field
is built by an explicit converter function (`_to_animal_out`, `to_task_out`, etc.) rather than
Pydantic's `from_attributes`, specifically so the wire value is never accidentally read off that
other column. If you add a new farm-data-plane entity with a wire-facing `farm_id`, follow the same
pattern — don't rely on attribute-name auto-mapping.

## Deliberate divergences from the rest of this codebase

- **Error shape**: raw `HTTPException(status_code=..., detail="...")` → `{"detail": "..."}`, not
  `AppError` → `{"error": {"code", "message"}}` (API_ERROR_CODES.md). This is the one place in the
  codebase that intentionally does *not* use the shared error convention, because the external
  contract fixes the shape and `detail` is user-facing copy, not a machine-parsed code.
- **A second auth system** (above), sharing only `UserIdentity`.
- **Permission grid, not role checks**, for authorization (though a few endpoints layer a role check
  *on top of* the grid — see "Business rules" below).

Everything else — RLS, `TenantDataRouter`, soft-delete via `deleted_at`, optimistic concurrency via
`version`, the control/tenant two-database split — is shared with the rest of the platform.

## Build stages

The contract was implemented in the order the reference app actually needs it, so the tablet could
render end-to-end as early as possible rather than needing all 92 endpoints at once:

| Stage | Groups | What it unlocks |
|---|---|---|
| 1 | Auth, Farm, Employees (`/me/access`, `/modules/catalog`) | The app can log in and render navigation |
| 2 | Animals, Tasks, Notifications, Priorities, Reports (`/morning-briefing`) | The daily-use home screen and animal registry |
| 3 | Animal Health, Observations, Feed, Production, Agriculture | Recording actual farm work |
| 4 | Employees (admin CRUD), Sales/Expenses (read), Audit, Recommendations, Reports (`/daily-summary`) | Management and oversight |
| 5 | Module licensing, Mouneh, Farm Visits | Licensed add-ons — hidden entirely by the app when inactive, safe to build last |

Each stage's tests live in `api/tests/test_farmos_stage{1..5}[_suffix].py` and run against real
PostgreSQL, RLS included — nothing about this contract is mocked at the database layer.

## Endpoint inventory by group

Field-level detail (types, defaults, nullability) lives in `app/farmos/schemas.py`, one Pydantic
model per request/response shape, named to match the contract's own schema names. This table is
the routing map — which router module owns which path.

| Group | Router module | Representative endpoints |
|---|---|---|
| Auth | `routes_auth.py` | `POST /auth/login`, `GET /auth/me` |
| Employees (self) | `routes_employees.py` | `GET /me/access`, `GET /modules/catalog` |
| Employees (admin) | `routes_employees_admin.py` | `GET/POST/PATCH/DELETE /employees`, `PUT /employees/{id}/permissions` |
| Farm | `routes_farms.py` | `GET /farms/me` |
| Animals | `routes_animals.py` | `GET/POST /animals`, `GET/PATCH/PUT /animals/{id}` |
| Animal health | `routes_health.py` | `GET/POST /health/treatments` |
| Observations | `routes_observations.py` | `POST /observations` |
| Feed & inventory | `routes_feed.py` | `GET /feed/items`, `POST /feed/transactions` |
| Production | `routes_production.py` | `GET/POST /production/{eggs,harvest,milk}`, `GET /production/fields` |
| Agriculture | `routes_agriculture.py` | `GET/POST /crop-plantings`, `GET/POST/DELETE /crops`, `POST/PATCH /fields`, `POST /harvest` |
| Sales & finance | `routes_finance.py` | `GET /expenses`, `GET /sales` (both read-only — see below) |
| Tasks | `routes_tasks.py` | `GET/POST/PATCH/DELETE /tasks` |
| Notifications | `routes_notifications.py` | `GET /notifications`, `POST /notifications/{id}/read`, `POST /notifications/read-all` |
| Priorities & audit | `routes_priorities.py`, `routes_audit.py` | `GET /priorities`, `GET /audit` |
| AI recommendations | `routes_recommendations.py` | `GET /recommendations`, `PATCH /recommendations/{id}/decision` |
| Reports | `routes_reports.py` | `GET /morning-briefing`, `GET /reports/daily-summary` |
| Module licensing | `routes_modules.py` | `GET /modules`, `POST /modules/{code}/activate` |
| Mouneh production | `routes_mouneh.py` | raw materials, products, recipes, batches, finished goods, sales |
| Farm visits | `routes_visits.py`, `routes_visit_bookings.py` | activities, packages, visitors, sessions, calendar, bookings, staff roster, incidents, feedback, retail sales |

## Business rules with an explicit rule ID

The reference spec cites several rules by ID in endpoint descriptions; each is implemented exactly
where cited, not as a generic framework:

- **RULE-WITHDRAWAL** — an animal's `withdrawal_until` (set by `POST /health/treatments` when it
  includes one) hard-blocks `POST /production/milk` from destination `"sale"` while active; other
  destinations succeed with `under_withdrawal_warning: true` (`app/farmos/routes_production.py`).
- **RULE-MOU-005** — `POST /mouneh/batches/{id}/complete` auto-fills any recipe material never
  explicitly `/consume`'d at its planned (recipe) quantity, so a manager who skips the granular step
  still gets correct stock and cost (`app/farmos/mouneh_service.py`).
- **RULE-VIS-002** — `POST /visit-bookings/{id}/confirm` is rejected if it would push a session's
  total party size over capacity.
- **RULE-VIS-006** — `POST /visit-retail-sales` deducts real stock (Mouneh finished goods or plain
  inventory) and creates a core `Sale` row so the purchase flows into Sales & Finance.
- **RULE-VIS-009** — `PATCH /visit-sessions/{id}` lets a manager close a session by setting
  `status`.
- **RULE-VIS-010** — visitor PII (`GET`/`POST /visitors`) is gated beyond the normal permission grid
  to owner/manager/`visitor_coordinator` only (`app/farmos/deps.py:require_visitor_access`) —
  nobody else gets a route to it, permission grid or not.
- **Constitution: "Workers record observations; workers do not diagnose."** `POST /observations`
  has no diagnostic-role gate and its schema structurally has no `diagnosis` field; `POST
  /health/treatments` (the only place a diagnosis is recordable) is gated to a veterinarian or the
  farm owner/manager (`app/farmos/deps.py:require_diagnostic_role`).
- **CONSTITUTION.md: never generate a recommendation without persisted evidence.**
  `GET /recommendations` (default `refresh=true`) re-evaluates real rules against this farm's real
  stored data before returning anything — `app/farmos/recommendations.py` implements
  `RULE-FEED-COST-INSIGHT` (today's feed-expense share vs. a 35% threshold) and `RULE-HARVEST-DUE`
  (active crop plantings due within 48h); a refresh skips re-creating a rule+entity pair that
  already has an undecided row rather than spamming duplicates.

## Known, deliberate gaps

- **`GET /expenses` and `GET /sales` are read-only.** The captured contract has no matching POST
  endpoint for either — `Expense` rows have no write path yet in this codebase (`app/farmos/
  finance_models.py` documents this); `Sale` rows are written only by `POST /mouneh/sales` and
  `POST /visit-retail-sales`, per the contract's own note that a general manual sale-entry endpoint
  is tracked as follow-on work, not yet built.
- **Audit instrumentation is representative, not exhaustive.** `app/audit/service.py`'s
  `record_audit_event` was extended with FarmOS-specific columns (`module_code`, `summary`,
  `changes_json`, `metadata_json`, `device`) and wired into employee CRUD, animal move/update, and
  treatment creation — enough to prove `GET /audit` works end-to-end against real writes. Most other
  Stage 1-3 mutations do not yet call it.
- **No PATCH/cancel/check-in on `VisitBooking`.** The captured contract has Create and Confirm only;
  `checked_in_at`/`completed_at`/`cancelled_at` exist on the model and wire shape (matching real
  example responses) but nothing in this API surface sets them yet.

## Testing

```bash
cd api
python -m pytest -q tests/test_farmos_stage1.py tests/test_farmos_stage2.py \
  tests/test_farmos_stage3.py tests/test_farmos_stage4.py \
  tests/test_farmos_stage5_mouneh.py tests/test_farmos_stage5_visits.py
```

Every test runs against real `origami_control_test` / `origami_tenant_shared_test` databases (RLS
included) via the `client`/`control_db` fixtures in `tests/conftest.py`; `farmos_login`/
`farmos_headers` there are the FarmOS-specific equivalents of the platform's own
`dev_login`/`auth_headers`. `tests/helpers.py:add_farmos_user` creates a password-enabled employee
with an explicit permission grid (or `role="owner"`/`"manager"` for `full_access`).
