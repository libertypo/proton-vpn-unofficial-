"""Utility helpers for warning suppression."""

import warnings


def suppress_eventlet_deprecation_warnings() -> None:
    """Silence Eventlet deprecation warnings that can leak from dependency initialization."""
    warnings.filterwarnings(
        "ignore",
        message=".*Eventlet.*deprecated.*",
        category=DeprecationWarning,
    )
