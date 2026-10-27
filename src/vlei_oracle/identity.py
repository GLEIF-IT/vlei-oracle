"""Shared local oracle identity creation for both service entrypoints."""

import json
from pathlib import Path


def incept_if_new(*, hby, alias: str, incept_file: str | None = None):
    """Create the oracle AID if the Habery does not already contain it.

    The local e2e usually incepts the oracle AID before the service starts, so
    this normally reopens an existing Hab. The fallback keeps standalone server
    starts usable for tests and development.
    """

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
