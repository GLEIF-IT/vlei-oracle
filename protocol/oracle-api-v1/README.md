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
Both also expose GET /v1/endorsements/{associationSaid}, a passive local resource.

## Validation and responses

Match the configured path/query, authenticate the exact body first, then decode
the producer's v1 object. Require canonical association/LE SAIDs and issuer AID,
a positive integer status revision and matching event identity. Accept producer
acceptance or explicit verified revocation. Validate nonnegative integer issuance
sequence and anchor SAID, and a canonical EOA extId on the configured chain.
Observation metadata is informational and is not required by this consumer.
Credential, proof and account verification are trusted producer responsibilities.

| Result | HTTP response | Durable application effect |
| --- | --- | --- |
| Valid new event | 200, authenticated producer-profile ACK | Receipt, newest status, selection and queued work commit together |
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
  not establish priority. A later same-issuer revocation arriving before acceptance
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

A selected accepted association is eligible for publication; exact-SAID revocation
requests withdrawal. Derive wallet from extId and the contract identity reference
from the issuer AID using Comply's encoding. Identity registration alone supplies
no endorsement.

Receipt queues work atomically; duplicate receipt never schedules it again. The
writer advances one operation through `queued`, `preparing`, `pending`, then
`completed` or `dead`. Phase moves, confirmed effects and journal outcomes commit
atomically. Cursors close before RPC yields. Completed history is never scanned
for work. A no-effect selection still completes its operation visibly.

Contract steps come from current local selection and this writer's confirmed
effects, not periodic contract mapping reads. First publication registers identity
then endorses. Same-identity replacement publishes the new SAID. Entity changes
withdraw, remove identity, register identity, then publish. Revocation withdraws
the exact association; the identity hint can remain. Recheck selection after RPC
preparation. Resolve an already signed transaction before applying newer status.

Persist signed bytes/hash before submission. Preparation and definite reverted
transactions have a finite per-step budget (default three attempts). Interrupted
pending RPCs also have a three-failure budget; receipt waiting has a persistent
120-second deadline. An uncertain submission can retry only the same signed
bytes after checking its receipt. Exhaustion moves work to `dead`. An unresolved
signed dead letter fences the writer; no new nonce is allocated until manual
retry resolves it. Failure does not revoke verifier acceptance or erase prior
confirmed effects. Successful receipt processing ends with no further polling
once all operation steps complete. There is no mapping-drift repair, automatic
expiry, nonce replacement/cancellation or comprehensive reorg recovery.

## Endorsement resource

`GET /v1/endorsements/{associationSaid}` returns 200 for a known association or
404 for an unknown SAID. It needs no service signature and makes no RPC calls.
The response describes only the requested association:

| Field | Meaning |
|---|---|
| `associationSaid` | The requested credential SAID. |
| `wallet` | Its associated wallet address. |
| `publicationStatus` | `published` only when the Oracle's last confirmed publication for that wallet names this exact SAID; otherwise `not_published`. |
| `action` | `publish` for an accepted association's request, or `withdraw` for a revoked association's request. |
| `deliveryStatus` | `queued`, `in_progress`, `completed`, `failed`, `superseded`, or `rejected`. |
| `error` | This request's latest error, or an empty string. |

`publicationStatus` describes local confirmed knowledge, not a fresh contract
mapping read. A pending or failed withdrawal may still report `published`.
`not_published` does not assert that the association was previously published:
it also covers a never-published or replaced association.

`deliveryStatus` describes the latest request. `completed` means no further
contract step is required, including a withdrawal when that association is
already absent. `failed` requires manual intervention after the bounded budget.
An accepted association whose finished request is no longer eligible reports
`superseded` or `rejected`; in-flight work remains `queued` or `in_progress`
until processed. `action` identifies that association's request, not an internal
identity-registry step or a transaction affecting a predecessor.

The response excludes internal queue records, operation IDs, retry counters,
wallet-level identity/endorsement records and global posting blockage. There
are no legacy field aliases. Signed bytes and signing keys are never exposed.
Offline recovery uses the operation ID from the operator's local dead-letter
records; this public resource is not a recovery/debugging interface.

Operate one writer against known initial empty registry state and preserve its
store. This is a breaking storage change: use fresh stores, with no migration or
old-format reader. See [operations](../../docs/status-inbox.md) for local dead-letter
retry and [qualification](../../docs/two-service-qualification.md) for evidence.
