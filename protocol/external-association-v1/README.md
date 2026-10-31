# vLEI External Association v1

## File reference

- [README.md](README.md): Association purpose, structure, and source documents.
- [association.schema.json](association.schema.json): Common ACDC schema and field definitions.
- [eoa-v1/](eoa-v1/README.md): Ethereum wallet challenge, EOA profile, examples, and test vector.

## Overview

A vLEI External Association links a Legal Entity's vLEI-controlled AID to an
external identifier. The external controller signs a challenge, and the Legal
Entity embeds that signature in an ACDC it issues. A relying party can then
verify both the entity's authority and the external controller's agreement.

The association has an issuer and no issuee. Its LE vLEI edge identifies the
credential held by that issuer and supplies the authoritative LEI. The common
schema leaves identifier and signature formats to profiles such as the
[Ethereum EOA profile](eoa-v1/README.md).

The association stands on its own; an oracle may separately publish a verified
association mapping for on-chain use. See the [schema](association.schema.json) for field
constraints and the [paired qualification helpers](../../scripts/protocol/README.md) for a
complete delegated test chain presented to EOA Verifier. The oracle receives
authenticated accepted/revoked status and publishes selected mappings.

## Sources

- *Associating External Identifiers with a LE vLEI*: common methodology.
- *Associating an Ethereum EOA with a LE vLEI — EOA Implementation Profile*: Ethereum account signing and verification.
- *Associating an ENS Name with a LE vLEI — ENS Implementation Profile*: ENS associations and the distinction between verification and endorsement.
