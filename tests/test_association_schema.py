"""The packaged artifact is a checked copy of the canonical protocol schema."""

from pathlib import Path

from vlei_oracle.association.constants import ASSOCIATION_SCHEMA
from vlei_oracle.association.schemas import association_schemer


def test_packaged_schema_matches_the_canonical_protocol():
    schema = association_schemer()
    canonical = (
        Path(__file__).resolve().parents[1]
        / "protocol/external-association-v1/association.schema.json"
    )
    assert schema.raw == canonical.read_bytes()
    assert schema.said == ASSOCIATION_SCHEMA
