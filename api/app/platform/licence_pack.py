"""What a new customer needs to start working: a way to sign in.

This used to hand over two credentials — a pairing key for the tablet and
a sign-in credential for the owner — because a device had to be licensed
before it could be used, and modules were sold in bundles that the key
carried.

Neither is true now. Origami is one subscription covering the whole
product, and tablets no longer carry a licence: a device is simply
something a signed-in person is using, recorded when it first appears.
So the pairing key is gone, and with it the class of confusion where an
admin pasted an ORG- key into an /activate/ URL because both were called
"activation".

What remains is the half that was always the point: the owner's sign-in.
Either a password set here and read down the phone, or a one-time link
they open to choose their own. Both are credentials, both are returned
exactly once and stored only as a hash, so a lost handover is reissued
rather than recovered — which is why reissuing supersedes whatever was
outstanding.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import UserIdentity
from app.auth.passwords import generate_password, hash_password
from app.common.enums import MembershipStatus, TenantRole
from app.plans.models import Plan, Subscription
from app.tenants.invitations import IssuedInvitation, issue_invitation
from app.tenants.models import Tenant, TenantMembership


class LicencePackError(Exception):
    """Why a pack cannot be issued, in words an operator can act on."""


@dataclass
class LicencePack:
    tenant: Tenant
    plan: Plan | None
    # Exactly one of these, per the caller's choice of credential: a
    # sign-in link the owner redeems, or a password set for them here.
    # Issuing both would be two ways in where one was asked for.
    invitation: IssuedInvitation | None = None
    owner_password: str | None = None
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
    invitation_ttl_hours: int,
    credential: str = "link",
) -> LicencePack:
    """The owner's way in, issued once.

    No pairing key any more: a tablet is not licensed, it is simply the
    device somebody signed in on, and it records itself when they do.
    """
    owner = db.get(UserIdentity, membership.user_id)

    invitation = None
    owner_password = None
    if credential == "password":
        # No mail server, no link to pass around: the admin reads this to
        # the customer. set_member_password records that they did not
        # choose it themselves, so every screen keeps saying so until they
        # replace it from the tablet app.
        if owner is None:
            raise LicencePackError("This customer's owner account is missing.")
        owner_password = set_member_password(db, user=owner, password=None)
    else:
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
        invitation=invitation,
        owner_password=owner_password,
        owner=owner,
    )


def set_member_password(db: Session, *, user: UserIdentity, password: str | None) -> str:
    """Sets a tenant user's password on their behalf and returns it once.

    password_changed_at is cleared, not stamped: somebody else chose this,
    which is a different security position from a password its holder
    picked, and the console shows that difference until they replace it
    (POST /api/v1/auth/change-password on the tablet).
    """
    chosen = password or generate_password()
    user.password_hash = hash_password(chosen)
    user.password_changed_at = None
    db.flush()
    return chosen


def licence_pack_email(pack: LicencePack) -> tuple[str, str]:
    """The welcome message: how to get in, and nothing else to do.

    There is no second step any more. Install the app, sign in, and every
    part of Origami is there — no key to type, no tablet to pair, no
    module to wait for.
    """
    owner_name = pack.owner.display_name if pack.owner else "there"

    subject = f"Your Origami account for {pack.tenant.display_name}"
    body = (
        f"Hello {owner_name},\n\n"
        f"{pack.tenant.display_name} is set up on Origami, with every part of\n"
        "the app included.\n\n"
        + (
            "Sign in\n"
            "   Open this link and choose a password. It works once:\n\n"
            f"   {pack.invitation.url}\n\n"
            if pack.invitation
            else "Sign in\n"
            f"   Your email:    {pack.owner.email if pack.owner else ''}\n"
            f"   Your password: {pack.owner_password}\n\n"
            "   Please change it once you are in, from Settings in the app.\n\n"
        )
        + "Install the Origami app on as many tablets as you need and sign in on\n"
        "each one. Nothing else to set up.\n\n"
        "If you were not expecting this, you can ignore this message.\n"
    )
    return subject, body
