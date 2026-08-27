from __future__ import annotations

from collections.abc import Generator

from fastapi import Depends
from sqlalchemy.orm import Session

from app.auth.dependencies import get_tenant_context
from app.auth.schemas import TenantContext
from app.common.tenant_router import TenantDataRouter


def get_tenant_db(
    tenant_context: TenantContext = Depends(get_tenant_context),
) -> Generator[Session, None, None]:
    with TenantDataRouter.session_for(tenant_context.tenant_id) as session:
        yield session
