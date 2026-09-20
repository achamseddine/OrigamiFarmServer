#!/usr/bin/env python3
"""Seeds local/dev data: a platform admin and two tenants (Tenant A /
dairy, Tenant B / mixed), each with the same identical-looking animal so
isolation can be exercised immediately after `docker-compose up`. Safe to
re-run — every lookup is by natural key before creating.

The two tenants used to differ in what they had bought, because that was
the other thing worth exercising. Origami is one subscription covering the
whole product now, so they differ only in their data.

Run from api/ with the venv active:
    PYTHONPATH=. python ../scripts/seed.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))

from sqlalchemy import select  # noqa: E402

from app.auth.models import UserIdentity  # noqa: E402
from app.auth.passwords import hash_password  # noqa: E402
from app.common.db import ControlSessionLocal  # noqa: E402
from app.common.enums import (  # noqa: E402
    BillingCycle,
    MembershipStatus,
    PlatformRole,
    SubscriptionStatus,
    TenantRole,
    TenantStatus,
)
from app.common.tenant_router import TenantDataRouter  # noqa: E402
from app.plans.licensing_map import MODULE_LICENCES  # noqa: E402
from app.plans.models import ModuleCatalog, Subscription  # noqa: E402
from app.plans.subscription_plan import get_or_create_plan  # noqa: E402


def _licence(module_code: str) -> str:
    return MODULE_LICENCES[module_code]

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
# group, license_code). Every module names the licence that gates it, taken
# from the single source of truth in app/plans/licensing_map.py rather than
# repeated here — a module whose licence the tenant does not hold is
# reported licensed_active=false (app/farmos/routes_employees.py), which is
# what makes a plan mean anything in the app.
FARMOS_MODULE_CATALOG: dict[str, tuple[str, str, str, str | None]] = {
    "morning_operations": (
        "Morning Operations", "عمليات الصباح", "operations", _licence("morning_operations"),
    ),
    "animals": ("Animals", "الحيوانات", "livestock", _licence("animals")),
    "animal_health": ("Animal Health", "صحة الحيوان", "livestock", _licence("animal_health")),
    "feed_nutrition": ("Feed & Nutrition", "الأعلاف والتغذية", "livestock", _licence("feed_nutrition")),
    "milk_production": ("Milk Production", "إنتاج الحليب", "livestock", _licence("milk_production")),
    "egg_production": ("Egg Production", "إنتاج البيض", "livestock", _licence("egg_production")),
    "agriculture": ("Agriculture", "الزراعة", "crops", _licence("agriculture")),
    "produce_harvest": ("Produce & Harvest", "المحاصيل والحصاد", "crops", _licence("produce_harvest")),
    "inventory": ("Inventory", "المخزون", "operations", _licence("inventory")),
    "tasks": ("Tasks", "المهام", "operations", _licence("tasks")),
    "sales": ("Sales", "المبيعات", "finance", _licence("sales")),
    "expenses": ("Expenses", "المصروفات", "finance", _licence("expenses")),
    "finance": ("Finance", "المالية", "finance", _licence("finance")),
    "employees": ("Employees", "الموظفون", "management", _licence("employees")),
    "reports": ("Reports", "التقارير", "management", _licence("reports")),
    "settings": ("Settings", "الإعدادات", "management", _licence("settings")),
    "ai_intelligence": ("AI Intelligence", "الذكاء الاصطناعي", "intelligence", _licence("ai_intelligence")),
    "mouneh_production": ("Mouneh Production", "إنتاج المونة", "addon", _licence("mouneh_production")),
    "mouneh_inventory": ("Mouneh Inventory", "مخزون المونة", "addon", _licence("mouneh_inventory")),
    "farm_visits": ("Farm Visits", "زيارات المزرعة", "addon", _licence("farm_visits")),
    # The two add-on licences are catalog rows in their own right, because
    # the tablet contract addresses them by name
    # (POST /api/v1/modules/mouneh/activate) and plan_module has a foreign
    # key to module_catalog. They gate nothing themselves.
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

        print("Seeding a priced subscription for each demo tenant...")
        # Demo commercial data, so the Business dashboard has something to
        # show locally. The price is invented for the demo — a real
        # deployment sets its own in the console, and the plan ships
        # unpriced precisely so an invented figure never reaches a real
        # revenue report.
        plan = get_or_create_plan(db)
        plan.monthly_price_cents = 24_900
        plan.annual_price_cents = 249_000
        db.flush()

        for tenant, cycle in (
            (tenant_a, BillingCycle.MONTHLY),
            (tenant_b, BillingCycle.ANNUAL),
        ):
            existing_sub = db.execute(
                select(Subscription).where(Subscription.tenant_id == tenant.id)
            ).scalar_one_or_none()
            if existing_sub is None:
                now = datetime.now(timezone.utc)
                db.add(
                    Subscription(
                        tenant_id=tenant.id,
                        plan_id=plan.id,
                        status=SubscriptionStatus.ACTIVE,
                        billing_cycle=cycle,
                        starts_at=now - timedelta(days=60),
                        renews_at=now + timedelta(days=20),
                    )
                )
        db.flush()

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
    print("  FarmOS tablet login (POST /api/v1/auth/login): either owner email above,")
    print(f"    password '{DEMO_PASSWORD}'")


if __name__ == "__main__":
    main()
