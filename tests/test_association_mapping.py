"""Immutable identity is readable without becoming an acceptance verdict."""

from pathlib import Path

import pytest
from keri.app import habbing
from keri.core import serdering
from keri.vdr import credentialing

from vlei_oracle.verification.constants import ASSOCIATION_SCHEMA
from vlei_oracle.verification.credentials import assess_association, reconstruct_association_mapping
from vlei_oracle.verification.errors import MissingCredentialArtifactsError
from vlei_oracle.verification.schema_cache import pin_accepted_schemas


@pytest.fixture
def credential_material():
    """Own isolated KERIpy stores and one readable association fixture."""
    with habbing.openHby(name="association_mapping", temp=True) as hby:
        pin_accepted_schemas(hby)
        rgy = credentialing.Regery(hby=hby, name="association_mapping", temp=True)
        credential = serdering.SerderACDC(raw=(
            Path(__file__).resolve().parents[1]
            / "protocol/external-association-v1/eoa-v1/association.json"
        ).read_bytes())
        try:
            yield rgy, credential
        finally:
            rgy.reger.close(clear=True)


def test_stored_identity_does_not_confer_acceptance(credential_material):
    """Readable mapping fields do not supply missing KERIpy acceptance evidence."""
    rgy, credential = credential_material
    rgy.reger.creds.pin(keys=(credential.said,), val=credential)
    association_mapping = reconstruct_association_mapping(rgy=rgy, said=credential.said, schema_said=ASSOCIATION_SCHEMA)
    assert association_mapping["wallet"] == credential.attrib["extId"].split(":")[2]
    assert association_mapping["associationSaid"] == credential.said
    assessment = assess_association(rgy=rgy, said=credential.said,
                                    schema_said=ASSOCIATION_SCHEMA, trusted_geda_aid="root")
    assert assessment.outcome == "unavailable"
    assert assessment.credential is None
    assert rgy.reger.saved.get(keys=(credential.said,)) is None


def test_missing_identity_remains_unavailable(credential_material):
    """A missing credential remains retryable rather than becoming a rejection."""
    rgy, credential = credential_material
    with pytest.raises(MissingCredentialArtifactsError):
        reconstruct_association_mapping(rgy=rgy, said=credential.said, schema_said=ASSOCIATION_SCHEMA)
    assert assess_association(rgy=rgy, said=credential.said,
                              schema_said=ASSOCIATION_SCHEMA, trusted_geda_aid="root").outcome == "unavailable"
