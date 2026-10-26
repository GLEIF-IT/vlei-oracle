# vLEI association oracle

The new [trusted status receiver](docs/status-inbox.md), `server receive`, accepts
authenticated EOA Verifier events into a durable inbox and returns signed ACKs.
It performs no Ethereum posting or independent association verification. The
combined `server start` workflow described below remains available unchanged.

The oracle receives a finished Legal Entity External Association ACDC through
IPEX and verifies its GLEIF-rooted KERI/TEL chain, embedded EIP-712 proof, and
Ethereum EOA account type. Verification uses accepted KERIpy stores; RPC waits
run as HIO tasks. Durable verification work recovers read-only checks after restart.

A Legal Entity controller builds and signs the challenge before issuing the
untargeted association. The association holds the exact nonce in `u`, follows
its LE credential edge for entity identity, and embeds the external proof under
its SAID. There is no OOR approval or intrinsic expiry.

This prototype verifies associations and maintains their contract endorsements.
An on-chain consumer trusts the oracle's
result; it does not perform vLEI verification. See the [protocol](protocol/external-association-v1/README.md)
and [real issuance exercise](scripts/protocol/README.md) for scope and setup.
The [operations runbook](docs/association-operations.md) covers clean qualification,
replacement, revocation, recovery, and compromise.

## Code organization

- `receiving/` owns HTTP routes, native KERI parsing/escrow scheduling, and
  accepted Grant ingestion. It creates durable work without deciding validity.
- `verification/` owns credential evidence, EOA proof/account checks, and the
  coordinator that retries work and atomically enrolls successful associations. Its worker
  uses HIO for Ethereum observations; schema resources live here too.
- `endorsement/` owns action selection, contract effects, and durable transaction
  reconciliation. It reuses verification checks before effects and owns ABI resources.
- `basing.py` owns application persistence; `rpc.py` provides the shared yielding
  request helper. Neither imports the workflows that use it.
- `serving.py` composes the native stores, clients, and doers. `commands/` contains
  CLI adapters, including the local deployment-log parser.

Endorsement may call verification; verification does not call endorsement.
Receiving and verification coordinate through durable verification work. Service
composition connects the packages, while each doer keeps its resource ownership.

## Install

Use a fresh Python 3.12.6+ environment: GLEIF packages own the `keri` and `hio`
import paths and must not share an environment with upstream distributions.

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e '.[dev]' -r scripts/protocol/requirements.txt
.venv/bin/python scripts/dev/prepare-demo.py --schemas-only
.venv/bin/python -m pytest -q
```

## Ceremony

The issuer supplies its canonical AID, exact LE vLEI SAID, wallet and chain, and
a 1–256 character nonce. The external controller signs with a wallet or the
local test-key helper:

```sh
.venv/bin/vlei-oracle challenge --issuer-aid "$LE_AID" --le-vlei-said "$LE_SAID" \
  --wallet "$WALLET" --chain-id "$CHAIN_ID" --nonce "$NONCE" --out challenge.json
.venv/bin/vlei-oracle client sign --challenge challenge.json \
  --private-key "$TEST_WALLET_KEY" --out signature.txt
.venv/bin/python scripts/protocol/ceremony.py --challenge challenge.json \
  --signature signature.txt --registry "$LE_REGISTRY_SAID" \
  --nonce-store issuer-nonces.sqlite3 --out association.json
```

The ceremony validates the signature and canonical profile, then consumes the
nonce durably for that issuer before exposing credential bytes. Reuse the same
issuer nonce ledger across ceremonies. A duplicate nonce fails, including after
restart or concurrent finalization. Consumption is irreversible: a crash after
consumption may burn the nonce. Generate a fresh challenge if no finished artifact
survived. After an issuance interruption, inspect the existing KLI state and resume its
native escrows. Do not blindly repeat credential creation or finalize another
artifact with that nonce.

Issue the finished credential through the Legal Entity's existing registry:

```sh
.venv/bin/kli vc create --name "$LE_KEYSTORE" --base "$KERI_BASE" --alias "$LE_ALIAS" \
  --registry-name "$LE_REGISTRY_NAME" --credential @association.json
```

KLI requires the association and source schemas in the issuer's cache, an
accepted LE credential held by that AID, and its registry/witness configuration.
The test exercise supplies those using native `Credentialer`/`Registrar` APIs.
The canonical schema lives under `protocol/`; the package contains an exact
checked copy for installed-service use. Schema OOBI discovery and witness
qualification are documented alongside the exercise as they are exercised.

Present the finished ACDC and required supporting KEL/TEL evidence with `kli ipex
grant` or native KERI CESR HTTP delivery. Addressed transport does not make the
association a targeted credential.

## Service

```sh
.venv/bin/vlei-oracle server start --name oracle --alias oracle \
  --trusted-geda-aid "$GEDA_AID" --schema-dir .demo/checkouts/vLEI/schema/acdc \
  --rpc-url http://127.0.0.1:8545 --no-post
```

- `POST /` receives CESR traffic through KERIpy direct mode.
- `GET /presentations/<association SAID>` returns durable verification status.
- `GET /health` returns liveness and operation counts.

Proofs arrive inside the ACDC. There is no signature-attachment endpoint or
legacy schema acceptance. Missing evidence and RPC failures remain pending;
established invalidity fails. The coordinator rechecks credential status before
committing an asynchronous result. Account observations retain chain, canonical
block hash/number, code and time off-chain.

To maintain endorsements, omit `--no-post` and supply `--chain-id`,
`--endorsement-registry`, and `--identity-registry`. Set the dedicated writer's
key in `VLEI_ORACLE_POSTING_KEY` (or name another variable with
`--posting-key-env`); the writer requires the registry's compliance-admin role.
`--confirmations` defaults to two and `--posting-interval` to one second.
The writer must not share its nonce stream with other processes.

Successful posting progresses to `endorsed`. Current evidence is rechecked
before each effect; observed revocation causes exact-association withdrawal and
`withdrawn`. The store binds the chain, deployments, and writer, and persists
signed transaction identities before broadcast. Restart reconciles receipts,
nonces, and current registry state. Unknown consumed nonces require operator
reconciliation and appear in the transaction journal's current failure reason.
Replacing an existing assertion requires its native issuance evidence; an
unknown on-chain association stays pending. Within one entity, immutable KEL
issuance order prevents an older association from overwriting a newer one.
Changing entities requires explicit withdrawal and resolution of conflicting
managed bindings.

RPC or missing-evidence outages leave current endorsements in place. Polling
latency includes the managed association and journal counts, RPC waits, and confirmations; there is no
unconditional finite stale-authority bound. Emergency pause and authorized exact
withdrawal are independent operational controls. EOA key transfer remains
unobservable; an endorsement does not establish continuous wallet control.

Use fresh stores for this prototype; old operation stores are not upgraded.
The issuance exercise isolates its test stores and local Anvil. It does not
modify existing operator credentials or deploy to another network.


## Data ownership

Application state holds unfinished verification (association and native Grant
references, progress, current blockage), explicit managed membership (association,
reconciliation state, current blockage), and signed-effect recovery (exact original
and cancellation identities, association/action, nonce/fee, completion outcome).
One deployment/writer scope guards recovery; the issuer ledger holds only consumed
issuer/nonce pairs. Withdrawn membership remains to prevent older associations
regaining authority. Native KERI stores own credential identity and issuance order;
current validity, account code, registry state, receipts, and proposed actions are
reconstructed for each decision. Queues and read-only retry timing stay in memory.
Use fresh stores for this roll-forward schema; preserve existing operator stores.
