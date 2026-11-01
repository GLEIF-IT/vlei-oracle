# Operate the trusted-status Oracle

Oracle authenticates Verifier status, commits receipt/selection, then optionally
publishes or withdraws contract endorsements. [Oracle API v1](../protocol/oracle-api-v1/README.md)
owns validation/errors, duplicate handling and selection. The shared delivery
profile belongs to [Verifier API v1](https://github.com/GLEIF-IT/eoa-verifier/blob/master/protocol/verifier-api-v1/README.md).
A storage ACK does not mean selection or confirmed publication.

## Start

```sh
vlei-oracle server receive \
  --verifier-aid "$VERIFIER_AID" --verifier-kel verifier.cesr \
  --chain-id 31337 --webhook-url http://localhost:9723/v1/status-events
```

Use a dedicated persistent `--name` (default `vlei-oracle-inbox`). Optional
`--base`, `--head-dir`, `--alias`, `--passcode` and `--incept-file` control KERIpy
identity/storage. The process prints its oracle AID. Give that AID to the verifier
as its webhook peer, and configure the same exact destination URL on both sides,
including any query string. Behind a proxy, use the external URL and preserve the
request path/query and signed headers. Use TLS at the deployment boundary.

`--verifier-kel` contains CESR key events and their required attachments,
not a public key copied from a request header. KERI validation must accept
the configured verifier's key state before startup succeeds. Once stored, the
file is optional on restart. For rotation, supply the updated stream and restart;
there is no background key discovery. Service signatures support one transferable
Ed25519 key at threshold one. The KERIpy `/oobi/{aid}` route exports this receiver's
identity for introduction to the verifier. Do not change a store's trusted
verifier or chain; use another store for a different trust scope. Use fresh
application stores for demonstrations and preserve existing operator data
separately. There are no storage migrations or previous-format readers.

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
Malformed HTTP chunks, non-object JSON-RPC envelopes and missing `result` fields
fail the RPC operation while signed status receipt remains available. The affected
client closes, and an existing pending signed transaction stays in the journal.

An uncertain broadcast keeps its hash and failure reason pending across restart.
The worker polls receipts and does not allocate another nonce until it resolves.
A permanently dropped transaction therefore needs operator investigation; automatic
rebroadcast, cancellation, nonce reconciliation and reorg recovery are outside this
runtime. Never share its writer with another process. Use fresh demonstration
stores. Changing chain, writer or contract addresses requires another store.

## Tests and limitations

Install test-only producer requirements from `tests/requirements.txt`; this is not
an Oracle runtime dependency. Contract/CI and demo revisions are explicit in
`scripts/integration/dependencies.json`, consumed by `.github/workflows/tests.yml`. Use the
qualified checkout with submodules and Foundry 1.5.1 on PATH as `VLEI_COMPLY_DIR`.
See [testing](testing.md) and [two-service qualification](two-service-qualification.md).

Transport interoperability is tested against the installed producer while the
small adapters remain independent. Both applications pin GLEIF KERI 1.2.19 and
HIO 0.6.22. Keep those package pins; do not install overlapping upstream keri/hio
distributions. The known KERIpy receipt-couple shortage can monopolize producer
intake on malformed evidence; a valid-stream qualification does not establish
malformed-stream liveness. See [Verifier operations](https://github.com/GLEIF-IT/eoa-verifier/blob/master/docs/operations.md).
