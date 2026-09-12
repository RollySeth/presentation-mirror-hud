from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TypeAlias


MetricValue: TypeAlias = float | bool | int | str | None


class SensorAdapter(ABC):
    @abstractmethod
    def sample(self) -> dict[str, MetricValue]:
        """Return a partial LiveMetrics payload."""

    def close(self) -> None:
        """Release hardware resources."""
