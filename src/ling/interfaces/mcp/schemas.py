"""Explicit MCP tool schemas. Validation messages name the field, not the raw value."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _Mutating(_Input):
    operation_id: str | None = Field(default=None, min_length=1)


class RegisterSlotInput(_Mutating):
    slot_id: str = Field(min_length=1)
    template_id: str = Field(min_length=1)


class HeartbeatInput(_Mutating):
    slot_id: str = Field(min_length=1)


class DispatchInput(_Mutating):
    issuer_slot_id: str = Field(min_length=1)
    target_template_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    target_slot_id: str | None = Field(default=None, min_length=1)


class ClaimInput(_Mutating):
    actor_slot_id: str = Field(min_length=1)
    ticket_id: str = Field(min_length=1)


class AbandonClaimInput(_Mutating):
    actor_slot_id: str = Field(min_length=1)
    ticket_id: str = Field(min_length=1)


class SubmitInput(_Mutating):
    actor_slot_id: str = Field(min_length=1)
    ticket_id: str = Field(min_length=1)


class ReviewInput(_Mutating):
    actor_slot_id: str = Field(min_length=1)
    ticket_id: str = Field(min_length=1)
    decision: str = Field(min_length=1)


class ConsumeInput(_Mutating):
    actor_slot_id: str = Field(min_length=1)
    ticket_id: str = Field(min_length=1)


class AcquireFileLockInput(_Mutating):
    actor_slot_id: str = Field(min_length=1)
    ticket_id: str = Field(min_length=1)
    paths: list[str] = Field(min_length=1)

    @field_validator("paths")
    @classmethod
    def paths_are_nonempty(cls, paths: list[str]) -> list[str]:
        cleaned = [path.strip() for path in paths]
        if any(not path for path in cleaned):
            raise ValueError("blank path")
        return cleaned


class DashboardInput(_Input):
    """Dashboard takes no arguments."""


class ToolOutput(BaseModel):
    """Fields every tool result must carry. Extra keys are allowed."""

    model_config = ConfigDict(extra="allow")

    ok: bool
    operation_id: str
    ticket_id: str | None = None
    state: str | None = None
    queue: int | None = None
    error_code: str | None = None
    message: str = ""


TOOL_OUTPUT_SCHEMA: dict[str, Any] = ToolOutput.model_json_schema()


def parse_input(model: type[BaseModel], arguments: dict[str, Any]) -> tuple[BaseModel | None, str | None]:
    """Validate tool arguments. The message names the field and not the raw value."""

    try:
        return model.model_validate(arguments), None
    except ValidationError as exc:
        return None, _input_message(exc)


def _input_message(exc: ValidationError) -> str:
    error = exc.errors()[0]
    location = ".".join(str(part) for part in error["loc"]) or "input"
    kind = error["type"]
    if kind == "missing":
        return f"{location} is required"
    if kind in {"string_too_short", "too_short", "value_error"}:
        return f"{location} must be non-empty"
    if kind.endswith("_type"):
        return f"{location} has the wrong type"
    if kind == "extra_forbidden":
        return f"{location} is not a field"
    return "invalid input"
