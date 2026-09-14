from __future__ import annotations

import hashlib
import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.enums import EntitlementStatus
from app.devices.models import Device
from app.entitlements.service import EntitlementService
from app.plans.models import TenantEntitlement
from app.tenants.models import Farm


def hash_activation_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def generate_activation_code() -> str:
    # Human-typeable if needed, but designed primarily for QR encoding.
    return secrets.token_urlsafe(9)


# No 0/O, 1/I/L, 5/S, 8/B: a licence key gets read down a phone line and
# typed on a tablet in a field, and those are the pairs people get wrong.
LICENCE_KEY_ALPHABET = "ACDEFGHJKMNPQRTUVWXY2346789"
LICENCE_KEY_PREFIX = "ORG"
LICENCE_KEY_GROUPS = 3
LICENCE_KEY_GROUP_SIZE = 4


def generate_licence_key() -> str:
    """A readable key in the shape ORG-XXXX-XXXX-XXXX.

    Twelve characters from a 27-letter alphabet is a little over 57 bits,
    which is plenty for a credential that expires, is single-use, and is
    stored only as a hash.
    """
    groups = [
        "".join(secrets.choice(LICENCE_KEY_ALPHABET) for _ in range(LICENCE_KEY_GROUP_SIZE))
        for _ in range(LICENCE_KEY_GROUPS)
    ]
    return "-".join([LICENCE_KEY_PREFIX, *groups])


def normalise_licence_key(code: str) -> str:
    """What the key means regardless of how it was typed.

    Somebody reading a key off an email into a tablet will lose the
    dashes or use lower case, and refusing that is a support call rather
    than security. Only keys generated in the readable format are stored
    under their normalised hash, so older codes are unaffected.
    """
    return "".join(ch for ch in code if ch.isalnum()).upper()


def active_modules_for_tenant(db: Session, tenant_id: uuid.UUID) -> list[str]:
    entitlements = EntitlementService(db)
    codes = db.execute(
        select(TenantEntitlement.module_code).where(
            TenantEntitlement.tenant_id == tenant_id,
            TenantEntitlement.status.in_(
                [EntitlementStatus.ACTIVE, EntitlementStatus.TRIAL]
            ),
        )
    ).scalars().all()
    return [code for code in codes if entitlements.is_module_active(tenant_id, code)]


def farm_ids_for_device(db: Session, device: Device) -> list[uuid.UUID]:
    if device.farm_id:
        return [device.farm_id]
    return list(
        db.execute(select(Farm.id).where(Farm.tenant_id == device.tenant_id, Farm.active.is_(True)))
        .scalars()
        .all()
    )


def permission_profile_hash_for(modules: list[str], farm_ids: list[uuid.UUID]) -> str:
    fingerprint = "|".join(sorted(modules)) + "::" + "|".join(sorted(str(f) for f in farm_ids))
    return hashlib.sha256(fingerprint.encode()).hexdigest()
