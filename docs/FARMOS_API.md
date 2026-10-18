# FarmOS Tablet API Contract

`app/farmos/` implements the exact REST API the Origami FarmOS tablet app calls — 97 endpoints
across 19 functional groups, reverse-engineered and verified against a real reference backend (81
examples from a demo snapshot, 11 captured live from the running app), plus 5 endpoints added since
to close gaps the captured examples left open (see "Known, deliberate gaps" below — each one moved
here once built), plus the 18 Stage 6 endpoints of the generic animal model (species configuration,
capability resolver, identifiers, animal groups, care record), the 63 Stage 7 endpoints of the
generic feed architecture (products and lots, formulas and numbered mixes, feeding programs and
events, usage policy, allocations, days of cover, reorder, reconciliation, costs) and the 65 Stage 8
endpoints of the farm pharmacy, health cases, purchase requisitions and the emergency protocol
engine including offline protocol runs, all ported from the reference backend's published OpenAPI
contract. This is a **fixed external contract**, not free-form internal API design: field
names, types, status codes, and response shapes match the app's expectations exactly, even where
that diverges from this codebase's own conventions elsewhere (see "Deliberate divergences from the
rest of this codebase" below).

Every route lives under `/api/v1`, mounted in `app/main.py`, one router module per functional
group (`app/farmos/routes_*.py`). Farm-data-plane models live in `app/farmos/*_models.py`
(`farm_models.py`, `production_models.py`, `finance_models.py`, `mouneh_models.py`,
`visits_models.py`, `livestock_models.py`, `feed_models.py`, `pharmacy_models.py`,
`health_case_models.py`, `emergency_models.py`, `procurement_models.py`) plus the pre-existing `app/tenant_api/models.py` (`Animal`, `Task`, `Field`,
`InventoryItem`/`InventoryMovement` — extended, not replaced, since `app/sync/` already depended on
them). All of it is RLS-protected exactly like the rest of the tenant data plane — see TENANCY.md.


## Modules

`GET /modules/catalog` returns every module with `license_code` and
`licensed_active`. The rule is one line
(`app/farmos/routes_employees.py`):

```
licensed_active = True
```

Always, for every customer, including one with no subscription recorded.
Origami is sold as a single subscription covering the whole product:
there are no tiers and no add-ons, so there is no module a farm has not
bought. The field stays in the response because a shipped app reads it.

`license_code` also stays, demoted from a gate to a description: it says
which part of the product a screen belongs to, and the console groups its
module list by it. Every one of the 20 modules names one — see
`app/plans/licensing_map.py`, the single source of truth. The two former
paid add-ons keep lowercase codes, `mouneh` and `visits_agritourism`,
because this contract addresses them by name in
`POST /modules/{module_code}/activate`; the rest use the platform's own
codes (`ANIMALS`, `MILK`, `SALES`, …). One code can cover several
modules: `SALES` covers sales, expenses and finance.

**What does still gate a request.** Two things, neither of them the
catalog: a suspended or terminated tenant is refused outright, and what
one person may do comes from their own membership's permission grid via
`require_permission(module, action)`. A client that ignores the catalog
can still reach a module's routes if the signed-in user's grid allows
it — that was true before this change and is unchanged by it.

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

### The login response carries the profile

`POST /auth/login` returns the signed-in person alongside the token:

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs…",
  "token_type": "bearer",
  "user": { "id": "…", "farm_id": "…", "name": "Rami", "email": "…",
            "phone": null, "role": "owner", "department": null,
            "language": "en", "active": true }
}
```

`user` is the same shape `GET /auth/me` returns, built by the same function
(`routes_auth.py:_profile_of`) so the two cannot drift apart.

It is not optional. The tablet app reads it directly — `json['user'] as Map<String, dynamic>` —
and does **not** call `/auth/me` on a fresh sign-in, only on a relaunch. Omitting it throws a Dart
type error rather than an API error, which the app's `on ApiException` handler does not catch: the
sign-in button silently does nothing while the server logs a clean `200`. That combination cost
this project the better part of a week, so `test_login_returns_the_profile_the_tablet_app_reads`
pins it.

### The login request registers the tablet

`POST /auth/login` accepts three optional fields beside the credentials:

```json
{
  "email": "…", "password": "…",
  "installation_id": "a-stable-id-for-this-install",
  "device_name": "Zahle tablet",
  "app_version": "1.4.0"
}
```

Sending `installation_id` puts the tablet on its customer's device list,
or updates the row that is already there. It replaces pairing: there is no
key to type in and nothing to generate in the console, so the list answers
*which tablets is this customer using* rather than which ones they are
permitted.

Three rules the server keeps:

- **Optional means optional.** A client that sends none of this still
  signs in. A device list is worth having; it is not worth blocking a farm
  worker's morning over.
- **A revoked device stays revoked.** Signing in on it updates
  `last_seen_at` and nothing else.
- **Recording never fails the sign-in.** The write is rolled back on any
  database error and the login proceeds.

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
| 6 | Livestock: species catalog & capability resolver, identifiers, animal groups, care record (conditions, weights) | The resolver-driven Add Animal form, group (flock/herd) subjects, and the animal's care record on the detail screen |
| 7 | Feed architecture: products & lots, formulas & numbered mixes, nutrient profiles, feeding programs/assignments/events, usage policy, allocations, days of cover/forecast/reorder, reconciliation, costs | The tablet's feed workspace, the mixing screen, the animal's feeding section and the feed-room daily plan |
| 8 | Farm pharmacy (medicines, lots, reservations, transfers, policies, alerts, administrations), health cases (diagnoses, plans), purchase requisitions, emergency protocols (authoring & veterinary approval, triage, runs, reassessment, offline package & offline runs) | The pharmacy dashboard, the emergency flow at the animal — online and offline — and the clinical half of the animal's digital twin |

Each stage's tests live in `api/tests/test_farmos_stage{1..8}[_suffix].py` and run against real
PostgreSQL, RLS included — nothing about this contract is mocked at the database layer.

Stages 6, 7 and 8 are the first three of a staged port of the reference backend's newer contract
(288 operations in its OpenAPI document). The remaining stage: feed-performance intelligence and
the platform masters (units of measure, assets, notification routes, scheduled jobs).

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
| Livestock (generic animal model) | `routes_livestock.py` | `GET /species`, `GET /species/{code}/configuration`, `GET /capabilities`, `POST /animal-capabilities/resolve`, `GET /animals/{id}/capabilities`, `GET/POST /animals/{id}/identifiers`, `DELETE /animals/{id}/identifiers/{id}`, `GET/POST /animal-groups`, `GET/PATCH /animal-groups/{id}`, `GET /animal-groups/{id}/capabilities`, `GET/POST /animals/{id}/conditions`, `DELETE /animals/{id}/conditions/{id}`, `GET/POST /animals/{id}/weights` |
| Animal health | `routes_health.py` | `GET/POST /health/treatments` — a treatment may name the stocked medicine (`medicine_product_id`), its health case and treatment plan |
| Health cases | `routes_health_cases.py` (shapes in `emergency_schemas.py`) | `GET/POST /health/cases`, `GET/PATCH /health/cases/{id}`, `POST …/close`, `POST …/diagnoses`, `POST …/plans` |
| Observations | `routes_observations.py` | `GET/POST /observations` — a new observation joins the subject's open case; a severe one starts emergency triage |
| Pharmacy | `routes_pharmacy.py` (shapes in `pharmacy_schemas.py`) | `GET /pharmacy/summary`, `POST /pharmacy/evaluate`, `GET /pharmacy/{categories,ingredients}`, `GET/POST /pharmacy/medicines`, `GET/PATCH /pharmacy/medicines/{id}`, `GET /pharmacy/lots`, `POST /pharmacy/lots/receive`, `PATCH /pharmacy/lots/{id}/{status,storage}`, `POST /pharmacy/lots/{id}/open`, `POST /pharmacy/{adjustments,transfers}`, `GET/POST /pharmacy/reservations`, `POST …/reservations/{id}/release`, `GET /pharmacy/policies`, `PUT /pharmacy/policies/{item_id}`, `GET /pharmacy/alerts`, `POST /pharmacy/alerts/{id}/{acknowledge,resolve,requisition}`, `GET/POST /pharmacy/administrations`, `POST …/administrations/{id}/reverse` |
| Procurement | `routes_procurement.py` | `GET/POST /procurement/requisitions`, `GET /procurement/requisitions/{id}`, `POST …/{submit,approve,reject,cancel}` |
| Emergency protocols | `routes_emergency.py` (shapes in `emergency_schemas.py`) | `GET /emergency/{summary,sign-codes,offline-package}`, `POST /emergency/offline-runs`, `POST /emergency/reassessments/check`, `GET/POST /emergency/protocols`, `GET /emergency/protocols/{id}`, `POST …/versions`, `POST …/versions/{v}/{approve,withdraw}`, `GET/POST /emergency/assessments`, `GET /emergency/assessments/{id}`, `POST …/{start,close}`, `GET /emergency/runs`, `GET /emergency/runs/{id}`, `POST …/steps/{s}/{prepare,confirm,skip}`, `POST …/{reassess,escalate,resolve}` |
| Feed & inventory (item level) | `routes_feed.py` | `GET/POST /feed/items`, `GET/POST /feed/transactions` — feed items are lot-tracked behind the scenes |
| Feeding (generic feed architecture) | `routes_feeding.py` (shapes in `feed_schemas.py`) | `GET/POST /feed-products`, `GET/PATCH /feed-products/{id}`, `GET/PUT /feed-products/{id}/usage-policy`, `POST …/usage-policy/validate`, `GET /feed-lots`, `POST /feed-lots/receive`, `PATCH /feed-lots/{id}/status`, `GET /feed-lots/{id}/trace`, `POST /feed-inventory/adjustments`, `GET/POST /feed-formulas`, `GET /feed-formulas/{id}`, `POST …/versions`, `GET …/versions/{v}`, `POST …/versions/{v}/activate`, `GET …/versions/{v}/scale`, `GET …/versions/{v}/nutrients`, `GET/POST /feed-batches`, `GET /feed-batches/{id}`, `GET …/usage`, `POST …/complete`, `POST …/quarantine`, `GET/PUT /feed-mix-settings`, `GET /feed-mixes/{n}`, `GET /feed-nutrients`, `GET/POST /feed-nutrient-profiles`, `GET/POST /feeding-programs`, `GET /feeding-programs/{id}`, `POST …/versions`, `POST …/versions/{v}/activate`, `POST /feeding-programs/resolve`, `GET /livestock-subjects/{id}/feeding-plan`, `GET/POST …/feeding-assignments`, `DELETE …/feeding-assignments/{id}`, `GET …/feeding-history`, `GET/POST /feeding-events`, `POST /feeding-events/{id}/reverse`, `GET /feeding-plan/today`, `GET /feed-inventory/{availability,days-of-cover,forecast,reorder-recommendations}`, `POST /feed-inventory/reorder-recommendations/{id}/acknowledge`, `GET/PUT /feed-reorder-policies`, `GET/POST /feed-allocations`, `POST /feed-allocations/{id}/{release,transfer}`, `GET/POST /feed-reconciliations`, `GET /feed-reconciliations/{id}`, `POST …/close`, `GET /feed-costs` |
| Production | `routes_production.py` | `GET/POST /production/{eggs,harvest,milk}`, `GET /production/fields` |
| Agriculture | `routes_agriculture.py` | `GET/POST /crop-plantings`, `GET/POST/DELETE /crops`, `POST/PATCH /fields`, `POST /harvest` |
| Sales & finance | `routes_finance.py` | `GET/POST /expenses`, `GET/POST /sales` |
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
  /health/treatments` (the only place a diagnosis is recordable) and `POST`/`DELETE
  /animals/{id}/conditions` (a standing allergy, adverse reaction, chronic condition or
  contraindication) are gated to a veterinarian or the farm owner/manager
  (`app/farmos/deps.py:require_diagnostic_role`). A condition is ended, never deleted.
- **Generic animal model — one `Animal`, no per-species tables.** Species is a code from the
  catalog in `app/farmos/livestock_catalog.py` (`cow`, `sheep`, `goat`, `horse`, `layer_hen`,
  `duck`, `turkey`, `other`), validated on every write with a 422 that lists the known codes.
  What an animal can do is resolved by `app/farmos/capability_service.py` from species + sex +
  life stage + management profile: configuration first (the most specific granting rule wins, a
  disabling rule is a veto — "broiler flocks do not lay"), biology last (a male is never pregnant,
  a hen never in calf, a ewe never lays — not configurable). `POST /animal-capabilities/resolve`
  returns the same answer the write endpoints enforce, so the tablet's form never offers something
  the server refuses. `pregnant`/`lactating` are refused (422) when the resolved set lacks
  `PREGNANCY`/`LACTATION`; a flag merely restated by an edit form is not re-judged, so an animal
  registered before the resolver existed is not refused for a profile it never had.
- **Identifiers are rows, not a column.** `animal_identifier` holds every ear tag, RFID,
  microchip, leg band, passport, farm number or name an animal carries; `animal.tag` only mirrors
  the primary one's value for lists and search. The resolver decides which types a species may
  carry and which it must (a horse must have a microchip, cattle an ear tag); uniqueness is per
  farm + type + issuing authority (409 names the other animal). Retiring keeps the row with
  `status: retired` so a record naming the old tag still resolves; the last required identifier
  cannot be retired (422); editing `tag` through `PUT /animals/{id}` retires the old primary and
  attaches a new one. An animal that predates identifiers (seeded, synced, or registered by an
  older client with only `tag`) has that tag materialised as its primary identifier the first time
  its record is read — inside the tenant-scoped request, because under `FORCE ROW LEVEL SECURITY`
  a migration cannot see any tenant's rows to backfill them.
- **Species cannot change once history exists.** `PUT /animals/{id}` with a different `species`
  is 409 once the animal has a milk, treatment or observation record.
- **Production writes respect capabilities.** `POST /production/milk` is 422 for an animal whose
  resolved set suppresses `MILK_PRODUCTION` by biology (a bull, a hen) and, for an animal that
  carries a management profile, when the profile does not grant it (a meat cow); an animal with no
  profile on record is held to biology only. `POST /production/eggs` applies the same rule to
  `EGG_PRODUCTION` when `flock_id` is a registered animal group; a free-text flock label is
  accepted as before.
- **Weights are a dated ledger.** `POST /animals/{id}/weights` (Animals edit *or* Animal Health
  create) appends a dated, methodised record; `animal.weight_kg` becomes the most recent by
  `measured_at`, so a back-dated entry is kept in history without overwriting the latest. A weight
  sent on `POST`/`PUT /animals` goes through the same ledger (`source_type: animal_update`).
- **Feed stock is the ledger; the balance is derived.** Every feed product owns one `inventory_item`
  and every kilogram that moves is an `inventory_movement` naming its `feed_lot` (purchase,
  production, opening balance, mixing, feeding, waste, return, adjustment). `inventory_item.current_qty`
  and `feed_lot.quantity_on_hand` are maintained from those rows and never edited; availability =
  on hand − quarantined/blocked/recalled/expired lots − stock reserved for another purpose. An
  `out` is issued FIFO from usable, unreserved lots; a shortfall is a 422 that shows the arithmetic
  unless `allow_negative` is passed, and then the uncovered quantity is recorded against no lot.
  Adjustments need an explanation; an unexplained difference stays a reconciliation variance.
- **Five feed concepts, never one table** (`app/farmos/feed_models.py`): product, formula (with
  immutable versions), batch, feeding program (with versions and applicability rules) and feeding
  event. A formula version is locked the moment a completed batch references it; a recipe change
  is a new version. A feeding event is immutable; a mistake is a reversal event that points at the
  original and puts the stock back on the lots it left.
- **Every mix is a numbered mix (FEED-SCHEMA §20).** `POST /feed-batches` takes the farm's next
  number from one row-locked sequence per tenant (`feed_mix_sequence`), whatever the mix is intended
  for; the number is never reused or renumbered, refused starts consume none, and an
  `Idempotency-Key` replay returns the same mix. `intended_species_code` / `intended_management_profile`
  are catalog references validated against every component's usage policy before the number is
  taken; `operator_id` must be an active member of the farm (checked against the control database),
  the mixer is a farm asset (none can be named until the platform stage lands, so `mixer_asset_id`
  is 404 today). Completing a batch consumes the actual weighed quantities, costs the mix from the
  lots it drew, and receives the output as a `farm_produced` lot whose code is the mix code;
  `GET /feed-mixes/{n}` and `GET /feed-batches/{id}/usage` read the mix's whole life — ingredients
  and their lots, output lot, every dated issue with the feeding program and assignment it was made
  under, waste and corrections, and the ledger-derived remaining quantity. `PUT /feed-mix-settings`
  (Feed & Nutrition *configure*) changes only the display format of future codes.
- **Usage policy is enforced at every door.** A product (or one lot) carries allow / block / limit
  rules over species, profile, stage, reproductive state and age, an inclusion cap and
  `requires_approved_formula`; the same evaluation runs when a formula component is added, a batch
  is started or completed, a program is activated or assigned, and a feeding is recorded. A
  lot-level policy may tighten but never broaden the product's. `POST …/usage-policy/validate`
  returns the decision with its reasons.
- **Programs resolve, assignments decide, reviews never reassign.** `POST /feeding-programs/resolve`
  matches a real or described subject against every active program version's rules (most specific
  rule wins, priority breaks ties, an unknown metric is a warning, never a silent match). A group's
  explicit assignment is inherited by every animal in it (same farm, same species, same
  `group_name`) that has none of its own; inheritance is computed, never stored, so an individual
  supplement, override or restriction sits on top of the group program. A restriction needs Feed &
  Nutrition *approve* or Animal Health *create*. A change the resolver reads (`PUT /animals/{id}`
  sex/stage/profile/pregnant/lactating/group/weight/birth date, `PATCH /animal-groups/{id}` count/
  stage/profile/composition, a milk record when a program has a production band) opens one
  `feeding_review` task per subject and changes nothing else; an explicit assignment closes it.
- **Days of cover, reorder and reconciliation are read from the same ledger.** Demand comes from
  the active programs and head counts (farm-produced demand expands into its formula's ingredients)
  or, failing that, from the last 14 days of consumption, and the answer says which. A breach of
  the reorder policy or of lead time + margin is a recommendation that names the programs at risk;
  acknowledging it opens a `feed_reorder` task and suppresses the recommendation while the task is
  open. A reconciliation categorises every movement in the period and closes only with an
  explained `reconciliation_adjustment` — never as consumption or waste.
- **CONSTITUTION.md: never generate a recommendation without persisted evidence.**
  `GET /recommendations` (default `refresh=true`) re-evaluates real rules against this farm's real
  stored data before returning anything — `app/farmos/recommendations.py` implements
  `RULE-FEED-COST-INSIGHT` (today's feed-expense share vs. a 35% threshold), `RULE-HARVEST-DUE`
  (active crop plantings due within 48h), `RULE-LOW-FEED` (an inventory item at or below its own
  `reorder_level`), and `RULE-EGG-DROP` (a flock's sellable-egg output down more than 20% versus
  the preceding week); a refresh skips re-creating a rule+entity pair that already has an undecided
  row rather than spamming duplicates. Every rule goes through the same `_add_recommendation()`
  helper, which also raises a paired `Notification` row — the only place in this codebase a
  `Notification` gets created, so `GET /notifications` and the notification half of `GET
  /priorities` now reflect real, rule-evaluated farm state instead of only test-seeded rows.

- **The pharmacy's eligible stock is not its on-hand stock.** Every medicine is an inventory item
  with lots (`inventory_lot`); a lot's on-hand moves only with a posted `inventory_movement`, and
  eligibility is read from lot facts: on hand − quarantined / blocked / recalled − expired or past
  its after-opening use-by − under a storage or cold-chain exception − reserved for another
  purpose. Every receipt records the manufacturer's lot and the expiry unless the product carries a
  recorded `lot_tracking_exception`. Taking a lot out of use needs a reason; a person clears a
  storage exception; an adjustment needs an explanation and never goes below zero; a transfer is a
  paired OUT / IN under one `correlation_id` and leaves on-hand unchanged.
- **Stock alerts are logistics findings, never treatment advice.** The farm's own policy
  (`PUT /pharmacy/policies/{item}`: essential, minimum, critical, target, days of cover, expiry
  warning) drives OK / LOW / CRITICAL / OUT; one open alert per condition, refreshed by every
  re-evaluation (each receipt, dose, adjustment, status change and `POST /pharmacy/evaluate`),
  resolved when the condition clears, superseded when the policy changes. `recommended_reorder =
  max(0, target − eligible − confirmed incoming)` rounded up to the pack; an alert opens at most one
  DRAFT purchase requisition, with a task for the buyer, and never a purchase order.
- **Stocking a medicine never authorises its use.** `POST /pharmacy/administrations` needs Animal
  Health *create*; a prescription-only product needs an active treatment of that product (or an
  approved protocol step); the route and species must be ones the product is authorised for; the
  dose comes from the exact lot named or first-expiry-first-out among eligible lots, never an
  ineligible one; the withdrawal windows come from the product's `withdrawal_rules` and land on the
  animal (and every animal of a group); a reversal puts the quantity back on the same lot and leaves
  the withdrawal standing.
- **Observation is not diagnosis, diagnosis is not treatment.** A health case (`HC-000001`) is the
  episode; a diagnosis (with a certainty, superseded never overwritten) and a treatment plan are
  recorded only by a diagnostic role; a treatment under a plan activates it; closing a case needs an
  outcome and resolves its diagnoses and plans. Observations of the last day join a new case and
  later ones land on the open case by themselves.
- **A purchase requisition is a document, not a purchase.** Draft → submitted → approved / rejected
  (by a manager, the owner or someone with Inventory *approve*; the authority is snapshotted on the
  decision) → received when a goods receipt names it. Submitted and approved lines are *confirmed
  incoming* for the pharmacy's arithmetic; a draft nobody acted on is withdrawn with the shortage.
- **The emergency engine never invents a drug, dose or route.** A protocol version is operational
  only after a veterinarian approves it with a reference (a manager drafts and withdraws; the
  approval is written once); every medication instruction lives on an approved version. Triage
  (`POST /emergency/assessments`) scores the signs — reported ones plus the last day's observations —
  against every current version for the species and subject scope, checks the animal's facts
  (species, sex, stage, weight and its age, pregnancy, lactation, withdrawal, recent medication,
  open diagnoses, recorded allergies and conditions) against the eligibility rules and explains the
  match; a danger sign, a required sign missing, a BLOCK / REQUIRE_VET rule, a score under the
  version's confidence or no applicable version escalates instead: a task to the routed
  veterinarian / manager, a bell entry, the subject's health case opened or joined.
- **A run executes the approved steps, nothing else.** Starting a run opens or joins the health
  case and notifies the manager and veterinarian as the version requires. A medication step is
  prepared from the approved rule and the animal's authoritative weight (entered now and recorded,
  or the latest dated record, refused when older than the protocol allows); a dose outside the
  approved range, a recorded allergy to the product or its ingredients, a repeat too soon or no
  eligible lot blocks the step and escalates the run. Confirming it records a prescription-provenance
  treatment under the run's plan and administers the exact lot through the pharmacy. Reassessment
  re-scores the same version: a danger sign or no improvement escalates; improvement continues; a
  withdrawn version cannot start and escalates the matched case. Escalations are kept even when the
  request is answered with the refusal.
- **Offline execution is recorded as it happened and revalidated at sync.** `GET
  /emergency/offline-package` ships only offline-eligible approved versions, signed by content, with
  the medicine mapping and an eligible-stock snapshot. `POST /emergency/offline-runs` (idempotent on
  `operation_id`) re-runs triage pinned to the version the tablet used, records what the tablet
  showed and did with device, actor and occurrence times, and lists every difference — a stale
  package, a version no longer current or never offline-eligible, a match the server does not
  confirm, a step or lot the current rules refuse — as discrepancies that escalate; nothing is
  silently rewritten.

## Known, deliberate gaps

- **`POST /expenses` and `POST /sales` field shapes are inferred, not captured.** The 81+11
  reference examples never captured a manual expense/sale entry, so these two endpoints (added to
  close the "no write path" gap noted below) mirror `ExpenseOut`/`SaleOut`'s existing fields rather
  than a verified real request. `Sale` rows also still arrive via `POST /mouneh/sales` and `POST
  /visit-retail-sales` as before; the general manual entry point now exists alongside those.
- **`GET /feed/items`'s new `POST` sibling, `GET /feed/transactions`, and `GET /observations` are
  the same situation** — built to close a real store-without-display or no-write-path gap using the
  same field names as their existing `Out` schemas, but not verified against a captured example.
- **Audit instrumentation is representative, not exhaustive.** `app/audit/service.py`'s
  `record_audit_event` was extended with FarmOS-specific columns (`module_code`, `summary`,
  `changes_json`, `metadata_json`, `device`) and wired into employee CRUD, animal move/update, and
  treatment creation — enough to prove `GET /audit` works end-to-end against real writes. Most other
  Stage 1-3 mutations do not yet call it.
- **No PATCH/cancel/check-in on `VisitBooking`.** The captured contract has Create and Confirm only;
  `checked_in_at`/`completed_at`/`cancelled_at` exist on the model and wire shape (matching real
  example responses) but nothing in this API surface sets them yet.
- **The livestock catalog is code-defined, the same for every farm.** Species, breeds, life
  stages, management profiles, capabilities and their rules (`app/farmos/livestock_catalog.py`)
  are served from code rather than tenant tables; breed and rule ids are stable UUID5 values so
  the tablet can cache them. Nothing in the tablet contract edits the catalog, so this matches the
  reference backend's behaviour; a per-farm override layer (a farm's own breeds, a disabled
  capability) would be a tenant table on top of this, not a change to the resolver.
- **Feed reference data is code-defined.** The nutrient catalog and the unit-of-measure table with
  its conversions live in `app/farmos/feed_catalog.py`, the same for every farm, like the livestock
  catalog; `GET /feed-nutrients` serves it and `POST /feed-nutrient-profiles` rejects a code outside
  it. Farm-defined nutrients and units arrive with the platform stage (`/units-of-measure`).
- **Feed signals are not yet on the notification bell.** Reorder recommendations and reconciliation
  variances are read from `GET /feed-inventory/reorder-recommendations` and
  `GET /feed-reconciliations`; the reference backend also surfaces them as `feed_reorder_required` /
  `feed_variance` notifications, which this port will add with the platform stage's signal routing.
  Feed-performance scoring is absent too: `GET /feed-mixes/{n}` returns `"performance": null` and
  the feed-performance endpoints are not mounted until that stage.
- **Suppliers, locations and mixer assets are ids without a master yet.** `supplier_id` on feed
  and medicine lots and `preferred_supplier_id` on policies and requisition lines are stored as
  given (`supplier_label` carries the name meanwhile); a pharmacy `location_id` is an opaque code
  the farm chooses (per-location balances, transfers and policies work on it, nothing validates it
  against a locations master); `mixer_asset_id` is refused with 404 until `/assets` exists — a
  free-text mixer name is never accepted. All three masters arrive with the platform stage.
- **Notification routing uses the built-in defaults.** Escalations go to the veterinarian (then
  managers), run notifications to the roles the version names, draft requisitions to a manager or
  the owner; a farm's own `notification_routes` configuration arrives with the platform stage.
  Pharmacy alerts and emergency escalations do reach the farm-wide bell (`GET /notifications`) and
  leave it when acknowledged, resolved or closed.
- **The scheduled scans are endpoints, not a scheduler.** `POST /pharmacy/evaluate` (the daily
  pharmacy scan) and `POST /emergency/reassessments/check` (overdue reassessments) are idempotent
  and safe to call from any scheduler; this repository does not yet run one.
- **Pharmacy reference data is code-defined.** Medicine categories, the built-in active-ingredient
  list and the unit-of-measure catalogue (`app/farmos/pharmacy_catalog.py`,
  `app/farmos/feed_catalog.py`) are the same for every farm; a farm may use any ingredient code
  (its name travels with the product) but a unit a document names must exist in the catalogue.

## Testing

```bash
cd api
python -m pytest -q tests/test_farmos_stage1.py tests/test_farmos_stage2.py \
  tests/test_farmos_stage3.py tests/test_farmos_stage4.py \
  tests/test_farmos_stage5_mouneh.py tests/test_farmos_stage5_visits.py \
  tests/test_farmos_stage6_livestock.py tests/test_farmos_stage7_feed.py \
  tests/test_farmos_stage8_clinical.py
```

Every test runs against real `origami_control_test` / `origami_tenant_shared_test` databases (RLS
included) via the `client`/`control_db` fixtures in `tests/conftest.py`; `farmos_login`/
`farmos_headers` there are the FarmOS-specific equivalents of the platform's own
`dev_login`/`auth_headers`. `tests/helpers.py:add_farmos_user` creates a password-enabled employee
with an explicit permission grid (or `role="owner"`/`"manager"` for `full_access`).
