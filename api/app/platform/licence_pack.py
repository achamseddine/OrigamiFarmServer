"""Everything a new customer needs, generated once and handed over together.

Issuing a licence used to be three unrelated jobs in three places: grant
the modules on one tab, generate a device activation code on another, and
— after the invitation work — send the owner a link from a third. Nothing
tied them together, so the obvious mistake was to do one or two of them
and believe the customer was set up.

This assembles the whole handover in one call:

  * the pairing key their tablet is typed into, and
  * the sign-in link their owner opens to choose a password.

Named apart deliberately. They were both "activation" once — the tablet
code and the account page — and an admin duly pasted a pairing key into
/activate/?token=, which is a reasonable thing to do when one word covers
two different credentials.

Both are credentials and both are returned exactly once. The key is stored
only as a hash (it reuses device activation, so there is one pairing
mechanism rather than two competing ones) and the invitation token is
hashed too, so nothing here can be read back out of the database later. A
lost pack is reissued, never recovered — which is also why reissuing
supersedes whatever was outstanding.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import UserIdentity
from app.common.enums import DeviceActivationStatus, MembershipStatus, TenantRole
from app.devices.models import DeviceActivation
from app.devices.service import generate_licence_key, hash_activation_code, normalise_licence_key
from app.plans.models import Plan, Subscription
from app.tenants.invitations import IssuedInvitation, issue_invitation
from app.tenants.models import Tenant, TenantMembership


class LicencePackError(Exception):
    """Why a pack cannot be issued, in words an operator can act on."""


@dataclass
class LicencePack:
    tenant: Tenant
    plan: Plan | None
    licences: list[str] = field(default_factory=list)
    licence_key: str = ""
    licence_key_expires_at: datetime | None = None
    activation: DeviceActivation | None = None
    invitation: IssuedInvitation | None = None
    owner: UserIdentity | None = None


def find_tenant_owner(db: Session, tenant_id: uuid.UUID) -> tuple[TenantMembership, UserIdentity]:
    """The person the pack is addressed to.

    Explicitly the tenant owner rather than "any member": the sign-in link
    sets a password, and handing that to whichever membership happens to
    sort first is not something to leave to chance.
    """
    row = db.execute(
        select(TenantMembership, UserIdentity)
        .join(UserIdentity, UserIdentity.id == TenantMembership.user_id)
        .where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.tenant_role == TenantRole.TENANT_OWNER,
            TenantMembership.status == MembershipStatus.ACTIVE,
        )
        .order_by(TenantMembership.created_at)
    ).first()
    if row is None:
        raise LicencePackError(
            "This customer has no active owner yet. Add one on the Access tab first — "
            "the sign-in link sets that person's password."
        )
    return row[0], row[1]


def issue_licence_pack(
    db: Session,
    *,
    tenant: Tenant,
    membership: TenantMembership,
    base_url: str,
    issued_by: uuid.UUID | None,
    licences: list[str],
    key_ttl_hours: int,
    invitation_ttl_hours: int,
    farm_id: uuid.UUID | None = None,
) -> LicencePack:
    now = datetime.now(timezone.utc)

    # Supersede whatever was outstanding. Two live keys for one customer is
    # how a revoked pack keeps working.
    for previous in db.execute(
        select(DeviceActivation).where(
            DeviceActivation.tenant_id == tenant.id,
            DeviceActivation.status == DeviceActivationStatus.PENDING,
        )
    ).scalars():
        previous.status = DeviceActivationStatus.REVOKED

    key = generate_licence_key()
    activation = DeviceActivation(
        tenant_id=tenant.id,
        farm_id=farm_id,
        # Hashed in its normalised form so the key still pairs a device
        # when it is typed in lower case or without its dashes.
        code_hash=hash_activation_code(normalise_licence_key(key)),
        status=DeviceActivationStatus.PENDING,
        expires_at=now + timedelta(hours=key_ttl_hours),
        created_by=issued_by,
    )
    db.add(activation)
    db.flush()

    invitation = issue_invitation(
        db,
        membership=membership,
        base_url=base_url,
        invited_by=issued_by,
        ttl_hours=invitation_ttl_hours,
    )

    subscription = db.execute(
        select(Subscription).where(Subscription.tenant_id == tenant.id)
    ).scalar_one_or_none()
    plan = db.get(Plan, subscription.plan_id) if subscription else None

    return LicencePack(
        tenant=tenant,
        plan=plan,
        licences=sorted(licences),
        licence_key=key,
        licence_key_expires_at=activation.expires_at,
        activation=activation,
        invitation=invitation,
        owner=db.get(UserIdentity, membership.user_id),
    )


def licence_pack_email(pack: LicencePack) -> tuple[str, str]:
    """One message carrying both halves.

    Two separate emails is how a customer ends up with a tablet code and
    no way to sign in, or the reverse.
    """
    owner_name = pack.owner.display_name if pack.owner else "there"
    plan_line = (
        f"Plan: {pack.plan.name}\n" if pack.plan else "Plan: not recorded yet\n"
    )
    includes = ", ".join(pack.licences) if pack.licences else "nothing yet"

    subject = f"Your Origami licence for {pack.tenant.display_name}"
    body = (
        f"Hello {owner_name},\n\n"
        f"{pack.tenant.display_name} is set up on Origami.\n\n"
        f"{plan_line}"
        f"Includes: {includes}\n\n"
        "1. Set your password\n"
        "   Open this link and choose a password. It works once:\n\n"
        f"   {pack.invitation.url if pack.invitation else ''}\n\n"
        "2. Pair your tablet\n"
        "   Install the Origami app, and type this pairing key into it when it asks:\n\n"
        f"   {pack.licence_key}\n\n"
        "   This is typed into the app, not opened in a browser. It pairs one\n"
        "   device — ask us for another if you have more tablets.\n\n"
        "If you were not expecting this, you can ignore this message.\n"
    )
    return subject, body
