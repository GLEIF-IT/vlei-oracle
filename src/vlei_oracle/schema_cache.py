"""Pinned ACDC schemas accepted by the GL1 vLEI Oracle."""

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
    """Build and validate the pinned GL1 Ethereum Wallet Attestation Schemer."""

    schemer = scheming.Schemer(raw=wallet_schema_raw())
    if schemer.said != WALLET_SCHEMA:
        raise ValueError(f"pinned wallet schema SAID {schemer.said} does not match expected {WALLET_SCHEMA}")
    return schemer


def pin_accepted_schemas(hby) -> None:
    """Pin all oracle-owned schemas into the Habery schema cache."""

    schemer = wallet_schemer()
    hby.db.schema.pin(keys=(schemer.said,), val=schemer)
