"""The packaged artifact is a checked copy of the canonical protocol schema."""

from pathlib import Path

from hio.base import doing
from keri.app import habbing
from keri.db import basing

from vlei_oracle.verification.constants import ASSOCIATION_SCHEMA
from vlei_oracle.verification.schema_cache import association_schemer, pin_accepted_schemas
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


def test_keripy_doers_own_all_service_store_lifecycles():
    runtime = setup_service(ServiceConfig(
        name="test-service-lifecycle", temp=True, no_post=True,
        rpc_url="http://127.0.0.1:1",
    ))
    stores = [doer.baser for doer in runtime.doers if isinstance(doer, basing.BaserDoer)]
    paths = [Path(store.path) for store in stores]
    assert len(stores) == 4 and all(path.exists() for path in paths)
    doing.Doist(real=True, tock=0.01, limit=0.03).do(doers=runtime.doers)
    assert all(store.env is None and not store.opened for store in stores)
    assert all(not path.exists() for path in paths)
    assert not runtime.hby.db.opened and not runtime.hby.ks.opened


def test_package_does_not_ship_retired_workflow_modules():
    from importlib.util import find_spec
    from importlib.resources import files

    for name in ("association_verifier", "constants", "contracts", "endpoints",
                 "eoa", "eoa_worker", "errors", "handling", "schema_cache"):
        assert find_spec("vlei_oracle." + name) is None
    assert not files("vlei_oracle").joinpath("schema_data").is_dir()
