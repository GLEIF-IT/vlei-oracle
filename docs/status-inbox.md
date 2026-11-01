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

The writer needs the deployed registries' permissions and exclusive ownership of
its Ethereum account. Status receipt commits selection and a queued operation
before ACK. One operation progresses through native LMDB phase subdatabases:
`queued.`, `preparing.`, `pending.`, `completed.` and `dead.`. `applied.` records
only this writer's confirmed effects; `txns.` retains signed attempt history.
`inbox.` and `statuses.` retain event and verifier-status history. None of these
history tables is scanned to schedule completed work.

`--confirmations` defaults to 2, `--posting-interval` to one second,
`--max-attempts` to 3 and `--pending-timeout` to 120 seconds. Each completed step
resets the preparation/revert attempt budget. Pending RPC failures are bounded
separately; receipt waiting survives restart using the persisted signing time.
A timeout or error is delivery progress, not credential invalidity. The service
continues receiving signed status while posting is waiting or dead-lettered.

Query local progress without querying Ethereum:

```sh
curl "http://localhost:9723/v1/endorsements/$ASSOCIATION_SAID"
```

This resource exposes selection, delivery phase, attempts, errors, transaction
hashes and last confirmed wallet effects. `completed` is terminal until a new
verifier event creates another obligation. Neither GET nor an idle worker reads
contract mappings. Independent changes on-chain are outside this operating model.

After correcting a dead letter, stop the service and explicitly retry its ID:

```sh
vlei-oracle retry "$OPERATION_ID" --name vlei-oracle-inbox
```

Pass the same `--base` and `--head-dir` used by the service. Restart normally.
Retry renews the bounded attempt/deadline budget. If signed bytes already exist,
retry retains their exact hash/nonce and checks the receipt before resubmission.
An unresolved signed dead letter blocks every later operation; unsigned failures
or confirmed reverts do not fence unrelated work. Do not clear that fence or
allocate another nonce by editing LMDB. There is no automatic cancellation,
replacement, full nonce reconciliation or comprehensive reorg recovery.

Use a known empty initial registry deployment and fresh application stores for
this breaking storage layout. Preserve the application store across restart;
there are no migrations, history reconstruction or backward-compatible readers.
Changing writer, chain or contract addresses requires a separate deployment/store.
Storage remains under KERIpy's name/base/head-dir conventions in `vlei-oracle/db`.

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
