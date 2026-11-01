# Two-service qualification

`tests/qualification/test_two_services.py` runs the installed `eoa-verifier` and `vlei-oracle`
CLIs as separate processes with independent KERI identities and persistent
stores. It invokes Verifier-owned witnessed GEDA/QVI/LE fixture commands and deploys the pinned
Comply contracts on a test-owned Anvil chain. It never injects accepted results.

The test follows these boundaries:

1. Create a real EIP-712 wallet proof, issue its association through the LE, and
   deliver source-chain, KEL/TEL and addressed IPEX Grant evidence over HTTP.
2. Observe verifier acceptance, signed webhook receipt and actual contract mapping.
   Drop one successful ACK after the receiver commits; verify retry keeps identical
   event bytes and eventually obtains an authenticated ACK.
3. Issue an otherwise valid second-wallet credential reusing the first nonce;
   require terminal `NONCE_REUSED`, no webhook and no change to the mapping. Then
   issue a fresh-nonce association for the beneficiary wallet. With both wallet
   mappings confirmed, transfer USDC and check both token balances. The oracle
   uses a separately funded, explicitly authorized writer key.
4. Restart both applications from their own stores without another Grant. Keep the
   verifier offline while checking the oracle's mapping; reopen the verifier and
   verify its stored account observation is unchanged.
5. Present a higher-issuance same-LE replacement. Deliver the predecessor's verified
   revocation and confirm the successor remains mapped. Revoke the successor and
   require both its acknowledged retirement event and actual contract withdrawal.
   Submit another token transfer and confirm that both balances remain unchanged,
   even though the policy-failure transaction itself has a successful receipt.

A Grant alone is not a revocation proof: the test supplies verified revocation
TEL and witnessed KEL anchor. Authenticated storage ACK, application status and contract state
are asserted separately. Detailed ownership and delivery edge cases remain in the
focused policy/transport tests; this is not a second fault-matrix suite.

## Run the environment

```sh
python -m scripts.integration.env qualify --run-dir /tmp/fresh-oracle-run
```

Use Python 3.12.6+, Foundry 1.5.1 and jq. Exact tool/contract revisions are recorded
in `scripts/integration/dependencies.json`; CI reads that same manifest. Prepare
creates a fresh run-owned environment with installed application wheels. The
scenario exchanges public commands/artifacts, CESR HTTP, signed webhook bytes,
public status and real contract reads. No Verifier implementation imports or
acceptance injection are used.

For candidate work add `--verifier-source PATH --comply-source PATH` and optional
`--oracle-source PATH` on prepare/qualify. Git-inventoried source snapshots exclude
ignored state and Git internals. Local changes are retained in the original
checkout; source revision/content and wheel hashes identify the selected inputs.
Use a new run when changing inputs.

## Full installed-wheel suite

Build the Oracle wheel, install it with `[dev]` into a fresh environment, and copy
`tests`, `scripts`, `protocol`, `pytest.ini` and `README.md` outside the checkout.
Set `ORACLE_WHEEL` to that wheel and `GITHUB_SHA` to its exact tooling revision.
Optionally select explicit compatible `VERIFIER_TOOL_SOURCE` and `VLEI_COMPLY_DIR`
checkouts; otherwise preparation fetches the manifest pins. Run:

```sh
python -m pytest -q tests
```

The paired fixture creates/stops its own environment and retains logs/native
stores on failure. Contract-effect tests also require the explicit
`VLEI_COMPLY_DIR` with initialized submodules. Application code is installed,
never editable or imported through source-path injection. The base Oracle test
environment requires no Verifier package: the paired environment installs each
application wheel itself. Missing prerequisites fail.

A started operator run can execute the same scenario with `flow --run-dir RUN`.
`ORACLE_INTEGRATION_RUN` is the explicit test attachment used internally by that
command. The caller then owns stopping the run. Full flow is a fresh-scenario
operation; repeated flow does not reset acceptance, mappings or token history.

The focused producer completion-gap tests now belong to Verifier delivery tests;
Oracle keeps receiver ordering, selection and publication assertions. Both use
GLEIF KERI 1.2.19/HIO 0.6.22. Valid-stream qualification does not resolve the known
native receipt-couple shortage limitation. See the
[environment guide](../scripts/integration/README.md) for stop/start and clean.
