# Persistence qualification

The five-PR persistence stack is based on merged package reorganization #26
(`485ed6950c268e765d8827d8e7277aae9b7627b4`). It uses the manifest-pinned
vlei-comply commit `6220cd78411b8b721838ee67f11293ff5504c05c`. PRs remain open
for human review; this is local qualification, not a deployment or release signoff.
Reproduce with the [operations runbook](association-operations.md).

On 2026-10-08, a fresh non-editable wheel installation passed **153 Python tests,
24 Solidity tests, and 3 startup checks**, with no skips. Its modules and JSON
resources matched current source exactly, with no retired modules or duplicate
schema resources. The wheel SHA256 was
`613a75fb2a5ac4746be5283e5f497658c73448a44e2f729a9dbefb59ada9f33b`.
Python 3.12.10, gleif_keri 1.2.15rc1, and gleif_hio 0.6.20rc3 were used.

The combined native KLI, two-chain, and independent-process exercise passed
against fresh stores and contracts. It demonstrated witnessed issuance and
presentation, actual posting and allowed/rejected transfers, live replacement,
late old revocation, active/source revocation, process interruption during
verification and before/after broadcast, and isolated chain/deployment scope.
Separate real Anvil and HTTP tests cover original/cancellation recovery and
reorgs, canonical confirmations, unknown nonce consumption, and RPC outages
at receipt, transaction, and nonce queries. Serialized-store tests verify that
removed evidence, copied bindings, counters/timestamps, receipt snapshots, and
workflow transaction summaries are absent. The issuer ledger contains only
issuer/nonce consumption; concurrency and process-restart replay checks pass.

In the combined clean run, replacement took 20.318s, active withdrawal and
rejected transfer 19.351s, and source withdrawal 17.526s, below the existing
30s assertions. Initial posting took 34.651s against its separate 60s watchdog.
Overlapping earlier qualification runs exceeded the replacement assertion;
these are single-machine local measurements, not load or production guarantees.
The assertions were preserved. No production deployment, independent security
review, or production-readiness claim follows from these results.
