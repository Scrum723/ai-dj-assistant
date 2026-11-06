"""Env helpers: CHIEF_* preferred, HALO_* accepted as legacy alias."""

from __future__ import annotations

import os
from typing import Optional


def env(name: str, default: Optional[str] = None) -> Optional[str]:
    """Read CHIEF_<name>, then HALO_<name>, then default. Bare PORT is not handled here."""
    v = os.getenv(f"CHIEF_{name}")
    if v is not None:
        return v
    return os.getenv(f"HALO_{name}", default)
