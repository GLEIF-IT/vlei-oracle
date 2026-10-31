# Two-service qualification

`tests/test_two_services.py` runs the installed `eoa-verifier` and `vlei-oracle`
CLIs as separate processes with independent KERI identities and persistent
stores. It reuses the witnessed GEDA/QVI/LE issuer helpers and deploys the pinned
comply contracts on a test-owned Anvil chain. It never injects accepted results.

The test follows these boundaries:

1. Create a real EIP-712 wallet proof, issue its association through the LE, and
   deliver source-chain, KEL/TEL and addressed IPEX Grant evidence over HTTP.
2. Observe verifier acceptance, signed webhook receipt and actual contract mapping.
   Drop one successful ACK after the receiver commits; verify retry keeps identical
   event bytes and eventually obtains an authenticated ACK.
3. Issue an otherwise valid second-wallet credential reusing the first issuer/nonce;
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

## Run

Install the oracle wheel and `tests/requirements.txt` in a fresh Python 3.12
environment. Put Foundry 1.5.1 on PATH and set `VLEI_COMPLY_DIR` to comply revision
`13c6ae3506f6c6f398b37f12d646da4f2c21577b` with submodules and built contracts.
From a directory containing the copied `tests`, `scripts` and `protocol` trees:

```sh
PYTHONPATH=scripts/protocol python -m pytest -q tests/test_two_services.py
```

CI runs this with the full installed-wheel suite. Missing prerequisites fail.
Test logs and stores live beneath pytest's temporary directory; the fixture stops
only its own subprocesses. On macOS hosts with LMDB vnode exhaustion, copy the
inputs into a Linux container with no host bind mounts and run there.

The producer is pinned to verifier companion revision
`ba1971f54fd26191eb147fec0e7a75ed179eda7f` in `tests/requirements.txt`, not as an
oracle runtime import. Merge the verifier acceptance-policy companion before
this oracle schema/vector companion PR. Its source matches the qualified verifier wheel.
Existing status-posting tests establish old-observation
acceptance and failure handling without the verifier. The paired comply launcher invokes this same scenario; it does not copy the
KERIpy issuance or process-ownership code.

Both applications use GLEIF KERI 1.2.19 and HIO 0.6.22. This qualification uses
the coordinated nonce-policy candidate wheels; the closed ingress and
replay-pruning candidates remain excluded. The known KERIpy receipt-couple shortage limitation remains unresolved;
a passing valid-stream workflow does not establish liveness for that malformed input.

## Qualify local application changes

To qualify unmerged verifier/oracle changes, build wheels from clean temporary
copies of both working trees and install both wheels into the same fresh Python
3.12 environment with pytest and `scripts/protocol/requirements.txt`. Omit
`tests/requirements.txt` for this run: its Git requirement deliberately installs
the merged verifier and would replace the candidate being qualified.

Copy the oracle's `tests`, `scripts` and `protocol` trees outside the checkouts,
then use the same pinned comply revision and Foundry version documented above:

```sh
PYTHONPATH=scripts/protocol VLEI_COMPLY_DIR=/path/to/pinned/comply \
  /path/to/qualification/venv/bin/python -m pytest -q \
  tests/test_two_services.py tests/test_status_inbox.py
```

The paired test verifies the installed CLIs and wire/resource compatibility.
`test_status_inbox.py` additionally exercises Python imports from the installed
producer through its `delivery`/`persistence` components. These imports require
the reorganized producer revision pinned in `tests/requirements.txt`.

The current schema restricts association nonces to 1–128 ASCII characters from
`[A-Za-z0-9_-]`; the verifier uses direct issuer/nonce Komer keys. The reference
ACDC, schema SAID and endorsement fixture have been regenerated together.
