"""How far this deployment has got through its own setup.

A fresh Origami install is not usable until a handful of things exist:
someone besides the bootstrap admin, a price on the subscription, a
customer, a subscription recording what that customer pays, and a tablet
that has signed in. Each of those is already visible somewhere in the
console, but only to a reader who knows where to look and what the absence
of a row means — which is exactly what a first-time operator does not know.

"Create a plan" used to be a step of its own. It is not one any more: the
plan is made by the server the first time anybody asks for it, so a step
for it would arrive permanently ticked and teach a new operator nothing.

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
from app.plans.models import Subscription
from app.plans.subscription_plan import get_or_create_plan
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
    # get_or_create_plan rather than a query: asking what the subscription
    # costs on a deployment where nobody has opened the Subscription screen
    # yet should answer "not priced", not "no plans".
    plan = get_or_create_plan(db)
    priced = plan.monthly_price_cents is not None or plan.annual_price_cents is not None
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
            key="pricing",
            done=priced,
            detail=f"{plan.name} is priced"
            if priced
            else f"{plan.name} has no price yet — revenue reads as zero until it does",
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
                "Every customer is subscribed"
                if subscribed >= tenants
                else f"{tenants - subscribed} of {_plural(tenants, 'customer')} "
                "with no subscription recorded"
            ),
        ),
        # Tablets are no longer paired — one appears here the first time
        # somebody signs in on it, so this step is done by a farmer rather
        # than by an admin.
        SetupStepOut(
            key="device",
            done=devices >= 1,
            detail=_plural(devices, "tablet") + " signed in",
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
