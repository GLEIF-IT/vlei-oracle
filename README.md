# vLEI association oracle

The [trusted-status service](docs/status-inbox.md), `server start`, accepts
EOA Verifier's authenticated status, persists ownership selection, acknowledges durable
event storage and publishes or withdraws the selected association mapping as an endorsement on Ethereum. `server receive`
runs the same inbox without contract posting. Initial credential, wallet and
account verification belongs to EOA Verifier; the oracle does not repeat it.

Accepted associations remain valid until their association credential is revoked
or a newer same-LE issuance supersedes them. Revocation withdraws only the matching
association SAID. Storage ACKs do not mean an on-chain transaction has succeeded. The
[two-service qualification](docs/two-service-qualification.md) exercises the installed
applications with witnessed KEL/TEL evidence, signed HTTP and Anvil.

KERI delivery uses JSON messages with CESR attachments. CESR-native is reserved
for CESR serialization of message structures. Use fresh PoC application stores
for this revised v1 status/ACK contract; no migrations or old-payload readers are
provided. Preserve existing user stores separately.

## Code organization

- `receiving/service.py` composes trusted HTTP receipt and optional posting under HIO.
- `receiving/status.py` authenticates events and ACKs durable receipt.
- `selecting.py` chooses ownership using stored issuer/issuance facts.
- `endorsement/posting.py` serializes contract actions and records receipt outcomes;
  `effects.py` owns HIO RPC clients and ABI operations.
- `basing.py` owns persistence; `rpc.py` provides a caller-owned-client request helper.

- `association/` defines the wire profile and issuer ceremony helpers; it does
  not accept credentials or run account checks in the oracle service.

The [architecture cleanup plan](docs/architecture-cleanup-plan.md) sequences the
remaining cross-repository removals and operating fixes. This application has
one service model: authenticated verifier status followed by local selection and
optional contract posting. Credential evidence is delivered to EOA Verifier.

## Install

Use a fresh Python 3.12.6+ environment: GLEIF packages own the `keri` and `hio`
import paths and must not share an environment with upstream distributions.

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e '.[dev]' -r tests/requirements.txt -r scripts/protocol/requirements.txt
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
exports for the witnessed two-wallet test; keep this prepared environment rather than
reinstalling the guide's earlier qualification snapshot.

The schema-server tool `vlei` still declares upstream `keri`/`hio` distribution
names. The preparer installs it with `--no-deps` into the GLEIF demo environment;
`pip check` therefore reports those two metadata requirements as missing. Do not
install the overlapping upstream packages to silence them. This exception belongs
to demo schema tooling, not either application wheel.

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
  --nonce-head-dir "$ISSUER_STATE_ROOT" --nonce-name issuer-nonces \
  --out association.json
```

The ceremony validates the signature and canonical profile, then consumes the
nonce durably for that issuer before exposing credential bytes. Reuse the same
issuer-owned store selection across ceremonies. `--nonce-head-dir` is required;
`--nonce-name` defaults to `issuer-nonces` and `--nonce-base` defaults to empty.
The persistent layout is `<head>/vlei-oracle/nonces/<base>/<name>/`, containing
LMDB `data.mdb` and `lock.mdb`. Relative roots resolve at construction and remain
stable across reopen. If the selected root cannot open, the ceremony fails;
it never redirects consumption to a home-directory store. A duplicate nonce fails, including after
restart or concurrent finalization. Consumption is irreversible: a crash after
consumption may burn the nonce. Generate a fresh challenge if no finished artifact
survived. After an issuance interruption, inspect the existing KLI state and resume its
KERIpy escrows. Do not blindly repeat credential creation or finalize another
artifact with that nonce.

The ledger uses KERIpy `LMDBer` and a non-duplicate `Komer` table named
`consumed.`. Each typed claim stores only its exact issuer and nonce; its key is
a 64-character hexadecimal SHA-256 digest of the serialized pair. Ledger owners
use `dbing.openLMDB(cls=NonceLedger, name=..., base=..., headDirPath=..., temp=False)`;
ceremony functions borrow the open ledger. KERIpy owns creation and closure.
Persistent closure retains claims; standard temporary contexts clean up test
stores. Keep one open ledger per store in each process and share it with
borrowers in the synchronous issuer tooling. LMDB provides transaction locking
and synchronous commits. There is no application mutex, thread pool, retry loop
or additional serialization. The pinned binding rejects overlapping writes on
one environment with a native error; callers must not assume those calls queue.

This is a fresh storage/API baseline. There is no positional-path constructor,
`--nonce-dir` alias, migration or old-format reader. Preserve old operator stores
separately. Select the new store deliberately and use fresh ceremony nonces;
do not replay historical challenges into an empty store.

Issue the finished credential through the Legal Entity's existing registry:

```sh
.venv/bin/kli vc create --name "$LE_KEYSTORE" --base "$KERI_BASE" --alias "$LE_ALIAS" \
  --registry-name "$LE_REGISTRY_NAME" --credential @association.json
```

KLI requires the association and source schemas in the issuer's cache, an
accepted LE credential held by that AID, and its registry/witness configuration.
The paired qualification supplies those using KERIpy `Credentialer`/`Registrar` APIs.
The canonical schema lives under `protocol/`; the package contains an exact
checked copy for issuer ceremony use. The [paired qualification](docs/two-service-qualification.md)
exercises witnessed issuance and delivery to the verifier.

Present the finished ACDC and required supporting KEL/TEL evidence to EOA Verifier
with `kli ipex grant` or KERI CESR HTTP delivery. Addressed transport does not make the
association a targeted credential.
