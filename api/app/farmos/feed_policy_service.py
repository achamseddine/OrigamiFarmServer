"""Feed usage policy (feed architecture §24, §33, §35).

Physical possession of a feed does not imply permission to use it. A
product (or a single lot) may carry a policy of allow / block / limit rules
over species, management profile, life stage, reproductive state and age,
plus an inclusion-rate cap and "must go through an approved formula". The
same evaluation runs when a formula component is added, when a batch is
mixed, when a program is activated and when a feeding is recorded — so a
cattle-only premix fails before any inventory is consumed, whichever door
it came in through. The answer always says why.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.farmos import feed_models as fm
from app.farmos.feed_catalog import FeedError
from app.farmos.livestock_service import ensure_utc, now_utc


@dataclass
class Target:
    """Who the feed would be used for."""

    species_code: str | None = None
    management_profile: str | None = None
    life_stage: str | None = None
    reproductive_state: str | None = None
    age_days: int | None = None
    label: str = ""
    # A formula, a batch or a program is authored for a species, not for
    # one animal: dimensions it cannot know yet (profile, stage, age) are
    # left to the feeding-time check, which sees the real subject. For a
    # real subject `partial` is False and an unset dimension means "not
    # that" — an animal with no profile is not a dairy animal.
    partial: bool = False


@dataclass
class Decision:
    allowed: bool = True
    reasons: list[str] = field(default_factory=list)
    max_inclusion_pct: float | None = None
    requires_approved_formula: bool = False
    cross_species_transfer_allowed: bool = True
    policies: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reasons": self.reasons,
            "max_inclusion_pct": self.max_inclusion_pct,
            "requires_approved_formula": self.requires_approved_formula,
            "cross_species_transfer_allowed": self.cross_species_transfer_allowed,
            "policies": self.policies,
        }


def _effective(policy: fm.FeedUsagePolicy) -> bool:
    if policy.status != "active" or policy.deleted_at is not None:
        return False
    t = now_utc()
    start = ensure_utc(policy.effective_from)
    end = ensure_utc(policy.effective_to)
    if start and start > t:
        return False
    if end and end < t:
        return False
    return True


def policies_for(
    db: Session, product: fm.FeedProduct, lot: fm.FeedLot | None = None
) -> list[fm.FeedUsagePolicy]:
    """The product's policies plus the lot's. Evaluation ANDs them, which is
    exactly the rule that a lot-level policy may tighten but never broaden
    the product's (§24)."""
    rows = list(
        db.scalars(
            select(fm.FeedUsagePolicy).where(
                fm.FeedUsagePolicy.feed_product_id == product.id, fm.FeedUsagePolicy.lot_id.is_(None)
            )
        )
    )
    if lot is not None:
        rows += list(db.scalars(select(fm.FeedUsagePolicy).where(fm.FeedUsagePolicy.lot_id == lot.id)))
    return [p for p in rows if _effective(p)]


def _rule_matches(rule: fm.FeedUsagePolicyRule, target: Target) -> bool:
    def dim(wanted, actual) -> bool:
        if wanted is None:
            return True
        if actual is None and target.partial:
            return True
        return wanted == actual

    if not dim(rule.species_code, target.species_code):
        return False
    if not dim(rule.management_profile, target.management_profile):
        return False
    if not dim(rule.life_stage, target.life_stage):
        return False
    if not dim(rule.reproductive_state, target.reproductive_state):
        return False
    if rule.min_age_days is not None or rule.max_age_days is not None:
        if target.age_days is None:
            return target.partial
        if rule.min_age_days is not None and target.age_days < rule.min_age_days:
            return False
        if rule.max_age_days is not None and target.age_days > rule.max_age_days:
            return False
    return True


def _describe(rule: fm.FeedUsagePolicyRule) -> str:
    parts = []
    if rule.species_code:
        parts.append(rule.species_code.replace("_", " "))
    if rule.management_profile:
        parts.append(rule.management_profile)
    if rule.life_stage:
        parts.append(rule.life_stage)
    if rule.reproductive_state:
        parts.append(rule.reproductive_state)
    if rule.min_age_days is not None or rule.max_age_days is not None:
        parts.append(f"age {rule.min_age_days or 0}–{rule.max_age_days or '∞'} days")
    return " / ".join(parts) or "any animal"


def evaluate(
    db: Session,
    product: fm.FeedProduct,
    target: Target,
    *,
    lot: fm.FeedLot | None = None,
    inclusion_pct: float | None = None,
    through_formula: bool = False,
) -> Decision:
    decision = Decision()
    who = target.label or (target.species_code or "this use").replace("_", " ")
    for policy in policies_for(db, product, lot):
        decision.policies.append(policy.name)
        decision.requires_approved_formula = (
            decision.requires_approved_formula or policy.requires_approved_formula
        )
        decision.cross_species_transfer_allowed = (
            decision.cross_species_transfer_allowed and policy.cross_species_transfer_allowed
        )
        allows = [r for r in policy.rules if r.effect == "allow"]
        blocks = [r for r in policy.rules if r.effect == "block"]
        limits = [r for r in policy.rules if r.effect == "limit"]
        if allows and not any(_rule_matches(r, target) for r in allows):
            decision.allowed = False
            allowed_for = "; ".join(_describe(r) for r in allows)
            decision.reasons.append(
                f"{product.name} is restricted by '{policy.name}' to: {allowed_for} — not {who}."
            )
        for r in blocks:
            if _rule_matches(r, target):
                decision.allowed = False
                why = f": {r.reason}" if r.reason else "."
                decision.reasons.append(
                    f"{product.name} is blocked for {_describe(r)} by '{policy.name}'{why}"
                )
        for r in limits:
            if _rule_matches(r, target) and r.max_inclusion_pct is not None:
                cap = r.max_inclusion_pct
                decision.max_inclusion_pct = (
                    cap if decision.max_inclusion_pct is None else min(decision.max_inclusion_pct, cap)
                )
        if policy.requires_approved_formula and not through_formula:
            decision.allowed = False
            decision.reasons.append(
                f"{product.name} may only be fed through an approved formula ('{policy.name}'), not directly."
            )
    if (
        inclusion_pct is not None
        and decision.max_inclusion_pct is not None
        and inclusion_pct > decision.max_inclusion_pct + 1e-9
    ):
        decision.allowed = False
        decision.reasons.append(
            f"{product.name} at {inclusion_pct:.1f}% exceeds its maximum inclusion "
            f"of {decision.max_inclusion_pct:.1f}%."
        )
    return decision


def check_or_raise(
    db: Session,
    product: fm.FeedProduct,
    target: Target,
    *,
    lot: fm.FeedLot | None = None,
    inclusion_pct: float | None = None,
    through_formula: bool = False,
) -> Decision:
    """Evaluates and refuses with every reason on a block."""
    decision = evaluate(
        db, product, target, lot=lot, inclusion_pct=inclusion_pct, through_formula=through_formula
    )
    if not decision.allowed:
        raise FeedError(" ".join(decision.reasons))
    return decision


def cross_species_transfer_allowed(db: Session, product: fm.FeedProduct) -> bool:
    return all(p.cross_species_transfer_allowed for p in policies_for(db, product))


def set_policy(
    db: Session,
    product: fm.FeedProduct,
    *,
    name: str,
    rules: list[dict],
    requires_approved_formula: bool = False,
    cross_species_transfer_allowed: bool = True,
    lot: fm.FeedLot | None = None,
    notes: str | None = None,
    actor: uuid.UUID | None,
) -> fm.FeedUsagePolicy:
    """Replaces the product's (or lot's) policy. A lot-level policy that
    would broaden the product's is refused."""
    for r in rules:
        if r.get("effect") not in ("allow", "block", "limit"):
            raise FeedError("Each rule needs an effect: allow, block or limit.")
        if r.get("effect") == "limit" and r.get("max_inclusion_pct") is None:
            raise FeedError("A limit rule needs max_inclusion_pct.")
    if lot is not None:
        product_policies = policies_for(db, product)
        product_allows = any(r.effect == "allow" for p in product_policies for r in p.rules)
        if product_allows:
            for r in rules:
                if r.get("effect") == "allow":
                    probe = Target(
                        species_code=r.get("species_code"),
                        management_profile=r.get("management_profile"),
                        life_stage=r.get("life_stage"),
                        reproductive_state=r.get("reproductive_state"),
                        partial=True,
                    )
                    if not evaluate(db, product, probe).allowed:
                        raise FeedError("A lot-level policy cannot allow what the product's policy blocks.")
    existing = list(
        db.scalars(
            select(fm.FeedUsagePolicy).where(
                fm.FeedUsagePolicy.feed_product_id == product.id,
                fm.FeedUsagePolicy.lot_id == (lot.id if lot else None),
            )
        )
    )
    for old in existing:
        old.status = "retired"
        old.effective_to = now_utc()
    policy = fm.FeedUsagePolicy(
        tenant_id=product.tenant_id,
        last_modified_by=actor,
        feed_product_id=product.id,
        lot_id=lot.id if lot else None,
        name=name,
        requires_approved_formula=requires_approved_formula,
        cross_species_transfer_allowed=cross_species_transfer_allowed,
        status="active",
        effective_from=now_utc(),
        notes=notes,
    )
    for r in rules:
        policy.rules.append(
            fm.FeedUsagePolicyRule(
                tenant_id=product.tenant_id,
                effect=r["effect"],
                species_code=r.get("species_code"),
                management_profile=r.get("management_profile"),
                life_stage=r.get("life_stage"),
                reproductive_state=r.get("reproductive_state"),
                max_inclusion_pct=r.get("max_inclusion_pct"),
                min_age_days=r.get("min_age_days"),
                max_age_days=r.get("max_age_days"),
                reason=r.get("reason"),
            )
        )
    db.add(policy)
    db.flush()
    return policy
