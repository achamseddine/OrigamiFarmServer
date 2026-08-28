"""Import every control-plane model so ControlBase.metadata is complete.

Alembic's control env.py imports this module before autogenerating; without
it, models that are never imported elsewhere would be invisible to
'alembic revision --autogenerate'.
"""

from app.audit import models as _audit_models  # noqa: F401
from app.auth import models as _auth_models  # noqa: F401
from app.backups import models as _backups_models  # noqa: F401
from app.billing import models as _billing_models  # noqa: F401
from app.common.db import ControlBase
from app.devices import models as _devices_models  # noqa: F401
from app.farmos import models as _farmos_models  # noqa: F401
from app.files import models as _files_models  # noqa: F401
from app.plans import models as _plans_models  # noqa: F401
from app.platform import models as _platform_models  # noqa: F401
from app.support import models as _support_models  # noqa: F401
from app.tenants import models as _tenants_models  # noqa: F401

__all__ = ["ControlBase"]
