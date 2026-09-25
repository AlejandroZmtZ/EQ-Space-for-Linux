"""Asynchronous worker for non-blocking filter-chain and profile operations."""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

logger = logging.getLogger(__name__)


class WorkerSignals(QObject):
    """Signals emitted by :class:`AsyncActionWorker`."""

    started = Signal()
    finished = Signal(bool, str)  # (success, message_or_error)


class AsyncActionWorker(QRunnable):
    """Executes a callable on a background thread from QThreadPool."""

    def __init__(self, action: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.action = action
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    def run(self) -> None:
        self.signals.started.emit()
        try:
            res = self.action(*self.args, **self.kwargs)
            msg = str(res) if res is not None else ""
            self.signals.finished.emit(True, msg)
        except Exception as exc:
            logger.exception("AsyncActionWorker encountered an error")
            self.signals.finished.emit(False, str(exc))


def run_async(
    action: Callable[..., Any],
    on_finished: Optional[Callable[[bool, str], None]] = None,
    on_started: Optional[Callable[[], None]] = None,
    *args: Any,
    **kwargs: Any,
) -> AsyncActionWorker:
    """Convenience helper to dispatch a task to the global QThreadPool."""
    worker = AsyncActionWorker(action, *args, **kwargs)
    if on_started is not None:
        worker.signals.started.connect(on_started)
    if on_finished is not None:
        worker.signals.finished.connect(on_finished)
    QThreadPool.globalInstance().start(worker)
    return worker
