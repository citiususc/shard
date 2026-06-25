# Logger.py
from __future__ import annotations

import sys
from typing import TextIO


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

    def error(self, msg: str) -> None:
        if self.verbosity >= 0:
            print(f"[ERROR] {msg}")

    def warn(self, msg: str) -> None:
        if self.verbosity >= 1:
            print(f"[WARN] {msg}")

    def info(self, msg: str) -> None:
        if self.verbosity >= 2:
            print(f"[INFO] {msg}")

    def debug(self, msg: str) -> None:
        if self.verbosity >= 3:
            print(f"[DEBUG] {msg}")


logger = Logger()