"""Load the packaged copy of the canonical association schema before parsing."""

from importlib.resources import files

from keri.core import scheming

from vlei_oracle.association.constants import ASSOCIATION_SCHEMA


def association_schemer():
    """Validate the packaged schema against the pinned protocol SAID."""
    raw = (
        files("vlei_oracle.association")
        .joinpath("schema_data/acdc/external-association-v1.json")
        .read_bytes()
    )
    schema = scheming.Schemer(raw=raw)
    if schema.said != ASSOCIATION_SCHEMA:
        raise ValueError(
            "packaged association schema does not match the pinned protocol"
        )
    return schema
