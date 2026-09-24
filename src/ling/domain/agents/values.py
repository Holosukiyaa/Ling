"""Identity and level value types for templates and slots."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TemplateId:
    """Stable name of a template declaration."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value.strip():
            raise ValueError("template id must be a non-empty string")


@dataclass(frozen=True, slots=True)
class SlotId:
    """Stable identity of one template instance."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value.strip():
            raise ValueError("slot id must be a non-empty string")


@dataclass(frozen=True, slots=True)
class Level:
    """Authority level. A higher number is a superior of a lower number."""

    value: int

    def __post_init__(self) -> None:
        if not isinstance(self.value, int) or isinstance(self.value, bool) or self.value < 1:
            raise ValueError("level must be a positive integer")

    def outranks(self, other: Level) -> bool:
        return self.value > other.value
