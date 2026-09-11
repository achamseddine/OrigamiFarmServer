"""How far this deployment has got through its own setup.

A fresh Origami install is not usable until a handful of things exist:
someone besides the bootstrap admin, a plan with a price on it, a customer,
a subscription recording what that customer pays, and a paired tablet. Each
of those is already visible somewhere in the console, but only to a reader
who knows where to look and what the absence of a row means — which is
exactly what a first-time operator does not know.

So the check is done here, against the database, rather than written down
in a document that can quietly stop being true. Every step reports the
count it was decided from, because "not done" is only actionable next to
the number that made it false.

Readable by any platform role: this says what the platform is missing, not
what it earns.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_identity, require_platform_role
from app.auth.models import UserIdentity
from app.auth.schemas import Identity
from app.common.db import get_control_db
from app.common.enums import PlatformRole
from app.devices.models import Device
from app.plans.models import Plan, Subscription
from app.tenants.models import PlatformRoleAssignment, Tenant

router = APIRouter()

_ANY_PLATFORM_ROLE = (
    PlatformRole.PLATFORM_SUPER_ADMIN,
    PlatformRole.PLATFORM_COMMERCIAL_ADMIN,
    PlatformRole.PLATFORM_SUPPORT_ADMIN,
    PlatformRole.PLATFORM_AUDITOR,
)


class SetupStepOut(BaseModel):
    key: str
    done: bool
    # What the database actually held when this was decided, in a form the
    # console can print beside the step without recomputing it.
    detail: str


class SetupStateOut(BaseModel):
    generated_at: datetime
    steps: list[SetupStepOut]
    steps_done: int
    steps_total: int
    complete: bool


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else (plural or singular + 's')}"


@router.get("/setup", response_model=SetupStateOut)
def setup_state(
    db: Session = Depends(get_control_db),
    identity: Identity = Depends(get_identity),
    _role: Identity = Depends(require_platform_role(*_ANY_PLATFORM_ROLE)),
) -> SetupStateOut:
    me = db.get(UserIdentity, identity.user_id)

    staff = db.execute(
        select(func.count(func.distinct(PlatformRoleAssignment.user_id)))
    ).scalar_one()
    plans = db.execute(select(Plan)).scalars().all()
    priced = [
        plan
        for plan in plans
        if plan.monthly_price_cents is not None or plan.annual_price_cents is not None
    ]
    tenants = db.execute(select(func.count()).select_from(Tenant)).scalar_one()
    subscribed = db.execute(
        select(func.count(func.distinct(Subscription.tenant_id)))
    ).scalar_one()
    devices = db.execute(select(func.count()).select_from(Device)).scalar_one()

    # An OIDC identity has no password to change, so the step is not
    # outstanding for it — reporting it as undone would leave a checklist
    # nobody in that deployment could ever complete.
    signs_in_with_password = bool(me is not None and me.password_hash)
    if not signs_in_with_password:
        password = SetupStepOut(
            key="password",
            done=True,
            detail="This account signs in through your identity provider",
        )
    elif me is not None and me.password_changed_at is not None:
        password = SetupStepOut(
            key="password",
            done=True,
            detail=f"Set by you on {me.password_changed_at.date().isoformat()}",
        )
    else:
        password = SetupStepOut(
            key="password",
            done=False,
            detail="Still the password whoever created this account typed for you",
        )

    steps = [
        password,
        SetupStepOut(
            key="staff",
            done=staff >= 2,
            detail=f"{_plural(staff, 'account')} with platform access"
            + (" — only you" if staff == 1 else ""),
        ),
        SetupStepOut(
            key="plans",
            done=len(plans) >= 1,
            detail=_plural(len(plans), "plan") + " in the catalogue",
        ),
        SetupStepOut(
            key="pricing",
            done=len(priced) >= 1,
            detail=f"{len(priced)} of {_plural(len(plans), 'plan')} priced"
            if plans
            else "No plans to price yet",
        ),
        SetupStepOut(
            key="tenant",
            done=tenants >= 1,
            detail=_plural(tenants, "customer"),
        ),
        # Not merely "one subscription exists": a customer signed up and
        # never put on a plan is the gap that makes revenue read as zero,
        # and it reappears with every new customer, so this step goes back
        # to undone rather than staying ticked from the first one.
        SetupStepOut(
            key="subscription",
            done=tenants >= 1 and subscribed >= tenants,
            detail="No customers yet"
            if tenants == 0
            else (
                "Every customer is on a plan"
                if subscribed >= tenants
                else f"{tenants - subscribed} of {_plural(tenants, 'customer')} "
                "with no plan recorded"
            ),
        ),
        SetupStepOut(
            key="device",
            done=devices >= 1,
            detail=_plural(devices, "tablet") + " paired",
        ),
    ]

    done = sum(1 for step in steps if step.done)
    return SetupStateOut(
        generated_at=datetime.now(timezone.utc),
        steps=steps,
        steps_done=done,
        steps_total=len(steps),
        complete=done == len(steps),
    )
