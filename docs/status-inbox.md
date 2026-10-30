# Trusted verifier status and contract posting

`vlei-oracle server receive` accepts EOA Verifier webhooks and signs ACKs after
committing receipt, verifier status and ownership selection to LMDB. It does not start the legacy credential-verification or
Ethereum-posting workers. `server start` adds trusted-status contract posting;
`server legacy-start` retains the older combined workflow on independent stores.

## Start

```sh
vlei-oracle server receive \
  --verifier-aid "$VERIFIER_AID" --verifier-kel verifier.cesr \
  --chain-id 31337 --webhook-url http://localhost:9723/v1/status-events
```

Use a dedicated persistent `--name` (default `vlei-oracle-inbox`). Optional
`--base`, `--head-dir`, `--alias`, `--passcode` and `--incept-file` control native
identity/storage. The process prints its oracle AID. Give that AID to the verifier
as its webhook peer, and configure the same exact destination URL on both sides,
including any query string. Behind a proxy, use the external URL and preserve the
request path/query and signed headers. Use TLS at the deployment boundary.

`--verifier-kel` contains native CESR key events and their required attachments,
not a public key copied from a request header. Native KERI validation must accept
the configured verifier's key state before startup succeeds. Once stored, the
file is optional on restart. For rotation, supply the updated stream and restart;
there is no background key discovery. Service signatures support one transferable
Ed25519 key at threshold one. The native `/oobi/{aid}` route exports this receiver's
identity for introduction to the verifier. Do not change a store's trusted
verifier or chain; use another store for a different trust scope. A populated
inbox from before ownership selection has no selection history and is refused;
use fresh independent demonstration stores and redeliver verified status. Existing
data is preserved. Production migration is separate work.

## Receipt and ACK

POST the verifier's ordinary v1 JSON object to `/v1/status-events`. The
[producer's API note](https://github.com/GLEIF-IT/eoa-verifier/blob/f649452391e3cd3660a936205d55d51e38388db1/adrs/0002-verifier-result-object.md)
defines acceptance and retirement. The receiver supports these two states:

- `outcome: "valid"`: initially verified and accepted association.
- `outcome: "invalid", reasonCode: "REVOKED"`: verified association revocation.
  Retain the same `associationSaid` and `association` facts, increase `revision`,
  and set `eventId` to `associationSaid:revision`.

The oracle trusts the configured verifier's authenticated facts. It checks the
object's identifiers, revision and configured chain, derives the wallet from
`extId` and `identityRef` as Keccak-256 of the issuer AID's UTF-8 bytes. It does
not reverify the ACDC, wallet proof or account. `observation` is informational and
not required by the receiver; age never invalidates accepted status.

The inbox preserves immutable event bytes. Event receipt, the newest stored
status and selection commit in one transaction. Reordered older events and exact duplicates
are ACKed without replacing newer state. Revocation is terminal for that SAID.
Conflicting bytes at a revision or changed association facts return HTTP 409.
Selection policy may decline ownership while retaining and acknowledging the
verifier's valid status; it does not declare the credential cryptographically invalid.

HTTP 200 carries the signed body `{"eventId":"<said>:<revision>","accepted":true}`.
It acknowledges durable receipt, **not ownership approval or an on-chain endorsement**. Native KERI
signatures cover the exact body digest, method/path, destination, audience,
action, nonce and expiry. The ACK additionally binds the request attempt and HTTP
status. Transport nonces are persisted separately from application event IDs.
A retry uses the same event bytes with a new signature/nonce. A crash after commit
but before either side records the ACK is safe to redeliver. Invalid signatures
or replay return 401; malformed/unsupported events return 400. No error completes
delivery. Authentication expiry does not expire association validity.

## Ownership selection

`selecting.py` applies [ADR 0001](https://github.com/GLEIF-IT/eoa-verifier/blob/f649452391e3cd3660a936205d55d51e38388db1/adrs/0001-eoa-association-ownership-and-replacement.md)
to trusted facts. One canonical `extId` (chain/address) has one current association:

- Same LE holder AID: a strictly higher issuer-KEL issuance sequence supersedes
  the predecessor. LE credential SAIDs, assessment revisions and arrival times
  do not establish priority. Never compare different issuers' sequence numbers.
- Another LE: reject the competing claim while the current claim is active.
  Verified current-SAID revocation releases it. Previously rejected receipts do
  not activate automatically or on duplicate delivery; a new association is required.
- Old-SAID revocation cannot release its successor. Issuance history survives
  release, transfer and restart, so a superseded predecessor cannot return.
  A newer same-LE retirement arriving before its acceptance still supersedes the
  older claim; replaying either acceptance cannot restore it.

`statuses` retains verifier `status` separately from local `selection`:
`selected`, `superseded`, `rejected` or `revoked`. `claims` retains the current SAID
and highest known issuance sequence per issuer for that chain/address.
`OracleBaser.selected_association(ext_id)` reads the selected stored facts without
RPC or credential checks. The legacy management/transaction tables are unchanged.
No background selection worker is needed. Same-anchor
multiple-association detection remains deferred as described in the ADR appendix.

## Contract posting

```sh
export VLEI_ORACLE_POSTING_KEY=<dedicated-Ethereum-writer-key>
vlei-oracle server start \
  --verifier-aid "$VERIFIER_AID" --verifier-kel verifier.cesr \
  --chain-id 31337 --webhook-url http://localhost:9723/v1/status-events \
  --rpc-url http://localhost:8545 \
  --endorsement-registry "$ORACLE_REGISTRY" --identity-registry "$IDENTITY_REGISTRY"
```

The writer needs the deployed registries' permissions. One worker serializes
contract actions using the existing identity registration and endorsement
operations. A selected accepted association publishes its mapping; a retired
association withdraws only its own SAID. Stored selection is checked again after
RPC preparation. A transaction already broadcast may still land after replacement
or revocation; after its receipt, the next cycle applies current stored status.
No cancellation is attempted.

`--confirmations` defaults to 2 and `--posting-interval` to one second. The existing
transaction journal records signed transaction identity before submission, then
`pending`, `confirmed` or `reverted` receipt outcomes. A receipt is not a claim that
the mapping still exists: the worker reads actual contract state before choosing
another action. Duplicates do not require another send when the mapping agrees.
The worker never calls EOA Verifier, checks observation age or repeats account or
credential verification. RPC errors are logged; pre-submission errors retry on
later cycles. Reverts are recorded and current desired actions can retry.

An uncertain broadcast keeps its hash and failure reason pending across restart.
The worker polls receipts and does not allocate another nonce until it resolves.
A permanently dropped transaction therefore needs operator investigation; automatic
rebroadcast, cancellation, nonce reconciliation and reorg recovery are outside this
runtime. Never share its writer with another process. Do not reuse a legacy
workflow store or migrate a live legacy journal; demonstrate with independent
stores. Changing chain, writer or contract addresses requires another store.

## Checks

Install the test-only sender with `pip install -r tests/requirements.txt`. CI tests
installed wheels against Foundry 1.5.1 and comply revision
`f61846512bbc75255b82c25b1c4ac71bc2127c57`. For local contract tests, set
`VLEI_COMPLY_DIR` to that checkout with its submodules and put `forge`/`anvil` on PATH.
Missing prerequisites fail the contract suite. `test_status_posting.py` covers
accepted-to-published, replacement, exact withdrawal, duplicate receipt, pending
and uncertain sends, restart and a replacement during RPC preparation. Its facts
start after upstream initial verification. The [two-service qualification](two-service-qualification.md)
additionally runs witnessed native issuance through two installed CLI processes.

The signature adapter matches the merged verifier's profile without importing
that application at runtime. Interoperability tests guard this intentional small
cross-application duplication. KERI 1.2.19 and HIO 0.6.22 match that producer's pins;
KERI 1.2.19 requires HIO 0.6.22 exactly; do not override the resolver.
