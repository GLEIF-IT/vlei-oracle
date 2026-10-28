# Local association operations

This runbook describes the legacy combined workflow. For the independent EOA
Verifier/oracle deployment, use [trusted status](status-inbox.md).

Use fresh state and the pinned dependency pair. This runbook qualifies an
unpublished local prototype; deployments beyond Anvil need separate authorization.
Keep existing operator credentials and stores.

## Clean qualification

Requires Python 3.12.6+, authenticated `gh`, Git, and Foundry on `PATH`.

```sh
gh repo clone GLEIF-IT/vlei-oracle
cd vlei-oracle
python3.12 scripts/dev/prepare-demo.py --directory .demo-association
source .demo-association/demo.env
VLEI_COMPLY_DIR="$COMPLY_DIR" .demo-association/venv/bin/python -m pytest -q
(cd "$COMPLY_DIR" && forge test && python3 scripts/dev/test_startup.py)
"$COMPLY_DIR/scripts/dev/init-local-vlei-fixture.sh" --two-chain-check --process-check --kli-check
```

The dependency manifest pins schema and contract checkouts; the preparer installs
GLEIF Python packages in a fresh environment and initializes Solidity submodules.
It refuses dirty or mismatched checkouts. Choose a fresh directory when a pin
changes. The full fixture starts local Anvil instances and a witness, creates
fresh actors/stores, and stops its processes on exit. Process/KLI test namespaces
are printed and retained for inspection even on failure. Public keys in fixtures
are test keys; operator keys do not belong in files or shell history.

## Issue and present

For manual operation, establish the native GEDA-delegated QVI and LE source
chain, witnessed issuer KEL, registry, schema cache, and transport contacts using
supported KLI workflows. The fixture demonstrates these native APIs; it does
not provision an operator's identifiers. Read the root README ceremony commands:
construct the challenge, collect the wallet signature, consume the exact issuer
nonce in the persistent ledger, and issue `association.json` with `kli vc create
--credential`. Keep the same ledger across issuer ceremonies. A burned nonce
requires a fresh challenge; an interrupted issuance requires inspection of the
existing KLI escrows before retrying.

Present the finished credential using native IPEX transport after discovering
its recipient and delivery endpoints:

```sh
"$KLI" ipex grant --name "$LE_KEYSTORE" --base "$KERI_BASE" --alias "$LE_ALIAS" \
  --recipient "$ORACLE_AID" --said "$ASSOCIATION_SAID"
```

The exercise independently qualifies CESR HTTP presentation. It replays issuer
introductions locally and does not claim an end-to-end mailbox deployment.

## Verify and endorse

Start the oracle with a fresh base and the trusted root, schemas, selected local
RPC, chain ID, endorsement registry, and identity registry. Set a dedicated
writer key in the configured environment variable; grant its address the
registry's compliance-admin role. Keep this writer's nonce stream exclusive.

```sh
"$VLEI_ORACLE" server legacy-start --name oracle --alias oracle --base "$ORACLE_BASE" \
  --trusted-geda-aid "$GEDA_AID" --schema-dir "$VLEI_SCHEMA_TOOLS_DIR/schema/acdc" \
  --rpc-url "$RPC_URL" --chain-id "$CHAIN_ID" \
  --endorsement-registry "$ORACLE_REGISTRY" --identity-registry "$IDENTITY_REGISTRY"
```

Poll `/presentations/<SAID>`. `verificationState` records off-chain verification. Successful verification atomically
creates explicit managed membership; `state` then reports continuing reconciliation.
Verification-only mode stops with managed state `verified`; successful
verification removes its unfinished work record. Status includes no saved wallet
binding, account observation, copied Grant metadata, retry counts, or timestamps.
Native credentials supply identity even after revocation. Current KERI/TEL and
canonical-block account checks are reconstructed for every posting decision;
RPC failure stays pending. Status reads only local work and management state;
it does not make network requests to reconstruct evidence.
`endorsed` requires canonical confirmed contract effects. The default is two
confirmations and a one-second posting interval. Missing evidence or RPC failure
stays pending. Inspect verification and management failure reasons and the durable signed transaction
journal for ambiguous submissions; never discard the store to allocate a new
nonce. The journal keeps exact original/cancellation signed bytes and hashes,
association/action references, nonce and original gas price (for safe same-nonce
cancellation), completion outcome, and current blockage. Deployment scope is
stored once. Receipt/block snapshots and workflow transaction-hash lists are
absent; `transactions` in status reads the journal relationship directly.
RPC outage is unknown, not evidence that a signed attempt is absent. An unknown
consumed nonce requires resolving the writer's actual chain history. Restart the same service/store/deployment to reconcile saved identities.
Completed attempts are rechecked from RPC for reorg recovery, including signed
cancellations; an orphaned completion reopens the same signed attempt.

## Replace and revoke

Replacement requires a new ceremony nonce and wallet signature, a new native
association issuance, and another Grant. Supply the currently endorsed credential's
native issuance evidence too; missing evidence remains pending. Immutable issuer
KEL issuance order prevents old replays from replacing newer associations. Resolve
conflicting entity bindings explicitly before reuse of a wallet.

Revoke the exact association in its LE registry and deliver the resulting signed
KEL/TEL evidence to every relying oracle:

```sh
"$KLI" vc revoke --name "$LE_KEYSTORE" --base "$KERI_BASE" --alias "$LE_ALIAS" \
  --registry-name "$LE_REGISTRY_NAME" --said "$ASSOCIATION_SAID" --send "$ORACLE_AID"
```

A source LE revocation similarly invalidates dependent associations. Once the
oracle observes invalidity, it withdraws only matching SAIDs. A late revocation
for an old SAID cannot clear its replacement. Identity hints never replace the
exact current endorsement. During evidence/RPC outages, an existing assertion
can remain usable indefinitely: use independent emergency controls when needed.

## Compromise response

A compromised wallet key or LE controller invalidates the assurance premise.
Pause affected wrapper transfers through the authorized emergency role, revoke
and deliver affected native credentials, withdraw exact current endorsements
through an uncompromised authorized writer, and rotate/recover the controller
through native KERI governance. Reissue with fresh wallet control and nonces.

A compromised oracle posting key requires immediate role removal by an
uncompromised administrator, emergency pause, inspection of every affected
registry binding and writer nonce, and a replacement dedicated writer. A fresh
writer/deployment scope requires a fresh oracle operation store; preserve the old
journal to reconcile its outstanding signed effects. A compromised
verifier host also requires rebuilding native evidence from trusted sources
and rechecking current validity before publishing new results. Restart alone does not repair compromise.

The registry trusts its authorized writer. The local Chainlink AllowAll engine
and direct underlying-token bypass are deferred production controls. EOA proof
establishes ceremony-time control; private-key transfer or theft can be invisible.
