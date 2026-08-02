"""Exception hierarchy for pygc3.

Kept deliberately small and mapped to the two outcomes a Home Assistant
integration cares about: a bad credential (→ reauth) vs. an unreachable panel
(→ mark unavailable / ConfigEntryNotReady).
"""

from __future__ import annotations


class GC3Error(Exception):
    """Base class for all pygc3 errors."""


class GC3ConnectionError(GC3Error):
    """The panel could not be reached (DNS, refused, timeout, TLS, socket).

    Transient by nature. The GC3 refuses new API connections for ~a minute after
    a reboot while it reaps the previous session, so callers should treat this as
    retryable rather than fatal.
    """


class GC3AuthError(GC3Error):
    """The panel rejected the credentials (HTTP 403 "Invalid API key").

    Maps to ConfigEntryAuthFailed / a reauth flow. Not retryable without new
    credentials.
    """


class GC3ResponseError(GC3Error):
    """The panel answered, but not in a way we can use.

    Unexpected HTTP status, or a body that was not the JSON we expected. Carries
    the status code when there is one.
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status
