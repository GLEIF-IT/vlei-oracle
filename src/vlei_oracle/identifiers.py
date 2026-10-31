"""Canonical external identifiers consumed from trusted verifier status."""

import re


def ext_id_chain(ext_id: str) -> int:
    """Return the chain ID from a canonical EOA CAIP-10 identifier.

    Committed identifiers must already be normalized; aliases are rejected.
    """
    if not isinstance(ext_id, str) or not re.fullmatch(
        r"eip155:[1-9][0-9]{0,31}:0x[0-9a-f]{40}", ext_id
    ):
        raise ValueError("external identifier must be canonical EOA CAIP-10")
    chain_id = int(ext_id.split(":")[1])
    if chain_id >= 2**256:
        raise ValueError("chain ID exceeds uint256")
    return chain_id
