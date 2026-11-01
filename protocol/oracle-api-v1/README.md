# Oracle API v1

Oracle receives trusted Verifier status and selects/publishes a current wallet
association. It does not accept credential evidence, issue credentials, verify
wallet proofs or consume association nonces.

## Dependencies and trust

[Verifier API v1](https://github.com/GLEIF-IT/eoa-verifier/blob/master/protocol/verifier-api-v1/README.md)
is authoritative for event fields/vocabulary and the shared authenticated webhook
and ACK profile, including context strings, header coverage, signatures, replay
claims and request correlation. Oracle implements that profile without importing
Verifier at runtime. [External association v1](https://github.com/GLEIF-IT/eoa-verifier/blob/master/protocol/external-association-v1/README.md)
is owned by Verifier. [Comply's registry](https://github.com/GLEIF-IT/vlei-comply/blob/main/docs/oracle-registry.md)
owns methods, roles, events and mapping encoding.

Trust exactly the configured verifier AID, chain and absolute destination URL.
The peer key must come from locally accepted KERI state, not an incoming header.
`server receive` serves the inbox; `server start` adds publication. Both expose
`POST /v1/status-events` and the receiver's native KERIpy OOBI introduction route.
There is no public Oracle association-status GET endpoint.

## Validation and responses

Match the configured path/query, authenticate the exact body first, then decode
the producer's v1 object. Require canonical association/LE SAIDs and issuer AID,
a positive integer status revision and matching event identity. Accept producer
acceptance or explicit verified retirement. Validate nonnegative integer issuance
sequence and anchor SAID, and a canonical EOA extId on the configured chain.
Observation metadata is informational and is not required by this consumer.
Credential, proof and account verification are trusted producer responsibilities.

| Result | HTTP response | Durable application effect |
| --- | --- | --- |
| Valid new event | 200, authenticated producer-profile ACK | Receipt, newest status and selection commit together |
| Exact duplicate | 200, newly authenticated ACK | No repeated selection/effect |
| Valid older revision | 200, authenticated ACK | Receipt retained; newer status preserved |
| Wrong path/query or malformed/unsupported event | 400 | No event receipt |
| Invalid/missing signature, wrong peer or replay | 401 | No event receipt |
| Conflicting bytes at an event revision, changed association facts, reacceptance after revocation, or revocation revision before accepted revision | 409 | Application transaction aborts |

Authentication claims a transport nonce before decoding or application receipt.
Thus a validly signed malformed/conflicting event may consume that attempt nonce;
retry with fresh transport authentication. Error responses never complete delivery.
Sign the success ACK only after LMDB commit. A crash/signing failure after commit
is safe to redeliver. **ACK means durable receipt, not selected ownership or
confirmed endorsement.**

## Selection and replacement

One canonical chain/wallet extId has one selected association. Store the producer's
status separately from Oracle selection: selected, superseded, rejected or revoked.

- Same issuer: a strictly higher issuer-KEL issuance-anchor sequence supersedes
  the predecessor. LE credential SAIDs, status revisions and arrival timestamps do
  not establish priority. A later same-issuer retirement arriving before acceptance
  still prevents an older claim from remaining selected.
- Different issuer: retain/reject the competing claim while a current claim is
  active. Current-SAID verified revocation releases that claim. An earlier rejected
  association does not activate on duplicates or later revisions; a new association
  is required. Never compare sequence numbers from different issuers' KELs.
- Revocation is terminal for its association SAID. Old-SAID revocation cannot release
  a successor. Issuance history survives release, transfer and restart; a superseded
  predecessor cannot return. Distinct associations sharing an issuance anchor have
  no additional conflict-detection policy.

Selection never revokes upstream Verifier acceptance. See the
[selection ADR](../../adrs/0001-wallet-association-selection.md) for rationale.

## Publication and withdrawal

A selected accepted association is eligible for publication; exact-SAID retirement
requests withdrawal. Derive wallet from extId and the contract identity reference
from the issuer AID using Comply's encoding. Identity registration alone supplies
no endorsement.

Receipt, selection, signed transaction submission, confirmed transaction and actual
current registry state are distinct milestones. Recheck durable selection after
RPC preparation. An already broadcast transaction may still land after replacement
or revocation; after its receipt the next cycle applies current desired state.
Withdrawal of an older SAID must not remove its replacement.

This runtime journals signed intent before submission and polls confirmation. It
does not cancel/rebroadcast automatically, reconcile nonce conflicts or recover
reorgs comprehensively. A permanently unresolved attempt can block later actions.
There is no periodic credential/account revalidation or automatic mapping expiry.
See [operations](../../docs/status-inbox.md) for scope, keys, recovery and limitations,
and [qualification](../../docs/two-service-qualification.md) for maintained evidence.
