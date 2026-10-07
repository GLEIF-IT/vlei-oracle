"""The packaged artifact is a checked copy of the canonical protocol schema."""

from pathlib import Path

from keri.app import habbing

from vlei_oracle.constants import ASSOCIATION_SCHEMA
from vlei_oracle.schema_cache import association_schemer, pin_accepted_schemas
from vlei_oracle.serving import ServiceConfig, setup_service


def test_packaged_schema_matches_the_canonical_protocol():
    schema = association_schemer()
    canonical = (
        Path(__file__).resolve().parents[1]
        / "protocol/external-association-v1/association.schema.json"
    )
    assert schema.raw == canonical.read_bytes()
    assert schema.said == ASSOCIATION_SCHEMA


def test_schema_is_cached_before_ingestion():
    with habbing.openHby(name="test-schema-cache", temp=True) as hby:
        pin_accepted_schemas(hby)
        assert hby.db.schema.get(keys=(ASSOCIATION_SCHEMA,)).said == ASSOCIATION_SCHEMA


def test_service_uses_the_association_schema_by_default():
    runtime = setup_service(
        ServiceConfig(
            name="test-default-schema",
            temp=True,
            no_post=True,
            rpc_url="http://127.0.0.1:1",
        )
    )
    try:
        assert (
            runtime.hby.db.schema.get(keys=(ASSOCIATION_SCHEMA,)).said
            == ASSOCIATION_SCHEMA
        )
    finally:
        runtime.baser.close(clear=True)
        runtime.rgy.reger.close(clear=True)
        runtime.hby.close(clear=True)
