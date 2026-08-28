"""Idempotency-Key handling for the FarmOS tablet contract.

The tablet is offline-first: a write that loses the network gets queued and
retried, and the tablet cannot tell "never arrived" from "arrived, but the
response never made it back". Both look identical from the field. So every
mutating request may carry an Idempotency-Key header; the first successful
(2xx) response for a given (key, user) is stored and replayed verbatim for
any later request carrying that same key — without re-running the handler,
so a replay never touches the database a second time. A key is remembered
only after success: a replay of something that was rejected deserves a
fresh attempt, since the missing permission might have been granted since.

Implemented as middleware (not a per-route dependency) so every mutating
route gets this for free and a route can't accidentally forget to wire it
in — see app/main.py.
"""

from __future__ import annotations

import json
import uuid

import jwt
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.common.db import ControlSessionLocal
from app.config import get_settings
from app.farmos.models import IdempotencyRecord
from app.farmos.security import decode_access_token

_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _extract_user_id(request: Request) -> uuid.UUID | None:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[len("Bearer "):]
    try:
        claims = decode_access_token(get_settings(), token)
        return uuid.UUID(claims["uid"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        return None


class IdempotencyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if (
            request.method not in _MUTATING_METHODS
            or not request.url.path.startswith("/api/v1")
        ):
            return await call_next(request)

        key = request.headers.get("Idempotency-Key")
        if not key:
            return await call_next(request)

        user_id = _extract_user_id(request)
        if user_id is None:
            # No valid identity to key against yet — let the route's own
            # auth dependency produce the proper 401, untouched.
            return await call_next(request)

        with ControlSessionLocal() as db:
            existing = db.execute(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.key == key, IdempotencyRecord.user_id == user_id
                )
            ).scalar_one_or_none()
            if existing is not None:
                has_body = existing.response_body is not None
                response = Response(
                    content=json.dumps(existing.response_body) if has_body else None,
                    status_code=existing.status_code,
                    media_type="application/json" if has_body else None,
                )
                response.headers["Idempotency-Replayed"] = "true"
                return response

        response = await call_next(request)

        if 200 <= response.status_code < 300:
            # call_next's declared return type is the base Response, but at
            # runtime it's always the StreamingResponse-shaped object
            # Starlette actually builds here.
            body_bytes = b"".join([chunk async for chunk in response.body_iterator])  # type: ignore[attr-defined]
            parsed = None
            if body_bytes:
                try:
                    parsed = json.loads(body_bytes)
                except ValueError:
                    parsed = None

            with ControlSessionLocal() as db:
                db.add(
                    IdempotencyRecord(
                        key=key,
                        user_id=user_id,
                        method=request.method,
                        path=request.url.path,
                        status_code=response.status_code,
                        response_body=parsed,
                    )
                )
                try:
                    db.commit()
                except Exception:
                    # A concurrent replay of the same key raced us and won —
                    # the other request's stored response is authoritative;
                    # our own response below is still returned to this
                    # caller since it's identical in shape.
                    db.rollback()

            passthrough_headers = {
                k: v
                for k, v in response.headers.items()
                if k.lower() not in ("content-length", "transfer-encoding")
            }
            return Response(
                content=body_bytes,
                status_code=response.status_code,
                headers=passthrough_headers,
                media_type=response.media_type,
            )

        return response
