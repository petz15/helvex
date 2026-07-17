"""NOGA hierarchy loader — zero-dependency, safe to import from any layer."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_NOGA_CACHE: dict[str, Any] | None = None


def load_noga_hierarchy() -> dict[str, Any]:
    """Load and cache NOGA hierarchy from noga_lookup.json. Called once at startup."""
    global _NOGA_CACHE
    if _NOGA_CACHE is not None:
        return _NOGA_CACHE

    lookup_path = Path(__file__).resolve().parents[3] / "noga_lookup.json"
    try:
        with lookup_path.open("r", encoding="utf-8") as f:
            noga_data = json.load(f)
    except Exception as e:
        logger.warning("Could not load noga_lookup.json: %s, returning empty hierarchy", e)
        _NOGA_CACHE = {}
        return _NOGA_CACHE

    parent_map: dict[str, str] = {}
    for code, node in noga_data.items():
        if isinstance(node, dict) and "parentCode" in node:
            parent_map[str(code)] = str(node["parentCode"])

    nodes: dict[str, dict] = {}
    for code, node in noga_data.items():
        if isinstance(node, dict):
            parent = parent_map.get(str(code))
            nodes[str(code)] = {
                "label": node.get("label", str(code)),
                "parent": parent,
            }
        else:
            nodes[str(code)] = {"label": str(code), "parent": None}

    _NOGA_CACHE = {
        "nodes": nodes,
        "parent_map": parent_map,
        "noga_data": noga_data,
    }
    return _NOGA_CACHE
