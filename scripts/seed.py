#!/usr/bin/env python3
"""Seeds local/dev data: a platform admin, and two tenants whose module
entitlements differ (Tenant A / dairy, Tenant B / mixed) so isolation and
entitlement enforcement can be exercised immediately after `docker-compose
up`. Safe to re-run — every lookup is by natural key before creating.

Run from api/ with the venv active:
    PYTHONPATH=. python ../scripts/seed.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))

from sqlalchemy import select  # noqa: E402

from app.auth.models import UserIdentity  # noqa: E402
from app.common.db import ControlSessionLocal  # noqa: E402
from app.common.enums import (  # noqa: E402
    EntitlementSource,
    EntitlementStatus,
    MembershipStatus,
    PlatformRole,
    TenantRole,
    TenantStatus,
)
from app.common.tenant_router import TenantDataRouter  # noqa: E402
from app.farmos.security import hash_password  # noqa: E402
from app.plans.models import ModuleCatalog, TenantEntitlement  # noqa: E402
from app.tenant_api.models import Animal  # noqa: E402
from app.tenants.models import Farm, PlatformRoleAssignment, Tenant, TenantMembership  # noqa: E402

# The platform/admin-web module catalog — unrelated to the FarmOS tablet
# contract's own 20-code permission catalog below; the two vocabularies
# coexist as separate module_catalog rows (see app/plans/models.py).
MODULE_CATALOG = {
    "CORE": ("Core", "الأساسية"),
    "ANIMALS": ("Animals & Livestock", "الماشية"),
    "ANIMAL_HEALTH": ("Animal Health", "صحة الحيوان"),
    "FEED": ("Feed & Nutrition", "الأعلاف"),
    "MILK": ("Milk Production", "إنتاج الحليب"),
    "EGGS": ("Egg Production", "إنتاج البيض"),
    "AGRICULTURE": ("Agriculture / Fields / Crops", "الزراعة"),
    "PRODUCE": ("Produce & Harvest", "المحاصيل"),
    "INVENTORY": ("Inventory", "المخزون"),
    "MOUNEH": ("Mouneh & Farm Product Processing", "المونة"),
    "SALES": ("Sales & Finance", "المبيعات"),
    "FARM_VISITS": ("Farm Visits & Agri-Tourism", "زيارات المزرعة"),
    "AI_INTELLIGENCE": ("AI / Decision Intelligence", "الذكاء الاصطناعي"),
}

# FarmOS tablet contract (GET /modules/catalog): code -> (label_en, label_ar,
# group, license_code). license_code is None for modules included in every
# plan; set for the two paid add-ons (Mouneh, Farm Visits), which must also
# have a matching TenantEntitlement row before GET /modules/catalog reports
# licensed_active=true for them — see app/farmos/routes_employees.py.
FARMOS_MODULE_CATALOG: dict[str, tuple[str, str, str, str | None]] = {
    "morning_operations": ("Morning Operations", "عمليات الصباح", "operations", None),
    "animals": ("Animals", "الحيوانات", "livestock", None),
    "animal_health": ("Animal Health", "صحة الحيوان", "livestock", None),
    "feed_nutrition": ("Feed & Nutrition", "الأعلاف والتغذية", "livestock", None),
    "milk_production": ("Milk Production", "إنتاج الحليب", "livestock", None),
    "egg_production": ("Egg Production", "إنتاج البيض", "livestock", None),
    "agriculture": ("Agriculture", "الزراعة", "crops", None),
    "produce_harvest": ("Produce & Harvest", "المحاصيل والحصاد", "crops", None),
    "inventory": ("Inventory", "المخزون", "operations", None),
    "tasks": ("Tasks", "المهام", "operations", None),
    "sales": ("Sales", "المبيعات", "finance", None),
    "expenses": ("Expenses", "المصروفات", "finance", None),
    "finance": ("Finance", "المالية", "finance", None),
    "employees": ("Employees", "الموظفون", "management", None),
    "reports": ("Reports", "التقارير", "management", None),
    "settings": ("Settings", "الإعدادات", "management", None),
    "ai_intelligence": ("AI Intelligence", "الذكاء الاصطناعي", "intelligence", None),
    "mouneh_production": ("Mouneh Production", "إنتاج المونة", "addon", "mouneh"),
    "mouneh_inventory": ("Mouneh Inventory", "مخزون المونة", "addon", "mouneh"),
    "farm_visits": ("Farm Visits", "زيارات المزرعة", "addon", "visits_agritourism"),
    # License SKUs themselves (rows GET /modules can reference by
    # module_code) — not permission-grid modules, so they carry no
    # license_code of their own.
    "mouneh": ("Mouneh Add-on", "إضافة المونة", "addon", None),
    "visits_agritourism": ("Farm Visits Add-on", "إضافة زيارات المزرعة", "addon", None),
}


def get_or_create_user(db, email: str, display_name: str) -> UserIdentity:
    user = db.execute(select(UserIdentity).where(UserIdentity.email == email)).scalar_one_or_none()
    if user is None:
        user = UserIdentity(idp_subject=email, email=email, display_name=display_name)
        db.add(user)
        db.flush()
        print(f"  created user_identity: {email}")
    return user


def get_or_create_tenant(db, *, company_code: str, display_name: str) -> Tenant:
    tenant = db.execute(select(Tenant).where(Tenant.company_code == company_code)).scalar_one_or_none()
    if tenant is None:
        tenant = Tenant(
            company_code=company_code,
            legal_name=display_name,
            display_name=display_name,
            country="US",
            status=TenantStatus.ACTIVE,
        )
        db.add(tenant)
        db.flush()
        print(f"  created tenant: {company_code} ({tenant.id})")
    return tenant


def grant_modules(db, tenant: Tenant, module_codes: list[str], actor: UserIdentity) -> None:
    for code in module_codes:
        existing = db.execute(
            select(TenantEntitlement).where(
                TenantEntitlement.tenant_id == tenant.id, TenantEntitlement.module_code == code
            )
        ).scalar_one_or_none()
        if existing is not None:
            continue
        db.add(
            TenantEntitlement(
                tenant_id=tenant.id,
                module_code=code,
                status=EntitlementStatus.ACTIVE,
                source=EntitlementSource.PLAN,
                effective_from=datetime.now(timezone.utc),
                changed_by=actor.id,
                reason="seed: initial plan entitlement",
            )
        )
    db.flush()


# Every FarmOS-tablet demo login uses this password — printed at the end
# of the run so a manual tester doesn't have to read this file.
DEMO_PASSWORD = "farmos-demo-2026"


def main() -> None:
    with ControlSessionLocal() as db:
        print("Seeding module catalog...")
        for code, (name_en, name_ar) in MODULE_CATALOG.items():
            if db.get(ModuleCatalog, code) is None:
                db.add(ModuleCatalog(module_code=code, name_en=name_en, name_ar=name_ar))
        for code, (label_en, label_ar, group, license_code) in FARMOS_MODULE_CATALOG.items():
            existing = db.get(ModuleCatalog, code)
            if existing is None:
                db.add(
                    ModuleCatalog(
                        module_code=code,
                        name_en=label_en,
                        name_ar=label_ar,
                        group=group,
                        license_code=license_code,
                    )
                )
            else:
                existing.group = group
                existing.license_code = license_code
        db.flush()

        print("Seeding platform admin...")
        admin = get_or_create_user(db, "admin@origami-platform.com", "Origami Platform Admin")
        if not db.execute(
            select(PlatformRoleAssignment).where(PlatformRoleAssignment.user_id == admin.id)
        ).scalars().all():
            db.add(
                PlatformRoleAssignment(
                    user_id=admin.id, platform_role=PlatformRole.PLATFORM_SUPER_ADMIN.value
                )
            )

        print("Seeding Tenant A (FARM-A, Dairy Farm)...")
        tenant_a = get_or_create_tenant(db, company_code="FARM-A", display_name="Dairy Farm")
        grant_modules(db, tenant_a, ["CORE", "ANIMALS", "FEED", "MILK"], admin)
        farm_a = db.execute(select(Farm).where(Farm.tenant_id == tenant_a.id)).scalar_one_or_none()
        if farm_a is None:
            farm_a = Farm(tenant_id=tenant_a.id, farm_code="MAIN", name="Main Dairy Site")
            db.add(farm_a)
            db.flush()
        owner_a = get_or_create_user(db, "owner@farm-a-demo.com", "Farm A Owner")
        owner_a.password_hash = hash_password(DEMO_PASSWORD)
        membership_a = db.execute(
            select(TenantMembership).where(
                TenantMembership.tenant_id == tenant_a.id, TenantMembership.user_id == owner_a.id
            )
        ).scalar_one_or_none()
        if membership_a is None:
            db.add(
                TenantMembership(
                    tenant_id=tenant_a.id,
                    user_id=owner_a.id,
                    status=MembershipStatus.ACTIVE,
                    tenant_role=TenantRole.TENANT_OWNER,
                    default_farm_id=farm_a.id,
                    role="owner",
                    job_title="Farm Owner",
                )
            )
        else:
            membership_a.role = "owner"

        print("Seeding Tenant B (FARM-B, Mixed Farm)...")
        tenant_b = get_or_create_tenant(db, company_code="FARM-B", display_name="Mixed Farm")
        grant_modules(
            db,
            tenant_b,
            ["CORE", "ANIMALS", "AGRICULTURE", "PRODUCE", "MOUNEH", "SALES", "FARM_VISITS"],
            admin,
        )
        farm_b = db.execute(select(Farm).where(Farm.tenant_id == tenant_b.id)).scalar_one_or_none()
        if farm_b is None:
            farm_b = Farm(tenant_id=tenant_b.id, farm_code="MAIN", name="Main Mixed Site")
            db.add(farm_b)
            db.flush()
        owner_b = get_or_create_user(db, "owner@farm-b-demo.com", "Farm B Owner")
        owner_b.password_hash = hash_password(DEMO_PASSWORD)
        membership_b = db.execute(
            select(TenantMembership).where(
                TenantMembership.tenant_id == tenant_b.id, TenantMembership.user_id == owner_b.id
            )
        ).scalar_one_or_none()
        if membership_b is None:
            db.add(
                TenantMembership(
                    tenant_id=tenant_b.id,
                    user_id=owner_b.id,
                    status=MembershipStatus.ACTIVE,
                    tenant_role=TenantRole.TENANT_OWNER,
                    default_farm_id=farm_b.id,
                    role="owner",
                    job_title="Farm Owner",
                )
            )
        else:
            membership_b.role = "owner"

        print("Granting Tenant B its licensed add-ons (Mouneh, Farm Visits)...")
        for license_code, plan in (("mouneh", "mouneh_addon"), ("visits_agritourism", "farmos_experience")):
            existing_license = db.execute(
                select(TenantEntitlement).where(
                    TenantEntitlement.tenant_id == tenant_b.id,
                    TenantEntitlement.module_code == license_code,
                )
            ).scalar_one_or_none()
            if existing_license is None:
                db.add(
                    TenantEntitlement(
                        tenant_id=tenant_b.id,
                        module_code=license_code,
                        status=EntitlementStatus.ACTIVE,
                        source=EntitlementSource.PLAN,
                        effective_from=datetime.now(timezone.utc),
                        changed_by=admin.id,
                        reason="seed: licensed add-on",
                        plan=plan,
                    )
                )

        db.commit()
        tenant_a_id, tenant_b_id, farm_a_id, farm_b_id = tenant_a.id, tenant_b.id, farm_a.id, farm_b.id

    # Deliberately identical-looking tag codes/names across tenants A and B
    # so a manual tester can immediately confirm isolation is real and not
    # an artifact of the sample data being trivially distinguishable.
    print("Seeding farm-data-plane sample records...")
    with TenantDataRouter.session_for(tenant_a_id) as db:
        if not db.execute(select(Animal).where(Animal.tag == "COW-001")).scalars().all():
            db.add(
                Animal(
                    tenant_id=tenant_a_id,
                    farm_id=farm_a_id,
                    tag="COW-001",
                    species="cow",
                    name="Bessie",
                )
            )

    with TenantDataRouter.session_for(tenant_b_id) as db:
        if not db.execute(select(Animal).where(Animal.tag == "COW-001")).scalars().all():
            db.add(
                Animal(
                    tenant_id=tenant_b_id,
                    farm_id=farm_b_id,
                    tag="COW-001",
                    species="cow",
                    name="Bessie",
                )
            )

    print("Done.")
    print("  Platform admin: admin@origami-platform.com (use /api/v1/auth/dev-login in AUTH_DEV_MODE)")
    print(f"  Tenant A: FARM-A / {tenant_a_id}  owner: owner@farm-a-demo.com")
    print(f"  Tenant B: FARM-B / {tenant_b_id}  owner: owner@farm-b-demo.com")
    print(f"  FarmOS tablet login (POST /api/v1/auth/login): either owner email above, password '{DEMO_PASSWORD}'")


if __name__ == "__main__":
    main()
