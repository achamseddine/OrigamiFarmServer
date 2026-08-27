from __future__ import annotations

import uuid

import boto3
from botocore.client import Config as BotoConfig
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_tenant_context
from app.auth.schemas import TenantContext
from app.common.db import get_control_db
from app.config import get_settings
from app.files.models import FileObject

router = APIRouter()


class PresignUploadRequest(BaseModel):
    entity_type: str
    entity_id: str
    farm_id: uuid.UUID | None = None
    mime_type: str
    size_bytes: int


class PresignUploadResponse(BaseModel):
    file_id: uuid.UUID
    upload_url: str
    storage_key: str
    expires_in: int = 900


def _s3_client():
    settings = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
        region_name=settings.s3_region,
        config=BotoConfig(signature_version="s3v4"),
    )


@router.post("/presign-upload", response_model=PresignUploadResponse)
def presign_upload(
    payload: PresignUploadRequest,
    tenant_context: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_control_db),
) -> PresignUploadResponse:
    """Never a public bucket: the object key is namespaced by tenant_id and
    the URL this returns is short-lived and issued only after the caller's
    tenant context has already been resolved and authorized.
    """
    settings = get_settings()
    file_id = uuid.uuid4()
    storage_key = (
        f"tenants/{tenant_context.tenant_id}/{payload.entity_type}/{payload.entity_id}/{file_id}"
    )

    file_object = FileObject(
        id=file_id,
        tenant_id=tenant_context.tenant_id,
        farm_id=payload.farm_id,
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        storage_key=storage_key,
        mime_type=payload.mime_type,
        size_bytes=payload.size_bytes,
    )
    db.add(file_object)
    db.flush()

    upload_url = _s3_client().generate_presigned_url(
        "put_object",
        Params={"Bucket": settings.s3_bucket_name, "Key": storage_key, "ContentType": payload.mime_type},
        ExpiresIn=900,
    )
    return PresignUploadResponse(file_id=file_id, upload_url=upload_url, storage_key=storage_key)
