"""Backward-compat shim — use app.clients.zefix_client instead."""
from app.clients.zefix_client import *  # noqa: F401, F403
from app.clients.zefix_client import (  # noqa: F401 — re-export private names used by tests
    _normalise_uid,
    _parse_company,
    _parse_legal_form,
)
