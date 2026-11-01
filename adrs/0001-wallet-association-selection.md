# ADR 0001: Select one current association for each chain and wallet

Status: Accepted; implemented
Date: 2026-10-31

Verifier acceptance proves an association under its configured policy. An Oracle
deployment still needs one current LE claim for a canonical chain/wallet, so
selection belongs here rather than in credential verification. LE identity is
the issuer AID, verified by the producer against the LE credential's holder.

Use strictly later issuance within the same issuer's KEL. A different issuer
cannot replace an active claim until the current association's verified revocation
releases it. Never compare sequence numbers across issuers. Retain issuance
history after release so a superseded predecessor cannot return. Reordered
revocation of an older association cannot withdraw its successor.

Commit status event, newest verifier status and selection in one LMDB transaction.
Keep local selection distinct from the producer's accepted/revoked status. A
rejected claim is still durably received; its ACK does not authorize publication.
Submission and confirmation remain separate from selection.

[oracle-api-v1](../protocol/oracle-api-v1/README.md) owns the complete rules and
error/duplicate handling. No same-event conflict detector or retrospective
withdrawal of verifier acceptance is implemented.
