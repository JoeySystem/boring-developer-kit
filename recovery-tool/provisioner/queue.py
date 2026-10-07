from __future__ import annotations

from collections.abc import Callable, Iterable

from .errors import OperatorActionRequired
from .models import BatchItemResult


class SequentialBatch:
    """Run hardware operations serially to avoid port and reset collisions."""

    def __init__(self, runner: Callable[[object], object]):
        self.runner = runner

    def run(self, items: Iterable[object]) -> list[BatchItemResult]:
        results = []
        for item in items:
            try:
                value = self.runner(item)
                results.append(BatchItemResult(item=item, success=True, value=value))
            except Exception as exc:
                results.append(
                    BatchItemResult(
                        item=item,
                        success=False,
                        error=str(exc),
                        action_required=isinstance(exc, OperatorActionRequired),
                    )
                )
        return results
