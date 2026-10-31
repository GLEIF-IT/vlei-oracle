# Posting ABI provenance

Generated from `GLEIF-IT/vlei-comply` commit
`007f145867749780a4b6296d36fa2c8a665e0e44`:

- `VLEIOracleRegistry.json`: exact copy of `abi/VLEIOracleRegistry.json`;
  SHA-256 `03e17828c8ca84cbf4a71fb320585f50f39ee42b65a4c47738914e5ddf8d63cf`.
- `IdentityRegistry.json`: `forge inspect IdentityRegistry abi --json`;
  SHA-256 `004c3f6a2dc30a19a30d20b1aac069f29afd781440a5d2744a36f1b38156d988`.

Web3 uses these only for local encoding/decoding. HIO clients perform
all service RPC. The owned Solidity calldata vector is checked by both suites.
The identity registry remains a policy input hint; acceptance requires the
exact wallet/entity endorsement. There is no global wallet whitelist.
