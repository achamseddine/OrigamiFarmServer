"""Which licence gates which screen in the tablet app.

The one place this is written down. Before it existed the answer was
"nothing much": seventeen of the twenty tablet modules carried no
license_code at all, so they were on for every farm regardless of plan,
and the platform's own uppercase module codes (ANIMALS, MILK, …) gated
nothing a farm worker could see. A plan built out of them switched on
exactly nothing in the app — which was the whole point of selling one.

The rule the API applies is unchanged and still the only rule
(app/farmos/routes_employees.py):

    licensed_active = license_code is None or license_code in <tenant's
                      ACTIVE/TRIAL entitlement codes>

What changes is that every module now names a licence, so a plan built
from those licences reaches the app.

Two of the licences are lowercase, and that is not an oversight: the
tablet contract pins `POST /api/v1/modules/mouneh/activate` and
`.../visits_agritourism/activate` as literal paths the mobile app already
calls (docs/FARMOS_API.md), so renaming them would break a shipped app to
tidy a naming convention. The platform codes MOUNEH and FARM_VISITS are
therefore unused, and nothing offers them for sale — the console builds
its picker from the values here rather than from a hand-kept list, so a
code nothing points at cannot be sold by mistake.
"""

from __future__ import annotations

# tablet module code -> the licence that gates it.
MODULE_LICENCES: dict[str, str] = {
    # Everyday running of the farm. CORE is the licence nobody sells
    # separately — a plan without it is a plan the app cannot open.
    "morning_operations": "CORE",
    "tasks": "CORE",
    "employees": "CORE",
    "reports": "CORE",
    "settings": "CORE",
    # Livestock
    "animals": "ANIMALS",
    "animal_health": "ANIMAL_HEALTH",
    "feed_nutrition": "FEED",
    "milk_production": "MILK",
    "egg_production": "EGGS",
    # Crops
    "agriculture": "AGRICULTURE",
    "produce_harvest": "PRODUCE",
    # Operations and money
    "inventory": "INVENTORY",
    "sales": "SALES",
    "expenses": "SALES",
    "finance": "SALES",
    "ai_intelligence": "AI_INTELLIGENCE",
    # Paid add-ons. Lowercase because the mobile app calls these codes by
    # name — see the module docstring.
    "mouneh_production": "mouneh",
    "mouneh_inventory": "mouneh",
    "farm_visits": "visits_agritourism",
}

# The licences that exist, each with the modules it unlocks. Derived rather
# than written out, so the two can never disagree.
LICENCE_MODULES: dict[str, list[str]] = {}
for _module, _licence in MODULE_LICENCES.items():
    LICENCE_MODULES.setdefault(_licence, []).append(_module)
for _modules in LICENCE_MODULES.values():
    _modules.sort()

# Without this a plan grants screens the app cannot open anything from, so
# the console warns when a plan leaves it out.
BASE_LICENCE = "CORE"
