# Sync Protocol

Implemented in `app/sync/routes.py` for one representative entity (`animal`) — the same pattern
(idempotency ledger, version-based conflict detection, tombstone soft-delete) applies unchanged
when fields/crops/inventory/Mouneh/etc. are added; only `_ENTITY_MODELS` grows.

## Push — `POST /api/v1/sync/push`

```json
{
  "changes": [
    {
      "event_id": "uuid",
      "entity_type": "animal",
      "entity_id": "uuid",
      "operation": "CREATE | UPDATE | DELETE",
      "base_version": 3,
      "payload": { "...": "..." }
    }
  ]
}
```

Requires `require_module("ANIMALS")` — tenant context, module entitlement, and (per-change)
permission are all checked before anything is written.

### Idempotency

Every change carries a client-generated `event_id`. Before applying anything, the server checks
`sync_event` (keyed by that exact UUID as primary key) for a prior record. If found, the *original*
recorded outcome is returned again (`status: "REPLAYED"`) and nothing is re-applied — a retried
push after a dropped response is a no-op, not a double-write or a double-count. Proven in
`test_sync.py::test_duplicate_sync_push_is_idempotent`, which pushes the same payload twice and
then pulls to confirm exactly one row exists at version 1.

### Conflict handling

- **CREATE** against an `entity_id` that already exists → `CONFLICT` (never silently overwritten).
- **UPDATE/DELETE** carry `base_version`; if it doesn't match the row's current `version`, the
  server returns a structured `CONFLICT` with the row's actual `current_version` rather than
  guessing or last-write-wins. The tablet decides whether to retry against the fresh version or
  surface it to the user. Proven in `test_sync.py::test_sync_conflict_is_structured`.
- **DELETE** is a tombstone (`deleted_at` set), never a row removal — consistent with
  `SyncedEntityMixin` across the farm-data plane (see ARCHITECTURE.md).
- Every change — applied, conflicted, or rejected — is recorded in `sync_event`, which doubles as
  both the idempotency ledger and a lightweight sync audit trail.

Per-change results carry one of `APPLIED | CONFLICT | REPLAYED | REJECTED`, never a single
all-or-nothing response for a batch — one bad change in a batch doesn't block the others.

Inventory-style "never overwrite a running total" handling is modeled at the schema level
(`inventory_item.quantity_on_hand` alongside a separate `inventory_movement` ledger,
`app/tenant_api/models.py`) even though the push/pull handlers in v0.1 only wire up `animal`; the
same movement-ledger pattern is what a future `INVENTORY` sync handler should use rather than
letting sync overwrite `quantity_on_hand` directly.

## Pull — `GET /api/v1/sync/pull?cursor=...`

Returns rows with `updated_at > cursor` (ISO-8601, ascending), capped at 500 per call
(`_MAX_PULL_BATCH`), filtered to the caller's farm scope, with a new cursor equal to the last
returned row's `updated_at`. **Known limitation, called out rather than hidden:** a timestamp
cursor can miss or duplicate rows that share an identical `updated_at` at the batch boundary; a
production-hardening pass should switch to a monotonic per-tenant sequence number instead. Every
returned item carries `entity_type`, `entity_id`, `version`, `deleted` (tombstone flag), and the
current payload, matching what push produces so a tablet can apply both through one code path.

## Object metadata

`app/files/models.py:FileObject` (control plane) records tenant/farm/entity linkage, storage key,
mime type, size, and uploader for every uploaded file; `POST /api/v1/files/presign-upload` issues a
short-lived presigned PUT URL (namespaced `tenants/{tenant_id}/{entity_type}/{entity_id}/{uuid}`,
never a public path) only after the caller's tenant context has been resolved and authorized.
