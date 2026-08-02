"""pygc3 -- async client for the 2GIG GC3 local automation API."""

from __future__ import annotations

from .client import (
    DEFAULT_PARTITION,
    DEFAULT_PORT,
    DEFAULT_TIMEOUT,
    GC3Client,
)
from .exceptions import (
    GC3AuthError,
    GC3ConnectionError,
    GC3Error,
    GC3ResponseError,
)
from .models import Pairing, PanelStatus, Zone, ZoneStatus

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_PARTITION",
    "DEFAULT_PORT",
    "DEFAULT_TIMEOUT",
    "GC3AuthError",
    "GC3Client",
    "GC3ConnectionError",
    "GC3Error",
    "GC3ResponseError",
    "Pairing",
    "PanelStatus",
    "Zone",
    "ZoneStatus",
    "__version__",
]
