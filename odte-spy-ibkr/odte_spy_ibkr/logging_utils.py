from __future__ import annotations

import logging
import sys
from typing import Literal


def setup_logging(level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO") -> logging.Logger:
    """Configure structured console logging for the runner."""
    root = logging.getLogger("odte_spy_ibkr")
    if root.handlers:
        return root
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(handler)
    root.setLevel(getattr(logging, level))
    root.propagate = False
    return root