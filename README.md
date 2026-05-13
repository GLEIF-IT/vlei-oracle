# vLEI Oracle

`vlei-oracle` is the GL1 off-chain verifier/oracle boundary for `vlei-comply`.
It implements GL1-specific verification directly: vLEI credential-chain checks,
GL1 Ethereum Wallet Attestation validation, native EIP-712 Ethereum signature
recovery, and on-chain posting of compact registry state.

The first e2e target is local Anvil:

```text
single-sig GEDA -> QVI -> LE -> OOR -> GL1 Ethereum Wallet Attestation
  -> vlei-oracle -> vlei-comply contracts -> wvLEI transfer
```

## Commands

```sh
vlei-oracle challenge ...
vlei-oracle wallet-data ...
vlei-oracle presentation-from-keri ...
vlei-oracle verify-presentation ...
vlei-oracle post-verified ...
vlei-oracle parse-deploy-log ...
```

The e2e orchestration lives in:

```sh
scripts/e2e/gl1-wallet-transfer-e2e.sh
```

It is intentionally a shell script so the workflow remains explicit at the
KLI, vLEI-server, Forge, Anvil, Cast, and contract boundaries.

## Local Setup

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/python -m pip install -e . -e ../../../core/python/keripy -e ../../../credentials/schema-tools/vLEI
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

## Wallet Attestation Edges

`gl1-ethereum-wallet-attestation.json` uses `NI2I` on both wallet credential
edges:

- `le -> Legal Entity vLEI credential`
- `oor -> Official Organizational Role credential`

The wallet attestation is issued by the OOR holder as self-attested wallet
binding evidence. The verifier rejects wallet credentials whose wallet edges
omit `NI2I` or use the default `I2I` operator.

## Current Boundary

The verifier uses local KERIpy as the KERI/ACDC dependency baseline. Sally is
not imported directly; its chain-validation behavior is used as a reference for
the verifier's vLEI dependency checks. Ethereum wallet signatures are recovered
inside the Python verifier with `eth-account`; `cast` is used by the e2e only
for wallet signing and Anvil contract interaction.
