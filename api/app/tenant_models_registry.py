"""Import every farm-data-plane model so TenantBase.metadata is complete."""

from app.common.db import TenantBase
from app.farmos import farm_models as _farmos_farm_models  # noqa: F401
from app.tenant_api import models as _tenant_api_models  # noqa: F401

__all__ = ["TenantBase"]
