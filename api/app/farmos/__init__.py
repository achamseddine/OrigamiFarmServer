"""The FarmOS tablet API contract.

Everything under here implements the 92-endpoint tablet contract
(docs/FARMOS_API.md) exactly as specified — including where that means
diverging from the rest of this codebase's own conventions (plain
``{"detail": "..."}`` errors instead of app.common.errors.AppError,
``farm_id`` instead of ``tenant_id`` on the wire, its own password-based
login instead of OIDC). Those divergences are deliberate and documented,
not drift.
"""
