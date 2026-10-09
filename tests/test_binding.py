"""Immutable identity is readable without becoming an acceptance verdict."""

from pathlib import Path

import pytest
from keri.app import habbing
from keri.core import serdering
from keri.vdr import credentialing

from vlei_oracle.verification.constants import ASSOCIATION_SCHEMA
from vlei_oracle.verification.credentials import assess_association, reconstruct_binding
from vlei_oracle.verification.errors import MissingCredentialArtifactsError
from vlei_oracle.verification.schema_cache import pin_accepted_schemas


@pytest.fixture
def native_material():
    with habbing.openHby(name="binding", temp=True) as hby:
        pin_accepted_schemas(hby)
        rgy = credentialing.Regery(hby=hby, name="binding", temp=True)
        credential = serdering.SerderACDC(raw=(
            Path(__file__).resolve().parents[1]
            / "protocol/external-association-v1/eoa-v1/association.json"
        ).read_bytes())
        try:
            yield rgy, credential
        finally:
            rgy.reger.close(clear=True)


def test_stored_identity_does_not_confer_acceptance(native_material):
    rgy, credential = native_material
    rgy.reger.creds.pin(keys=(credential.said,), val=credential)
    binding = reconstruct_binding(rgy=rgy, said=credential.said, schema_said=ASSOCIATION_SCHEMA)
    assert binding["wallet"] == credential.attrib["extId"].split(":")[2]
    assert binding["associationSaid"] == credential.said
    assessment = assess_association(rgy=rgy, said=credential.said,
                                    schema_said=ASSOCIATION_SCHEMA, trusted_geda_aid="root")
    assert assessment.outcome == "unavailable"
    assert assessment.credential is None
    assert rgy.reger.saved.get(keys=(credential.said,)) is None


def test_missing_identity_remains_unavailable(native_material):
    rgy, credential = native_material
    with pytest.raises(MissingCredentialArtifactsError):
        reconstruct_binding(rgy=rgy, said=credential.said, schema_said=ASSOCIATION_SCHEMA)
    assert assess_association(rgy=rgy, said=credential.said,
                              schema_said=ASSOCIATION_SCHEMA, trusted_geda_aid="root").outcome == "unavailable"
