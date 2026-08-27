"""Stable, machine-readable error codes.

These codes are part of the API contract (see docs/API_ERROR_CODES.md) and
must not change meaning once shipped. Add new codes rather than repurposing
existing ones.
"""

from __future__ import annotations

from fastapi import status


class ErrorCode:
    # Authn/authz
    UNAUTHENTICATED = "UNAUTHENTICATED"
    INVALID_TOKEN = "INVALID_TOKEN"
    TENANT_SUSPENDED = "TENANT_SUSPENDED"
    TENANT_TERMINATED = "TENANT_TERMINATED"
    MODULE_NOT_ENTITLED = "MODULE_NOT_ENTITLED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    DEVICE_REVOKED = "DEVICE_REVOKED"
    DEVICE_NOT_FOUND = "DEVICE_NOT_FOUND"
    LICENSE_LEASE_EXPIRED = "LICENSE_LEASE_EXPIRED"
    FARM_SCOPE_DENIED = "FARM_SCOPE_DENIED"
    PLATFORM_ROLE_REQUIRED = "PLATFORM_ROLE_REQUIRED"
    MEMBERSHIP_NOT_FOUND = "MEMBERSHIP_NOT_FOUND"

    # Device activation
    ACTIVATION_CODE_INVALID = "ACTIVATION_CODE_INVALID"
    ACTIVATION_CODE_EXPIRED = "ACTIVATION_CODE_EXPIRED"
    ACTIVATION_CODE_ALREADY_USED = "ACTIVATION_CODE_ALREADY_USED"

    # Generic
    NOT_FOUND = "NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    CONFLICT = "CONFLICT"
    IDEMPOTENCY_REPLAY = "IDEMPOTENCY_REPLAY"
    SYNC_CONFLICT = "SYNC_CONFLICT"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


_STATUS_BY_CODE: dict[str, int] = {
    ErrorCode.UNAUTHENTICATED: status.HTTP_401_UNAUTHORIZED,
    ErrorCode.INVALID_TOKEN: status.HTTP_401_UNAUTHORIZED,
    ErrorCode.TENANT_SUSPENDED: status.HTTP_403_FORBIDDEN,
    ErrorCode.TENANT_TERMINATED: status.HTTP_403_FORBIDDEN,
    ErrorCode.MODULE_NOT_ENTITLED: status.HTTP_403_FORBIDDEN,
    ErrorCode.PERMISSION_DENIED: status.HTTP_403_FORBIDDEN,
    ErrorCode.DEVICE_REVOKED: status.HTTP_403_FORBIDDEN,
    ErrorCode.DEVICE_NOT_FOUND: status.HTTP_404_NOT_FOUND,
    ErrorCode.LICENSE_LEASE_EXPIRED: status.HTTP_403_FORBIDDEN,
    ErrorCode.FARM_SCOPE_DENIED: status.HTTP_403_FORBIDDEN,
    ErrorCode.PLATFORM_ROLE_REQUIRED: status.HTTP_403_FORBIDDEN,
    ErrorCode.MEMBERSHIP_NOT_FOUND: status.HTTP_403_FORBIDDEN,
    ErrorCode.ACTIVATION_CODE_INVALID: status.HTTP_400_BAD_REQUEST,
    ErrorCode.ACTIVATION_CODE_EXPIRED: status.HTTP_400_BAD_REQUEST,
    ErrorCode.ACTIVATION_CODE_ALREADY_USED: status.HTTP_400_BAD_REQUEST,
    ErrorCode.NOT_FOUND: status.HTTP_404_NOT_FOUND,
    ErrorCode.VALIDATION_ERROR: status.HTTP_422_UNPROCESSABLE_ENTITY,
    ErrorCode.CONFLICT: status.HTTP_409_CONFLICT,
    ErrorCode.IDEMPOTENCY_REPLAY: status.HTTP_200_OK,
    ErrorCode.SYNC_CONFLICT: status.HTTP_409_CONFLICT,
    ErrorCode.RATE_LIMITED: status.HTTP_429_TOO_MANY_REQUESTS,
    ErrorCode.INTERNAL_ERROR: status.HTTP_500_INTERNAL_SERVER_ERROR,
}


class AppError(Exception):
    """Raised anywhere in the app to produce a stable API error response.

    Never leaks whether a cross-tenant object exists: callers needing that
    behavior should raise AppError(ErrorCode.NOT_FOUND) rather than a
    PERMISSION_DENIED that reveals the object is real but forbidden.
    """

    def __init__(self, code: str, message: str | None = None, *, status_code: int | None = None):
        self.code = code
        self.message = message or code.replace("_", " ").title()
        self.status_code = status_code or _STATUS_BY_CODE.get(code, status.HTTP_400_BAD_REQUEST)
        super().__init__(self.message)
