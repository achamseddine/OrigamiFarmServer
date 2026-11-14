"""Request shapes for work responsibilities (OrigamiFarmOS
backend/app/api/v1/employees.py `ResponsibilityIn` / `ResponsibilityEndIn`).
The responses are plain dicts built by app/farmos/responsibility_service.py
`to_dict`, as the reference returns them."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ResponsibilityIn(BaseModel):
    """What the employee looks after (an animal, a group, a species, a field)
    and the duties it means."""

    scope_type: str
    scope_id: str
    duties: list[str] = Field(min_length=1)
    notes: str | None = Field(None, max_length=500)


class ResponsibilityEndIn(BaseModel):
    reason: str | None = Field(None, max_length=300)
