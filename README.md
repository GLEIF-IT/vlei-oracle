# vLEI association oracle

The [trusted-status service](docs/status-inbox.md), `server start`, accepts
EOA Verifier's authenticated status, persists ownership selection, acknowledges durable
event storage and publishes or withdraws the selected association mapping as an endorsement on Ethereum. `server receive`
runs the same inbox without contract posting. Initial credential, wallet and
account verification belongs to EOA Verifier; the oracle does not repeat it.

Each event creates finite LMDB work that ends in completion or a dead letter.
Completed work never triggers periodic contract reads or repair of independent
on-chain changes. `GET /v1/endorsements/{associationSaid}` exposes local delivery
progress without querying Ethereum. See the [operator guide](docs/status-inbox.md)
for bounded retries and manual recovery.

Verifier acceptance persists until verified association revocation. Oracle selection
can supersede an older same-LE issuance without changing that upstream acceptance. Revocation withdraws only the matching
association SAID. Storage ACKs do not mean an on-chain transaction has succeeded. The
[two-service qualification](docs/two-service-qualification.md) exercises the installed
applications with witnessed KEL/TEL evidence, signed HTTP and Anvil.

KERI delivery uses JSON messages with CESR attachments. CESR-native is reserved
for CESR serialization of message structures. Use fresh PoC application stores
for this revised v1 status/ACK contract; no migrations or old-payload readers are
provided. Preserve existing user stores separately.

## Operator responsibility for stored state

Operators own compatibility between stored state and the configured Verifier,
chain, registry contracts and Ethereum writer. Startup logs previous/current
public settings when they change, continues, and records the new baseline.
An unchanged restart is quiet. No private key is included in these warnings.

A warning does not reset selection, replay completed work, rebuild confirmed
effects or replace signed transactions. Retained effects may describe the old
contracts, and signed transactions still target their original chain/contracts.
Inspect and resolve outstanding work before changing deployments; use separate
stores for an independent deployment and retain the old journal for recovery.
See the [operator guide](docs/status-inbox.md#configuration-changes-and-stored-state).

## Code organization

- runtime/ owns CLI, validated config, identity startup and HIO/store lifetime.
- receiving/ authenticates status ingress and signs ACKs for durable status-event storage.
- associations/ owns trusted status decoding, domain records and pure selection.
- endorsement/ owns desired actions, HIO RPC, effects and posting recovery.
- persistence/ owns LMDB lifecycle/table bindings, detached readers and explicit atomic writers.
- receiving/status_events.py owns status-event revision policy and wallet-selection preparation.
- endorsement/posting.py owns posting decisions; endorsement/doing.py only schedules them.
- endorsement/retrying.py and querying.py own offline recovery and local status queries.

The [application/persistence separation plan](docs/plans/application-persistence-separation.md)
records the staged boundary refactor and its validation requirements.

See the [testing guide](docs/testing.md) for module/class/function lookup and paired
qualification. Initializers contain no import facades; the root package retains
only its literal version metadata.

The [Oracle API](protocol/oracle-api-v1/README.md) defines status-event storage, selection and
publication. The [selection ADR](adrs/0001-wallet-association-selection.md) explains
the rationale. Credential evidence is delivered to EOA Verifier.

## Install

Use a fresh Python 3.12.6+ environment: GLEIF packages own the `keri` and `hio`
import paths and must not share an environment with upstream distributions.

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python '.[dev]'
```

For standalone installation, build and install a wheel in a fresh environment.
See the [operator guide](docs/status-inbox.md) for explicit trust, chain, contract
and writer settings. The Oracle wheel does not depend on EOA Verifier.

## Local integration environment

With Python 3.12.6+, Foundry 1.5.1 and jq on PATH:

```sh
python -m scripts.integration.env prepare --run-dir /tmp/oracle-demo
python -m scripts.integration.env start --run-dir /tmp/oracle-demo
python -m scripts.integration.env flow --run-dir /tmp/oracle-demo
python -m scripts.integration.env stop --run-dir /tmp/oracle-demo
```

Oracle owns the full stack and the sole maintained USDC scenario. Preparation
selects exact dependencies, installs fresh application wheels and creates native
witnessed source credentials. The Verifier fixture CLI owns issuance; Comply's
deploy command owns constructors and structured artifacts. No external schema
package or overlapping upstream KERI/HIO distribution is installed.
See [environment commands](scripts/integration/README.md) for lifecycle,
candidate-source selection and retained artifacts, and
[qualification](docs/two-service-qualification.md) for the installed-wheel suite.

## Protocol ownership

[EOA Verifier](https://github.com/GLEIF-IT/eoa-verifier/tree/master/protocol) owns
external-association-v1, its packaged schema/proof vectors, and verifier-api-v1
including shared webhook authentication/ACKs. Oracle owns only
[oracle-api-v1](protocol/oracle-api-v1/README.md).
[Comply](https://github.com/GLEIF-IT/vlei-comply/blob/main/docs/oracle-registry.md)
owns registry methods, identity encoding, ABI and calldata fixtures.

Oracle uses the Verifier-owned fixture CLI for qualification issuance, not an
installed Oracle issuance API. Oracle has no wallet challenge, credential
finalization or association-nonce store. No transport nonce ledger is needed. Ethereum transaction nonces remain a separate concern.

## Request verification and optional signed responses

Oracle opts into verifying every incoming webhook and authorizes only
`--verifier-aid`, using locally accepted KERI key state from `--verifier-kel`.
It signs its storage response after commit. Verifier needs Oracle's accepted KEL
to verify that signature, but no destination-AID setting. Other webhook targets
can return unsigned success without any KERI identity or state exchange.

Duplicate status events are handled by durable event ID/body and revision rules,
not transport nonces. Keep Oracle's signing identity and update imported KEL after
rotation. HTTP success, durable Oracle storage, selection and confirmed Ethereum
publication remain distinct. Use HTTPS; the signature profile covers method/path
and body digest, not HTTP status, origin/query or per-attempt freshness.
