"""Pinned ACDC schemas accepted by the GL1 vLEI Oracle.

The service should not resolve GL1-specific schemas from a runtime schema
server. Instead it loads the bundled wallet-attestation schema as a KERI
``Schemer`` and pins it into the Habery schema cache before parsing credential
traffic. External vLEI schemas may still be resolved by KLI issuers in the e2e.
"""

from __future__ import annotations

from importlib.resources import files

from keri.core import scheming

from .constants import WALLET_SCHEMA

GL1_WALLET_SCHEMA_RESOURCE = "schema_data/acdc/gl1-ethereum-wallet-attestation.json"
ACCEPTED_SCHEMA_SAIDS = (WALLET_SCHEMA,)


def wallet_schema_raw() -> bytes:
    """Return the bundled GL1 Ethereum Wallet Attestation schema bytes."""

    return files("vlei_oracle").joinpath(GL1_WALLET_SCHEMA_RESOURCE).read_bytes()


def wallet_schemer() -> scheming.Schemer:
    """Build and validate the pinned GL1 Ethereum Wallet Attestation Schemer.

    The SAID check catches drift between the packaged schema bytes and the
    verifier constant before the service starts accepting presentations.
    """

    schemer = scheming.Schemer(raw=wallet_schema_raw())
    if schemer.said != WALLET_SCHEMA:
        raise ValueError(f"pinned wallet schema SAID {schemer.said} does not match expected {WALLET_SCHEMA}")
    return schemer


def pin_accepted_schemas(hby) -> None:
    """Pin all oracle-owned schemas into the Habery schema cache."""

    schemer = wallet_schemer()
    hby.db.schema.pin(keys=(schemer.said,), val=schemer)
