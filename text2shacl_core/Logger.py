# Logger.py
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, List, Optional


_REQUEST_ID: ContextVar[str] = ContextVar("text2shacl_request_id", default="")
_REQUEST_LOG_BUFFER: ContextVar[Optional[List[str]]] = ContextVar(
    "text2shacl_request_log_buffer",
    default=None,
)


class Logger:
    """
    Verbosity levels:
      0 -> only errors
      1 -> warnings
      2 -> info
      3 -> debug
    """

    def __init__(
        self,
        verbosity: int = 2,
    ) -> None:
        self.verbosity = int(verbosity)

    def set_verbosity(self, level: int) -> None:
        self.verbosity = int(level)

    def get_verbosity(self) -> int:
        return self.verbosity

    @contextmanager
    def request_context(self, request_id: str) -> Iterator[List[str]]:
        """Attach a request id and per-request log buffer to the current context."""
        buffer: List[str] = []
        token_id = _REQUEST_ID.set(request_id)
        token_buffer = _REQUEST_LOG_BUFFER.set(buffer)
        try:
            yield buffer
        finally:
            _REQUEST_LOG_BUFFER.reset(token_buffer)
            _REQUEST_ID.reset(token_id)

    def _emit(self, level: str, msg: str) -> None:
        request_id = _REQUEST_ID.get()
        prefix = f"[{level}]"
        if request_id:
            prefix += f" [req:{request_id}]"
        line = f"{prefix} {msg}"
        print(line)
        buffer = _REQUEST_LOG_BUFFER.get()
        if buffer is not None:
            buffer.append(line)

    def error(self, msg: str) -> None:
        if self.verbosity >= 0:
            self._emit("ERROR", msg)

    def warn(self, msg: str) -> None:
        if self.verbosity >= 1:
            self._emit("WARN", msg)

    def info(self, msg: str) -> None:
        if self.verbosity >= 2:
            self._emit("INFO", msg)

    def debug(self, msg: str) -> None:
        if self.verbosity >= 3:
            self._emit("DEBUG", msg)


logger = Logger()
