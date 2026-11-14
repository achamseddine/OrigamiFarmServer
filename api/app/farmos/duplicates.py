"""Duplicate-entry guards for the FarmOS record workflows (OrigamiFarmOS
backend/app/core/duplicates.py; handbook/03-Behavioral-Model.md §5, §13).

Two different duplicates, two different defences:

* **the same request sent twice** — a retry, an offline replay, a double
  tap that got through — is answered from the stored response by the
  `Idempotency-Key` handling (app/farmos/idempotency.py). Nothing here.
* **the same fact entered twice by people** — two workers recording the
  same cow's milking, a delivery note typed in again, a mix saved twice —
  is a new request with a new key. Each record workflow therefore looks
  for an existing record that says the same thing (same subject, same
  farm day or the same half-hour, same quantities) and refuses with 409
  and a sentence starting "Possible duplicate:", naming what is already
  there.

A real second entry is legitimate (a second milking, a second insemination
the same heat, two deliveries on one invoice): the client resends with
`confirm_duplicate: true` after the person has said so, and the confirmed
duplicate is written to the audit trail with the record it resembles — an
exception that stays auditable. Where a repeat can never be right (a group
or a feed with the same name, a lot code already used) the refusal is a
plain 409 with no override.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi.exceptions import HTTPException
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.common.enums import ActorType
from app.farmos.deps import AccessContext
from app.tenants.models import Tenant

PREFIX = "Possible duplicate:"
# Two identical entries this close together are taken for the same fact.
WINDOW = timedelta(minutes=30)
# A dose of the same medicine to the same animal this close is checked.
DOSE_WINDOW = timedelta(hours=12)


class PossibleDuplicate(HTTPException):
    def __init__(self, message: str):
        super().__init__(status_code=409, detail=f"{PREFIX} {message}")


def guard(match, message: str, *, confirm: bool):
    """Refuses when [match] exists and the person has not confirmed. Returns
    the match (None when there is none) so a confirmed duplicate can be
    logged against it."""
    if match is not None and not confirm:
        raise PossibleDuplicate(message)
    return match


def _utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def near(at: datetime | None, window: timedelta = WINDOW) -> tuple[datetime, datetime]:
    t = _utc(at)
    return t - window, t + window


def tenant_timezone(control_db: Session, tenant_id: uuid.UUID) -> str:
    """The farm's own timezone (Tenant.timezone, control plane). Unknown
    or unset means UTC."""
    tenant = control_db.get(Tenant, tenant_id)
    return (tenant.timezone if tenant and tenant.timezone else None) or "UTC"


def farm_day(tz_name: str | None, at: datetime | None) -> tuple[datetime, datetime]:
    """The farm's local calendar day around [at], as UTC bounds — a morning
    milking in Beirut is "today" in Beirut, not in UTC. Callers pass the
    tenant's timezone from `tenant_timezone(control_db, access.tenant_id)`."""
    try:
        tz = ZoneInfo(tz_name or "UTC")
    except Exception:  # noqa: BLE001 - an unknown zone name falls back to UTC
        tz = ZoneInfo("UTC")
    t = _utc(at).astimezone(tz)
    start = t.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def log_confirmed(
    control_db: Session,
    access: AccessContext,
    *,
    entity_type: str,
    entity_id: str,
    match_id: str | uuid.UUID | None,
    what: str,
    module_code: str,
) -> None:
    """A duplicate the person confirmed: written down with what it
    resembles, as an audit event in the caller's name."""
    if match_id is None:
        return
    record_audit_event(
        control_db,
        actor_id=access.user_id,
        actor_type=ActorType.TENANT_USER,
        actor_role=access.role,
        tenant_id=access.tenant_id,
        action="duplicate.confirmed",
        entity_type=entity_type,
        entity_id=str(entity_id),
        module_code=module_code,
        summary=f"{access.display_name} confirmed a possible duplicate ({what})",
        metadata={"record": what, "resembles": str(match_id)},
    )
