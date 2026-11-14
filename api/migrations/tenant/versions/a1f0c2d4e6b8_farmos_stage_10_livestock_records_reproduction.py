"""FarmOS stage 10: livestock records (state history, group membership, head-count ledger, parentage), reproduction and lactation, farm settings

Revision ID: a1f0c2d4e6b8
Revises: 8de412824ec8
Create Date: 2026-11-14 09:12:40.000000

Ports the reference backend's b5e7a9c1d3f8 (livestock history and the
reproduction domain), a8d0c2e4f6b1 (dry-off setting) and c3e5a7b9d1f4
(calendar settings, rule groups) to the multi-tenant server.

New tables (all RLS-protected, tenant_id from the row they describe)
- animal_state_history (LIVESTOCK-SCHEMA §5): effective-dated STATUS,
  LIFE_STAGE, MANAGEMENT_PROFILE and LOCATION.
- animal_group_membership (§7), animal_group_quantity_event (§9),
  animal_relationship (§10).
- breeding_event, pregnancy_episode, pregnancy_assessment, birth_event,
  birth_offspring, incubation_batch, hatch_event (REPRODUCTION-SCHEMA);
  lactation_episode (PRODUCTION-SCHEMA §8).
- farm_settings: one row per tenant — dry_off_days_before_birth (60) and
  the calendar milk_peak_start_days (40), milk_peak_end_days (70),
  breeding_wait_days (50), heat_cycle_days (21). The server has no
  tenant-side farm row, so the reference's `farms` columns live here.

Column additions
- animal: lifecycle_status, exited_at, pregnancy_started_at,
  expected_due_date, lactation_started_at, dried_off_at — projections,
  written only by the domain services.
- animal_group: tracking_mode, membership_rule.

Backfill — existing flags become the authoritative records they stood for:
- every animal gets opening state-history rows (status, stage, profile,
  location) dated from its registration; an inactive animal is 'archived';
- an existing group with individually registered members becomes HYBRID,
  otherwise QUANTITY_MANAGED, and its count becomes an INITIAL_COUNT
  quantity event; an animal's free-text `group_name` that names no group of
  its species becomes a MEMBERSHIP_DERIVED group of that name, and every
  animal whose group_name matches a group gets a membership;
- `pregnant = true` becomes an active pregnancy episode (conception dated
  from `pregnancy_days` where known), `lactating = true` an active
  lactation episode numbered from `lactation_cycle`;
- every tenant that already has animals or groups gets its farm_settings
  row with the defaults.

The migration runs as the table owner, who is *not* exempt from FORCE ROW
LEVEL SECURITY, so the backfill lifts FORCE on `animal` and `animal_group`
while it reads and writes them and puts it back at the end; the new tables
get their policies once their rows are in.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a1f0c2d4e6b8"
down_revision: Union[str, None] = "8de412824ec8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Frozen at this revision: the catalog may change later, this migration must not.
_GESTATION = {"cow": 283, "sheep": 147, "goat": 150, "horse": 340}
_OVIPAROUS = ("layer_hen", "duck", "turkey")
_BACKFILL = "backfill"
_DEFAULT_SETTINGS = {
    "dry_off_days_before_birth": 60,
    "milk_peak_start_days": 40,
    "milk_peak_end_days": 70,
    "breeding_wait_days": 50,
    "heat_cycle_days": 21,
}

_NEW_TABLES = [
    "animal_state_history",
    "animal_group_membership",
    "animal_group_quantity_event",
    "animal_relationship",
    "breeding_event",
    "pregnancy_episode",
    "pregnancy_assessment",
    "birth_event",
    "birth_offspring",
    "lactation_episode",
    "incubation_batch",
    "hatch_event",
    "farm_settings",
]


def _enable_rls(table: str) -> None:
    # Mandatory tenant isolation for every new farm-data-plane table — see
    # TENANCY.md and the initial tenant migration, which created
    # app_current_tenant_id(). A farm's herd history, memberships, head
    # counts, parentage, breedings, pregnancies, births, lactations,
    # incubations and settings are tenant rows.
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation_{table}
        ON {table}
        USING (tenant_id = app_current_tenant_id())
        WITH CHECK (tenant_id = app_current_tenant_id())
        """
    )


def _synced(*extra: sa.Column) -> list:
    """The UUIDPrimaryKeyMixin + SyncedEntityMixin + TimestampMixin columns."""
    return [
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("farm_id", sa.UUID(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("origin_device_id", sa.UUID(), nullable=True),
        sa.Column("last_modified_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        *extra,
    ]


def _ts(name: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _uuid(name: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.UUID(), nullable=nullable)


def _table(name: str, *columns: object) -> None:
    op.create_table(name, *_synced(), *columns, sa.PrimaryKeyConstraint("id"))
    op.create_index(op.f(f"ix_{name}_tenant_id"), name, ["tenant_id"], unique=False)
    op.create_index(op.f(f"ix_{name}_farm_id"), name, ["farm_id"], unique=False)


def upgrade() -> None:
    # ------------------------------------------------------------ columns
    op.add_column("animal", sa.Column("lifecycle_status", sa.String(length=20), nullable=False, server_default="active"))
    op.add_column("animal", _ts("exited_at", nullable=True))
    op.add_column("animal", _ts("pregnancy_started_at", nullable=True))
    op.add_column("animal", _ts("expected_due_date", nullable=True))
    op.add_column("animal", _ts("lactation_started_at", nullable=True))
    op.add_column("animal", _ts("dried_off_at", nullable=True))
    op.add_column(
        "animal_group",
        sa.Column("tracking_mode", sa.String(length=30), nullable=False, server_default="QUANTITY_MANAGED"),
    )
    op.add_column("animal_group", sa.Column("membership_rule", sa.String(length=30), nullable=True))

    # ------------------------------------------------------------ livestock
    _table(
        "animal_state_history",
        _uuid("animal_id"),
        sa.Column("state_type", sa.String(length=40), nullable=False),
        sa.Column("value_code", sa.String(length=200), nullable=True),
        sa.Column("value_reference_id", sa.String(length=64), nullable=True),
        _ts("effective_from"),
        _ts("effective_to", nullable=True),
        sa.Column("reason_code", sa.String(length=80), nullable=True),
        sa.Column("source_type", sa.String(length=40), nullable=True),
        sa.Column("source_id", sa.String(length=64), nullable=True),
        _ts("recorded_at"),
        _uuid("recorded_by", nullable=True),
        sa.CheckConstraint("effective_to IS NULL OR effective_to >= effective_from", name="ck_animal_state_history_range"),
    )
    op.create_index(op.f("ix_animal_state_history_animal_id"), "animal_state_history", ["animal_id"], unique=False)
    op.create_index("ix_animal_state_history_animal", "animal_state_history", ["animal_id", "state_type", "effective_from"], unique=False)

    _table(
        "animal_group_membership",
        _uuid("animal_group_id"),
        _uuid("animal_id"),
        _ts("valid_from"),
        _ts("valid_to", nullable=True),
        sa.Column("reason_code", sa.String(length=80), nullable=True),
        sa.Column("end_reason_code", sa.String(length=80), nullable=True),
        _uuid("created_by", nullable=True),
        _uuid("ended_by", nullable=True),
        sa.CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="ck_animal_group_membership_range"),
    )
    op.create_index("ix_animal_group_membership_group", "animal_group_membership", ["animal_group_id", "valid_from"], unique=False)
    op.create_index("ix_animal_group_membership_animal", "animal_group_membership", ["animal_id", "valid_from"], unique=False)

    _table(
        "animal_group_quantity_event",
        _uuid("animal_group_id"),
        sa.Column("event_type", sa.String(length=30), nullable=False),
        sa.Column("quantity_delta", sa.Integer(), nullable=False),
        sa.Column("resulting_quantity", sa.Integer(), nullable=False),
        _ts("occurred_at"),
        sa.Column("reason", sa.String(length=200), nullable=True),
        sa.Column("source_type", sa.String(length=40), nullable=True),
        sa.Column("source_id", sa.String(length=64), nullable=True),
        _ts("recorded_at"),
        _uuid("recorded_by", nullable=True),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.CheckConstraint("resulting_quantity >= 0", name="ck_animal_group_quantity_non_negative"),
    )
    op.create_index("ix_animal_group_quantity_event_group", "animal_group_quantity_event", ["animal_group_id", "occurred_at"], unique=False)

    _table(
        "animal_relationship",
        _uuid("source_animal_id"),
        _uuid("target_animal_id"),
        sa.Column("relationship_type", sa.String(length=30), nullable=False),
        sa.Column("confidence_code", sa.String(length=30), nullable=False),
        sa.Column("evidence_type", sa.String(length=40), nullable=True),
        sa.Column("evidence_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        _uuid("created_by", nullable=True),
        sa.CheckConstraint("source_animal_id <> target_animal_id", name="ck_animal_relationship_not_self"),
    )
    op.create_index("ix_animal_relationship_source", "animal_relationship", ["source_animal_id", "relationship_type"], unique=False)
    op.create_index("ix_animal_relationship_target", "animal_relationship", ["target_animal_id", "relationship_type"], unique=False)

    # ------------------------------------------------------------ reproduction
    _table(
        "breeding_event",
        _uuid("female_animal_id"),
        _uuid("male_animal_id", nullable=True),
        sa.Column("sire_reference", sa.String(length=120), nullable=True),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        _ts("occurred_at"),
        sa.Column("semen_batch_reference", sa.String(length=120), nullable=True),
        _uuid("technician_user_id", nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("void_reason", sa.String(length=200), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        _ts("recorded_at"),
        _uuid("recorded_by", nullable=True),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_breeding_event_female", "breeding_event", ["female_animal_id", "occurred_at"], unique=False)

    _table(
        "pregnancy_episode",
        _uuid("animal_id"),
        _uuid("originating_breeding_event_id", nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        _ts("estimated_conception_at", nullable=True),
        _ts("expected_due_date", nullable=True),
        _ts("ended_at", nullable=True),
        sa.Column("outcome_code", sa.String(length=30), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        _uuid("created_by", nullable=True),
    )
    op.create_index("ix_pregnancy_episode_animal", "pregnancy_episode", ["animal_id", "status"], unique=False)

    _table(
        "pregnancy_assessment",
        _uuid("pregnancy_episode_id", nullable=True),
        _uuid("animal_id"),
        _ts("assessed_at"),
        sa.Column("assessment_type", sa.String(length=30), nullable=False),
        sa.Column("result_code", sa.String(length=20), nullable=False),
        sa.Column("estimated_gestation_days", sa.Integer(), nullable=True),
        _ts("expected_due_date", nullable=True),
        _uuid("examiner_user_id", nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        _ts("recorded_at"),
        _uuid("recorded_by", nullable=True),
    )
    op.create_index("ix_pregnancy_assessment_animal", "pregnancy_assessment", ["animal_id", "assessed_at"], unique=False)

    _table(
        "birth_event",
        _uuid("dam_animal_id"),
        _uuid("pregnancy_episode_id", nullable=True),
        _uuid("sire_animal_id", nullable=True),
        sa.Column("sire_reference", sa.String(length=120), nullable=True),
        _ts("occurred_at"),
        sa.Column("assistance_code", sa.String(length=20), nullable=False),
        sa.Column("offspring_count", sa.Integer(), nullable=False),
        sa.Column("live_count", sa.Integer(), nullable=False),
        sa.Column("stillborn_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        _ts("recorded_at"),
        _uuid("recorded_by", nullable=True),
        sa.CheckConstraint("offspring_count >= 0 AND live_count >= 0 AND stillborn_count >= 0", name="ck_birth_counts_non_negative"),
        sa.CheckConstraint("live_count + stillborn_count <= offspring_count", name="ck_birth_counts_add_up"),
    )
    op.create_index("ix_birth_event_dam", "birth_event", ["dam_animal_id", "occurred_at"], unique=False)

    _table(
        "birth_offspring",
        _uuid("birth_event_id"),
        _uuid("offspring_animal_id", nullable=True),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("sex", sa.String(length=5), nullable=True),
        sa.Column("birth_weight_kg", sa.Float(), nullable=True),
        sa.Column("outcome_code", sa.String(length=20), nullable=False),
        _uuid("destination_group_id", nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.UniqueConstraint("birth_event_id", "sequence_no", name="uq_birth_offspring_sequence"),
    )
    op.create_index(op.f("ix_birth_offspring_birth_event_id"), "birth_offspring", ["birth_event_id"], unique=False)

    _table(
        "lactation_episode",
        _uuid("animal_id"),
        _uuid("birth_event_id", nullable=True),
        _ts("started_at"),
        _ts("ended_at", nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("lactation_number", sa.Integer(), nullable=True),
        sa.Column("end_reason_code", sa.String(length=30), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False),
        _uuid("created_by", nullable=True),
        _uuid("ended_by", nullable=True),
        sa.CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="ck_lactation_range"),
    )
    op.create_index("ix_lactation_episode_animal", "lactation_episode", ["animal_id", "status"], unique=False)

    _table(
        "incubation_batch",
        sa.Column("species_code", sa.String(length=30), nullable=False),
        _uuid("source_group_id", nullable=True),
        sa.Column("code", sa.String(length=60), nullable=False),
        sa.Column("eggs_set", sa.Integer(), nullable=False),
        _ts("set_at"),
        _ts("expected_hatch_date", nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        _uuid("created_by", nullable=True),
        sa.UniqueConstraint("tenant_id", "code", name="uq_incubation_batch_code"),
        sa.CheckConstraint("eggs_set > 0", name="ck_incubation_eggs_set_positive"),
    )

    _table(
        "hatch_event",
        _uuid("incubation_batch_id"),
        _ts("occurred_at"),
        sa.Column("hatched_live", sa.Integer(), nullable=False),
        sa.Column("unhatched", sa.Integer(), nullable=True),
        sa.Column("culled", sa.Integer(), nullable=True),
        _uuid("destination_group_id", nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        _ts("recorded_at"),
        _uuid("recorded_by", nullable=True),
        sa.CheckConstraint("hatched_live >= 0", name="ck_hatch_live_non_negative"),
    )
    op.create_index(op.f("ix_hatch_event_incubation_batch_id"), "hatch_event", ["incubation_batch_id"], unique=False)

    # ------------------------------------------------------------ farm settings
    _table(
        "farm_settings",
        sa.Column("dry_off_days_before_birth", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("milk_peak_start_days", sa.Integer(), nullable=False, server_default="40"),
        sa.Column("milk_peak_end_days", sa.Integer(), nullable=False, server_default="70"),
        sa.Column("breeding_wait_days", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("heat_cycle_days", sa.Integer(), nullable=False, server_default="21"),
        sa.UniqueConstraint("tenant_id", name="uq_farm_settings_tenant"),
    )

    _backfill()

    for table in _NEW_TABLES:
        _enable_rls(table)


# ---------------------------------------------------------------- backfill
_UUID = postgresql.UUID(as_uuid=True)
_TS = sa.DateTime(timezone=True)

_animals_t = sa.table(
    "animal",
    sa.column("id", _UUID), sa.column("tenant_id", _UUID), sa.column("species", sa.String), sa.column("sex", sa.String),
    sa.column("life_stage", sa.String), sa.column("management_profile", sa.String), sa.column("location_label", sa.String),
    sa.column("group_name", sa.String), sa.column("active", sa.Boolean), sa.column("deleted_at", _TS),
    sa.column("pregnant", sa.Boolean), sa.column("pregnancy_days", sa.Integer), sa.column("lactating", sa.Boolean),
    sa.column("lactation_cycle", sa.Integer), sa.column("created_at", _TS), sa.column("lifecycle_status", sa.String),
    sa.column("pregnancy_started_at", _TS), sa.column("expected_due_date", _TS), sa.column("lactation_started_at", _TS),
)
_groups_t = sa.table(
    "animal_group",
    sa.column("id", _UUID), sa.column("tenant_id", _UUID), sa.column("version", sa.Integer), sa.column("name", sa.String),
    sa.column("species", sa.String), sa.column("group_type", sa.String), sa.column("count", sa.Integer),
    sa.column("sex_composition", sa.String), sa.column("status", sa.String), sa.column("tracking_mode", sa.String),
    sa.column("deleted_at", _TS), sa.column("created_at", _TS), sa.column("updated_at", _TS),
)
_history_t = sa.table(
    "animal_state_history",
    sa.column("id", _UUID), sa.column("tenant_id", _UUID), sa.column("version", sa.Integer), sa.column("animal_id", _UUID),
    sa.column("state_type", sa.String), sa.column("value_code", sa.String), sa.column("effective_from", _TS),
    sa.column("reason_code", sa.String), sa.column("source_type", sa.String), sa.column("recorded_at", _TS),
)
_members_t = sa.table(
    "animal_group_membership",
    sa.column("id", _UUID), sa.column("tenant_id", _UUID), sa.column("version", sa.Integer), sa.column("animal_group_id", _UUID),
    sa.column("animal_id", _UUID), sa.column("valid_from", _TS), sa.column("reason_code", sa.String),
)
_qty_t = sa.table(
    "animal_group_quantity_event",
    sa.column("id", _UUID), sa.column("tenant_id", _UUID), sa.column("version", sa.Integer), sa.column("animal_group_id", _UUID),
    sa.column("event_type", sa.String), sa.column("quantity_delta", sa.Integer), sa.column("resulting_quantity", sa.Integer),
    sa.column("occurred_at", _TS), sa.column("reason", sa.String), sa.column("source_type", sa.String), sa.column("recorded_at", _TS),
)
_pregnancies_t = sa.table(
    "pregnancy_episode",
    sa.column("id", _UUID), sa.column("tenant_id", _UUID), sa.column("version", sa.Integer), sa.column("animal_id", _UUID),
    sa.column("status", sa.String), sa.column("estimated_conception_at", _TS), sa.column("expected_due_date", _TS),
    sa.column("source", sa.String),
)
_lactations_t = sa.table(
    "lactation_episode",
    sa.column("id", _UUID), sa.column("tenant_id", _UUID), sa.column("version", sa.Integer), sa.column("animal_id", _UUID),
    sa.column("started_at", _TS), sa.column("status", sa.String), sa.column("lactation_number", sa.Integer),
    sa.column("source", sa.String),
)
_settings_t = sa.table(
    "farm_settings",
    sa.column("id", _UUID), sa.column("tenant_id", _UUID), sa.column("version", sa.Integer),
    *(sa.column(c, sa.Integer) for c in _DEFAULT_SETTINGS),
)


def _utc(value: datetime | None, default: datetime) -> datetime:
    if value is None:
        return default
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _backfill() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc)
    # The owner is subject to FORCE ROW LEVEL SECURITY and would see no
    # rows; lift it for the backfill only (re-applied below).
    op.execute("ALTER TABLE animal NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE animal_group NO FORCE ROW LEVEL SECURITY")
    try:
        animals = conn.execute(sa.select(_animals_t).where(_animals_t.c.deleted_at.is_(None))).fetchall()
        conn.execute(_animals_t.update().where(_animals_t.c.active == sa.false()).values(lifecycle_status="archived"))

        # Opening state history: what each animal was registered as.
        history = []
        for a in animals:
            since = _utc(a.created_at, now)
            for state_type, value in (
                ("STATUS", "active" if a.active else "archived"),
                ("LIFE_STAGE", a.life_stage),
                ("MANAGEMENT_PROFILE", a.management_profile),
                ("LOCATION", a.location_label),
            ):
                if value is None:
                    continue
                history.append({"id": uuid.uuid4(), "tenant_id": a.tenant_id, "version": 1, "animal_id": a.id, "state_type": state_type,
                                "value_code": value, "effective_from": since, "reason_code": "opening_state", "source_type": _BACKFILL,
                                "recorded_at": now})
        if history:
            op.bulk_insert(_history_t, history)

        # Groups: an existing group, and a group for every free-text group name that named none.
        groups = conn.execute(sa.select(_groups_t).where(_groups_t.c.deleted_at.is_(None))).fetchall()
        by_key = {(g.tenant_id, g.species, (g.name or "").strip().lower()): g.id for g in groups}
        members_of: dict = {}
        new_groups: dict = {}
        for a in animals:
            name = (a.group_name or "").strip()
            if not name or not a.active:
                continue
            key = (a.tenant_id, a.species, name.lower())
            if key not in by_key:
                group = new_groups.setdefault(key, {
                    "id": uuid.uuid4(), "tenant_id": a.tenant_id, "version": 1, "name": name, "species": a.species,
                    "group_type": "flock" if a.species in _OVIPAROUS else "herd", "count": 0, "sex_composition": None,
                    "status": "healthy", "tracking_mode": "MEMBERSHIP_DERIVED", "created_at": now, "updated_at": now, "_sexes": set(),
                })
                by_key[key] = group["id"]
            group_id = by_key[key]
            members_of.setdefault(group_id, []).append(a)
            if key in new_groups:
                new_groups[key]["count"] += 1
                new_groups[key]["_sexes"].add(a.sex)
        for g in new_groups.values():
            sexes = g.pop("_sexes")
            g["sex_composition"] = "female" if sexes == {"F"} else ("male" if sexes == {"M"} else "mixed")
        if new_groups:
            op.bulk_insert(_groups_t, list(new_groups.values()))

        for g in groups:
            mode = "HYBRID" if members_of.get(g.id) else "QUANTITY_MANAGED"
            conn.execute(_groups_t.update().where(_groups_t.c.id == g.id).values(tracking_mode=mode))
        quantity = [
            {"id": uuid.uuid4(), "tenant_id": g.tenant_id, "version": 1, "animal_group_id": g.id, "event_type": "INITIAL_COUNT",
             "quantity_delta": g.count, "resulting_quantity": g.count, "occurred_at": _utc(g.created_at, now), "reason": "Opening count",
             "source_type": _BACKFILL, "recorded_at": now}
            for g in groups if (g.count or 0) > 0
        ]
        if quantity:
            op.bulk_insert(_qty_t, quantity)

        memberships = [
            {"id": uuid.uuid4(), "tenant_id": a.tenant_id, "version": 1, "animal_group_id": group_id, "animal_id": a.id,
             "valid_from": _utc(a.created_at, now), "reason_code": "opening_state"}
            for group_id, members in members_of.items() for a in members
        ]
        if memberships:
            op.bulk_insert(_members_t, memberships)

        # Reproduction and lactation: the flags become the episodes they stood for.
        pregnancies, lactations = [], []
        for a in animals:
            if not a.active:
                continue
            if a.pregnant:
                conceived = now - timedelta(days=a.pregnancy_days) if a.pregnancy_days is not None else None
                gestation = _GESTATION.get(a.species)
                due = conceived + timedelta(days=gestation) if conceived is not None and gestation else None
                pregnancies.append({"id": uuid.uuid4(), "tenant_id": a.tenant_id, "version": 1, "animal_id": a.id, "status": "active",
                                    "estimated_conception_at": conceived, "expected_due_date": due, "source": _BACKFILL})
                conn.execute(_animals_t.update().where(_animals_t.c.id == a.id).values(pregnancy_started_at=conceived, expected_due_date=due))
            if a.lactating:
                started = _utc(a.created_at, now)
                lactations.append({"id": uuid.uuid4(), "tenant_id": a.tenant_id, "version": 1, "animal_id": a.id, "started_at": started,
                                   "status": "active", "lactation_number": a.lactation_cycle, "source": _BACKFILL})
                conn.execute(_animals_t.update().where(_animals_t.c.id == a.id).values(lactation_started_at=started))
        if pregnancies:
            op.bulk_insert(_pregnancies_t, pregnancies)
        if lactations:
            op.bulk_insert(_lactations_t, lactations)

        # Every farm that already keeps records gets its settings row with the defaults.
        tenants = {a.tenant_id for a in animals} | {g.tenant_id for g in groups}
        if tenants:
            op.bulk_insert(_settings_t, [{"id": uuid.uuid4(), "tenant_id": t, "version": 1, **_DEFAULT_SETTINGS} for t in tenants])
    finally:
        op.execute("ALTER TABLE animal FORCE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE animal_group FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    for table in reversed(_NEW_TABLES):
        op.drop_table(table)
    op.drop_column("animal_group", "membership_rule")
    op.drop_column("animal_group", "tracking_mode")
    op.drop_column("animal", "dried_off_at")
    op.drop_column("animal", "lactation_started_at")
    op.drop_column("animal", "expected_due_date")
    op.drop_column("animal", "pregnancy_started_at")
    op.drop_column("animal", "exited_at")
    op.drop_column("animal", "lifecycle_status")
