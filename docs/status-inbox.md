# Operate the trusted-status Oracle

Oracle authenticates Verifier status, commits status-event storage and selection, then optionally
publishes or withdraws contract endorsements. [Oracle API v1](../protocol/oracle-api-v1/README.md)
owns validation/errors, duplicate handling and selection. The shared delivery
profile belongs to [Verifier API v1](https://github.com/GLEIF-IT/eoa-verifier/blob/master/protocol/verifier-api-v1/README.md).
A storage ACK does not mean selection or confirmed publication.

## Start

```sh
vlei-oracle server receive \
  --verifier-aid "$VERIFIER_AID" --verifier-oobi "$VERIFIER_OOBI" \
  --chain-id 31337 --webhook-url http://localhost:9723/v1/status-events
```

Use a dedicated persistent `--name` (default `vlei-oracle-inbox`). Optional
`--base`, `--head-dir`, `--alias`, `--passcode` and `--incept-file` control KERIpy
identity/storage. The process prints its oracle AID. Give that AID to the verifier
as its webhook peer, and configure the same exact destination URL on both sides,
including any query string. Behind a proxy, use the external URL and preserve the
request path/query and signed headers. Use TLS at the deployment boundary.

`--verifier-oobi` is the operator-selected HTTP(S) introduction URL. Oracle
starts its listener and native KERIpy resolver without importing a KEL file.
`POST /v1/status-events` returns HTTP 503 with Retry-After while resolution is
unfinished, failed, or resolves an AID other than `--verifier-aid`. A resolved
marker or response header alone cannot authorize a producer: its expected AID
must have KERIpy-accepted key state. No event or posting work is stored while
this introduction is unavailable. Existing posting obligations remain durable.

Every startup refreshes the same OOBI using the retained KERI store. After producer
rotation, restart Oracle to resolve current key events; no CESR file exchange is
needed. Correct failed/unavailable introductions and restart; KERIpy owns its
normal OOBI retry/escrow behavior. There is no background key discovery from request
headers and no periodic refresh of a completed OOBI. Signatures support one
transferable Ed25519 key at threshold one. `/oobi/{aid}` exports Oracle's public
introduction; configure that URL as Verifier's optional `--webhook-oobi`.
Use fresh application stores for
demonstrations and preserve existing operator data separately. There are no
storage migrations or previous-format readers.

## Contract posting

```sh
export VLEI_ORACLE_POSTING_KEY=<dedicated-Ethereum-writer-key>
vlei-oracle server start \
  --verifier-aid "$VERIFIER_AID" --verifier-oobi "$VERIFIER_OOBI" \
  --chain-id 31337 --webhook-url http://localhost:9723/v1/status-events \
  --rpc-url http://localhost:8545 \
  --endorsement-registry "$ORACLE_REGISTRY" --identity-registry "$IDENTITY_REGISTRY"
```

The writer needs the deployed registries' permissions and exclusive ownership of
its Ethereum account. Status-event acceptance commits selection and a queued operation
before ACK. One operation progresses through dedicated LMDB phase subdatabases:
`queued.`, `preparing.`, `pending.`, `completed.` and `dead.`. `wallet-effects.` records
only this writer's confirmed effects; `txns.` retains signed attempt history.
`inbox.` and `statuses.` retain event and verifier-status history. None of these
history tables is scanned to schedule completed work.

Posting reads pending, preparing and queued work through separate bounded
queries. Its scheduler makes priority explicit; each persistence transition has
fixed source and destination tables (`queued_to_prep`,
`prep_to_pending`, `pending_to_prep`, and terminal/recovery operations).
There is no phase-string dispatcher. Preparation failures and pending RPC failures
have separate operations and budgets. Offline recovery prepares the reset record
and writer fence before the dead-to-preparing or dead-to-pending commit.
Signed transaction bytes, transaction-receipt progress and confirmed effects retain their
atomic commit boundaries.

`--confirmations` defaults to 2, `--posting-interval` to one second,
`--max-attempts` to 3 and `--pending-timeout` to 120 seconds. Each completed step
resets the preparation/revert attempt budget. Pending RPC failures are bounded
separately; transaction-receipt waiting survives restart using the persisted signing time.
A timeout or error is delivery progress, not credential invalidity. The service
continues receiving signed status while posting is waiting or dead-lettered.

Query local progress without querying Ethereum:

```sh
curl "http://localhost:9723/v1/endorsements/$ASSOCIATION_SAID"
```

This resource exposes only the requested association, wallet, locally confirmed
`publicationStatus`, requested `action`, `deliveryStatus`, and its `error`.
Queue records, retry counters, wallet-wide bookkeeping and global posting
blockage remain internal. See the
[response contract](../protocol/oracle-api-v1/README.md#endorsement-resource).
A completed operation stays terminal; replacement may make the old association
report `superseded` and `not_published` without scheduling new work. Neither GET
nor an idle worker reads contract mappings. Independent changes on-chain are outside this operating model.

After correcting a dead letter, stop the service and explicitly retry its ID:

```sh
vlei-oracle retry "$OPERATION_ID" --name vlei-oracle-inbox
```

Pass the same `--base` and `--head-dir` used by the service. Restart normally.
Retry renews the bounded attempt/deadline budget. If signed bytes already exist,
retry retains their exact hash/nonce and checks the transaction receipt before resubmission.
An unresolved signed dead letter blocks every later operation; unsigned failures
or confirmed reverts do not fence unrelated work. Do not clear that fence or
allocate another nonce by editing LMDB. There is no automatic cancellation,
replacement, full nonce reconciliation or comprehensive reorg recovery.

Use a known empty initial registry deployment and fresh application stores for
this breaking storage layout. Preserve the application store across restart;
there are no migrations, history reconstruction or backward-compatible readers.
Operators select compatible stores when changing writer, chain or contracts;
startup warns about differences and continues as described below.
Storage remains under KERIpy's name/base/head-dir conventions in `vlei-oracle/db`.

## Configuration changes and stored state

Operators own the decision to reuse state with different settings. At startup,
Oracle compares two small records with their previous values:

- Inbox: trusted Verifier AID and chain ID.
- Posting, when enabled: chain ID, Ethereum writer address, endorsement registry
  and identity registry addresses.

Differences produce logger warnings with previous/current public values and do
not block startup. Private keys are never logged. Each record is updated to the
new settings, so an unchanged restart is quiet and changing back warns again.
Missing records are initialized without history reconstruction. Receive-only
startup leaves the last posting settings available for comparison when posting
is next enabled.

No state conversion accompanies a warning. Received statuses, selection, queues,
signed journal entries, recovery fences and confirmed effects remain as stored.
Completed work is not republished automatically for new contracts. An existing
signed transaction still targets its original deployment; changing a writer key
does not replace its signature or cancel it. Review and resolve pending work
before changing deployments. For an independent deployment, use separate stores
and retain the original journal for recovery. Do not clear queues or fences
merely to silence a configuration warning.

Peer authentication, payload validation, RPC chain checks and contract permissions
still apply. Advisory startup settings do not bypass those checks or establish
that old records describe the newly configured deployment.

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

## Record vocabulary

Names identify domain objects and their representations through source, tests
and public status: `postingOperation` is a `PostingOperation`, `txRecord` is a
`TransactionRecord`, and `walletEffects` contains only this writer's confirmed
effects. A `contractCall` is `(contractName, functionName, args)`; the persisted
`functionName` is only the function-name string, not that tuple. Signed bytes
remain a transaction record after confirmation or revert.

Internal posting records retain `functionName`. The public endorsement resource
exposes only the association-level `action` (`publish` or `withdraw`), not the
internal contract function or serialized operation. No old response aliases are
provided.

## Application and persistence boundaries

`OracleBaser` owns only LMDB lifecycle and typed table bindings. Feature readers (`AssociationReader` and `PostingReader`)
return detached records from bounded lookups and close every read transaction
before returning. `StatusEventWriter` and `PostingWriter` serialize prepared values before a short write
transaction and expose explicit source-to-destination transitions. Integrity
checks inside those writes protect phase membership and immutable signed bytes;
they do not select wallets, decide retries or perform RPC.

`StatusEventService` validates event history and prepares selection and queued work.
It runs synchronously without yielding between reads and its atomic commit.
`PostingService` chooses contract steps and prepares transaction-receipt outcomes, retries,
dead letters and confirmed effects. It never holds an LMDB transaction across
RPC; after preparation yields, selection is checked again before signing.
`PostingDoer` only schedules this service beside its RPC effect clients.
`RecoveryService` prepares an explicit offline retry, and `EndorsementQuery`
projects bounded local records for the thin HTTP resource. Startup functions
`note_inbox` and `note_posting` read/save public settings through `ScopeStore`
and log changes; no scope service or immutable configuration binding remains.

Run one receiver/posting process per application store. The runtime uses one
cooperative application writer; it does not support concurrent independent
receiver processes sharing the same store. Stop it before offline recovery.
No additional mutex or lock is used. This API refactor changes no wire values,
phase tables, journal layout or atomic commit boundaries and provides no old
Python API aliases. See the [implementation plan](plans/application-persistence-separation.md).

## Request verification and optional signed responses

Oracle opts into verifying every incoming webhook and authorizes only
`--verifier-aid`, using KERIpy-validated key state from `--verifier-oobi`.
It signs its storage response after commit. Verifier resolves Oracle's OOBI with `--webhook-oobi`
to verify that signature, without a destination-AID setting. Other webhook targets
can return unsigned success without any KERI identity or state exchange.

Duplicate status events are handled by durable event ID/body and revision rules,
not transport nonces. Keep Oracle's signing identity and restart to refresh the configured OOBI after
rotation. HTTP success, durable Oracle storage, selection and confirmed Ethereum
publication remain distinct. Use HTTPS; the signature profile covers method/path
and body digest, not HTTP status, origin/query or per-attempt freshness.
