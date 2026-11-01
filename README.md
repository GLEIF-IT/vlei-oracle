# vLEI association oracle

The [trusted-status service](docs/status-inbox.md), `server start`, accepts
EOA Verifier's authenticated status, persists ownership selection, acknowledges durable
event storage and publishes or withdraws the selected association mapping as an endorsement on Ethereum. `server receive`
runs the same inbox without contract posting. Initial credential, wallet and
account verification belongs to EOA Verifier; the oracle does not repeat it.

Verifier acceptance persists until verified association revocation. Oracle selection
can supersede an older same-LE issuance without changing that upstream acceptance. Revocation withdraws only the matching
association SAID. Storage ACKs do not mean an on-chain transaction has succeeded. The
[two-service qualification](docs/two-service-qualification.md) exercises the installed
applications with witnessed KEL/TEL evidence, signed HTTP and Anvil.

KERI delivery uses JSON messages with CESR attachments. CESR-native is reserved
for CESR serialization of message structures. Use fresh PoC application stores
for this revised v1 status/ACK contract; no migrations or old-payload readers are
provided. Preserve existing user stores separately.

## Code organization

- runtime/ owns CLI, validated config, identity startup and HIO/store lifetime.
- receiving/ authenticates status ingress and signs durable receipt ACKs.
- associations/ owns trusted status decoding, domain records and pure selection.
- endorsement/ owns desired actions, HIO RPC, effects and posting recovery.
- persistence/ owns LMDB tables, scopes and atomic receipt/selection/journal writes.

See the [testing guide](docs/testing.md) for module/class/function lookup and paired
qualification. Initializers contain no import facades; the root package retains
only its literal version metadata.

The [Oracle API](protocol/oracle-api-v1/README.md) defines receipt, selection and
publication. The [selection ADR](adrs/0001-wallet-association-selection.md) explains
the rationale. Credential evidence is delivered to EOA Verifier.

## Install

Use a fresh Python 3.12.6+ environment: GLEIF packages own the `keri` and `hio`
import paths and must not share an environment with upstream distributions.

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e '.[dev]' -r tests/requirements.txt
.venv/bin/python scripts/dev/prepare-demo.py --schemas-only
# For the full suite, first prepare Foundry/comply as described below.
.venv/bin/python -m pytest -q
```

For standalone installation, build a wheel with `python -m pip wheel --no-deps
--wheel-dir dist .` and install it into a fresh environment. The oracle wheel does
not depend on or import EOA Verifier. See the [operator guide](docs/status-inbox.md)
for explicit trust, chain, contract and writer settings.

For both applications and the local launcher, prepare one isolated demo directory:

```sh
python3.12 scripts/dev/prepare-demo.py --directory .demo-paired
source .demo-paired/demo.env
```

This installs the invoking oracle source, the merged verifier pinned in
`tests/requirements.txt`, and the KERIpy/HIO versions declared by the package.
The manifest pins KERI demo assets, vLEI schemas and the comply launcher/contracts.
It exports both executables and checkout paths; it starts no services and creates
no identities. Existing mismatched/dirty dependency checkouts are never reset;
choose a new directory when changing pins. Install Foundry 1.5.1 separately, then
follow the prepared comply checkout's `docs/gl1-vlei-local-infra-setup.md` for
service configuration. Run its `scripts/dev/init-local-vlei-fixture.sh` with these
exports for the witnessed two-wallet test; keep this prepared environment with the selected compatible revisions.

The schema-server tool `vlei` still declares upstream `keri`/`hio` distribution
names. The preparer installs it with `--no-deps` into the GLEIF demo environment;
`pip check` therefore reports those two metadata requirements as missing. Do not
install the overlapping upstream packages to silence them. This exception belongs
to demo schema tooling, not either application wheel.

## Protocol ownership

[EOA Verifier](https://github.com/GLEIF-IT/eoa-verifier/tree/master/protocol) owns
external-association-v1, its packaged schema/proof vectors, and verifier-api-v1
including shared webhook authentication/ACKs. Oracle owns only
[oracle-api-v1](protocol/oracle-api-v1/README.md).
[Comply](https://github.com/GLEIF-IT/vlei-comply/blob/main/docs/oracle-registry.md)
owns registry methods, identity encoding, ABI and calldata fixtures.

Issuance assembly in `tests/support/issuers.py` serves the maintained paired
qualification; it is not an installed Oracle issuance API. Oracle has no wallet
challenge, credential finalization or association-nonce store. Transport replay
claims and Ethereum transaction nonces remain separate concerns.
