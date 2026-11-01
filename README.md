# vLEI Oracle

`vlei-oracle` is the GL1 off-chain verifier/oracle boundary for `vlei-comply`.
It implements GL1-specific verification directly: vLEI credential-chain checks,
GL1 Ethereum Wallet Attestation validation, native EIP-712 Ethereum signature
recovery, and on-chain posting of compact registry state.

The first e2e target is local Anvil:

```text
single-sig GEDA -> QVI -> LE -> OOR -> GL1 Ethereum Wallet Attestation
  -> vlei-oracle -> vlei-comply contracts -> wUSDC transfer
```

## Commands

```sh
vlei-oracle server start ...
vlei-oracle client post-signature ...
vlei-oracle challenge ...
vlei-oracle wallet-data ...
vlei-oracle parse-deploy-log ...
```

`server start` runs the HIO/Falcon verifier/oracle service. It owns the KERI
parser, Exchanger, IPEX Grant notification processor, wallet-signature
operation store, verifier, and Web3 contract poster.

```sh
vlei-oracle server start \
  --name oracle \
  --base "$KERI_BASE" \
  --alias oracle \
  --http-port 9723 \
  --trusted-geda-aid "$GEDA_AID" \
  --contracts runs/latest/contracts.json \
  --rpc-url http://127.0.0.1:8545 \
  --private-key "$ADMIN_PK"
```

The canonical presentation workflow is standard KERIpy IPEX plus one REST
signature call:

- `kli ipex grant` sends the wallet ACDC, recursive source credentials, KELs,
  TELs, registry events, and `/exn/ipex/grant` through the normal KERIpy
  artifact stream.
- `vlei-oracle server start` reacts to the `/exn/ipex/grant` notification and
  opens a durable pending operation.
- `POST /presentations/{walletCredentialSaid}/signature` with a CESR Matter
  containing the 65-byte Ethereum signature.

The signature helper signs EIP-712 typed data with `eth-account`, encodes the
65-byte Ethereum signature as the smallest aligned CESR bytes primitive
(`Bytes_L1` / `5B`), and sends the second request:

```sh
vlei-oracle client post-signature \
  --oracle-url http://127.0.0.1:9723 \
  --wallet-said "$WALLET_CREDENTIAL_SAID" \
  --typed-data runs/latest/originator/wallet-challenge.json \
  --private-key "$ORIGINATOR_PK"
```

The e2e orchestration lives in:

```sh
scripts/e2e/gl1-wallet-transfer-e2e.sh
```

It is intentionally a shell script so the workflow remains explicit at the
KLI, vLEI-server, Forge, Anvil, Cast, and contract boundaries.

For a human-operated, multi-terminal walkthrough that keeps setup separate from
using the deployed infrastructure, see
[vlei-comply GL1 vLEI CLI Execution Guide](../vlei-comply/docs/gl1-vlei-cli-execution-guide.md).

## Maintainer Map

- `serving.py` assembles the HIO/Falcon runtime: Habery, Regery, parser,
  direct-mode endpoint, mailbox/OOBI Doers, Grant handling, verification, and
  posting.
- `handling.py` owns the durable operation state machine from IPEX Grant notice
  through signature attachment, verification, posting, and terminal state.
- `wallet.py`, `clienting.py`, and `endpoints.py` define the EIP-712 challenge,
  Ethereum signing, CESR signature transport, REST decode, and status polling
  boundary.
- `keri_store.py` extracts recursive ACDC/TEL material from the local KERIpy
  store; `verifier.py` enforces the GL1 vLEI chain and wallet-binding policy.
- `posting.py` is the Web3 edge adapter. HIO Doers own timing and receipt
  polling; Web3 code only plans, submits, and reads contract state.

## Local Setup

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/python -m pip install -e . -e ../../../core/python/wot-upstream/keripy -e ../../../credentials/schema-tools/vLEI
```

The e2e expects usable demo witness ports (`5642`, `5643`, `5644`) and a vLEI
schema server on `7723`. If another process is bound to those ports, it must be
the matching `kli witness demo` / vLEI-server stack. A stale container that only
answers the built-in witness controller OOBIs is not enough because newly
incepted AIDs must be published through witness OOBIs for mailbox resolution.

```sh
./scripts/e2e/gl1-wallet-transfer-e2e.sh
```

The script writes all generated deployment logs, KERI artifacts, wallet
challenge JSON, presentations, and verification outputs under `runs/`.

## Wallet Attestation

`gl1-ethereum-wallet-attestation.json` uses `NI2I` on both wallet credential
edges:

- `le -> Legal Entity vLEI credential`
- `oor -> Official Organizational Role credential`

The wallet attestation is issued by the OOR holder as self-attested wallet
binding evidence. The verifier rejects wallet credentials whose wallet edges
omit `NI2I` or use the default `I2I` operator.

The current schema keeps only the wallet-to-Legal-Entity binding attributes and
`NI2I` edges. Deployment binding lives in the EIP-712 domain, and the schema
does not embed `walletSignatureType` or `walletSignature` in the ACDC. The
Ethereum wallet signature is supplied only through the signature REST endpoint.

The oracle pins this GL1 schema internally at startup by loading the bundled
schema as a KERI `Schemer` and writing it into the service Habery schema cache.
That makes the accepted GL1 schema set explicit and removes any oracle runtime
dependency on resolving the wallet schema from `vLEI-server`. The local e2e
still runs `vLEI-server` for the KLI-controlled issuance side and upstream vLEI
schemas.

## Current Boundary

The verifier uses local KERIpy as the KERI/ACDC dependency baseline. Sally is
not imported directly; its chain-validation behavior is used as a reference for
the verifier's vLEI dependency checks. Ethereum wallet signatures are recovered
inside the Python verifier with `eth-account`; Web3.py is isolated behind the
HIO `ContractPosterDoer`, which owns receipt polling and confirmation timing.
`cast` remains in the e2e for Anvil account and contract checkpoint helpers.

The local shell e2e starts `vlei-oracle server start` before wallet Grants are
issued. The oracle receives the standard KERIpy IPEX Grant artifact stream,
opens pending operations from Grant notifications, waits for `post-signature`,
verifies the vLEI source chain and wallet binding, then posts verified evidence
on chain. The oracle does not run `kli ipex admit` for wallet presentations.
