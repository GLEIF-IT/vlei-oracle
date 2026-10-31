"""Select the next contract action from an association mapping and observed chain state.

This boundary has no RPC, signing, persistence, or scheduler effects. The caller
supplies current trusted-status selection and chain scope before acting.
"""

ZERO_IDENTITY = "0x" + "00" * 32


def next_action(association_mapping, *, identity, endorsement, valid):
    """Return (contract, function, arguments), or None when already reconciled.

    The wallet identity registry is a policy input hint. Change it only after
    clearing a different entity's old endorsement, and publish only after the
    hint matches. A revoked or unselected status withdraws only its exact association.
    """
    wallet = association_mapping["wallet"]
    expected = association_mapping["identityRef"]
    said = association_mapping["associationSaid"]
    current_identity, current_said = endorsement
    if not valid:
        if current_said == said and current_identity != ZERO_IDENTITY:
            return "endorsement", "withdrawEndorsement", [wallet, said]
        return None
    if current_identity not in (ZERO_IDENTITY, expected):
        return "endorsement", "withdrawEndorsement", [wallet, current_said]
    if identity not in (ZERO_IDENTITY, expected):
        return "identity", "removeIdentity", [identity, wallet, b""]
    if identity == ZERO_IDENTITY:
        return "identity", "registerIdentity", [expected, wallet, b""]
    if (current_identity, current_said) != (expected, said):
        return "endorsement", "publishEndorsement", [wallet, expected, said]
    return None
