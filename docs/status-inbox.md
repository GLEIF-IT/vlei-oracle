# Trusted verifier status inbox

`vlei-oracle server receive` accepts EOA Verifier webhooks and signs ACKs after
committing receipt, verifier status and ownership selection to LMDB. It does not start the legacy credential-verification or
Ethereum-posting workers. `server start` retains the older combined workflow.

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
[producer's API note](https://github.com/GLEIF-IT/eoa-verifier/blob/7cf78be7bcb9fe98eab7919ae8ffbd2bb6ad5529/adrs/0002-verifier-result-object.md)
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

`selecting.py` applies [ADR 0001](https://github.com/GLEIF-IT/eoa-verifier/blob/7cf78be7bcb9fe98eab7919ae8ffbd2bb6ad5529/adrs/0001-eoa-association-ownership-and-replacement.md)
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
No background selection worker or contract submission is introduced. Same-anchor
multiple-association detection remains deferred as described in the ADR appendix.

## Checks

Install the test-only sender with `pip install -r tests/requirements.txt` and run
`pytest -q tests/test_status_inbox.py tests/test_selection.py`. Tests exercise native peer identities, real
HIO HTTP, automatic retry across both ACK loss windows, ordering/conflicts and rotation.
Selection tests cover replacement, transfer, delayed revocation, durable history
and receipt/selection rollback before ACK.
Fixture facts begin after initial verification; end-to-end native credential and
contract qualification remains a later increment. CI tests installed wheels and
the existing non-contract regression suite. Local `test_contract_effects.py`
requires Foundry and `VLEI_COMPLY_DIR`; it is explicitly outside this CI job.

The signature adapter matches the merged verifier's profile without importing
that application at runtime. Interoperability tests guard this intentional small
cross-application duplication. KERI 1.2.17 and HIO 0.6.21 match that producer's pins;
this is a compatibility baseline, not a claim that HIO 0.6.21 is the latest release.
