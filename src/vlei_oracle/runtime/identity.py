"""Persistent signing identity for authenticated status-event processing and ACKs."""

import json
from pathlib import Path


def incept_if_new(*, hby, alias: str, incept_file: str | None = None):
    """Reopen the configured oracle AID or incept it on the first start."""

    hab = hby.habByName(name=alias)
    if hab is not None:
        return hab

    inits = {
        "transferable": True,
        "icount": 1,
        "isith": "1",
        "ncount": 1,
        "nsith": "1",
        "wits": [],
        "toad": 0,
    }
    if incept_file:
        data = json.loads(Path(incept_file).read_text())
        if not isinstance(data, dict):
            raise ValueError("incept_file must contain a JSON object")
        inits.update(data)
    return hby.makeHab(name=alias, **inits)
