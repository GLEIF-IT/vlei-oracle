"""KERI/ACDC primitives used by the GL1 vLEI Oracle.

The production path prefers raw ACDC verification from KERIpy's stored bytes.
Dictionary-based SAID verification remains useful for unit fixtures and
generated JSON fragments before they are serialized by KERIpy.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from keri.core import coring, serdering


def saidify_sad(sad: dict[str, Any], *, label: str = "d") -> dict[str, Any]:
    """Return a copy of a self-addressed data object with its SAID injected."""

    _, saidified = coring.Saider.saidify(sad=deepcopy(sad), label=label)
    return saidified


def verify_said(sad: dict[str, Any], *, label: str = "d") -> bool:
    """Verify a self-addressed data object's SAID using KERIpy."""

    said = sad.get(label)
    if not isinstance(said, str) or not said:
        return False
    return coring.Saider(qb64=said).verify(sad=sad, prefixed=True, label=label)


def verify_acdc_raw(raw: str | bytes, *, said: str) -> bool:
    """Verify an ACDC SAID from the exact raw serialization emitted by KERIpy."""

    raw_bytes = raw.encode("utf-8") if isinstance(raw, str) else raw
    try:
        serder = serdering.SerderACDC(raw=raw_bytes)
    except Exception:
        return False
    return serder.said == said and serder.sad.get("d") == said
