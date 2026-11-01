# Oracle-owned local USDC environment

Use Python 3.12.6+, Foundry 1.5.1, jq and authenticated GitHub access. Commands run
from this tooling repository root. No neighboring checkout paths are inferred.

```sh
python -m scripts.integration.env prepare --run-dir /tmp/oracle-demo
python -m scripts.integration.env start --run-dir /tmp/oracle-demo
python -m scripts.integration.env status --run-dir /tmp/oracle-demo
python -m scripts.integration.env flow --run-dir /tmp/oracle-demo
python -m scripts.integration.env logs --run-dir /tmp/oracle-demo
python -m scripts.integration.env stop --run-dir /tmp/oracle-demo
python -m scripts.integration.env start --run-dir /tmp/oracle-demo
python -m scripts.integration.env status --run-dir /tmp/oracle-demo
python -m scripts.integration.env stop --run-dir /tmp/oracle-demo
python -m scripts.integration.env clean --run-dir /tmp/oracle-demo
```

`qualify --run-dir FRESH` performs prepare/start/flow/stop. `flow` requires fresh
scenario state; repeated flows never silently reset chain, credentials or stores.
Use a new run for another flow. Stop preserves persistent state. Start verifies
existing deployed code and writer authorization and reopens services without
new Grants or deployment. Clean refuses live owned services in either owner and
removes only this explicit stopped run. Keep run roots outside source checkouts. One controller operates each run at a
time; concurrent lifecycle commands are unsupported.

Preparation fetches the exact Verifier/Comply revisions in `dependencies.json`,
initializes recorded Solidity submodules and builds fresh application wheels.
Verifier preparation owns real witnessed GEDA/QVI/LE source issuance and exact
installed schema hosting; Oracle starts Anvil, invokes Comply's local deploy
command, funds/authorizes a distinct posting writer, then starts the installed
Oracle and Verifier CLIs with explicit peer KELs and addresses. It also starts an
isolated HTTP proxy that drops only the first successful post-commit ACK. This
fault fixture journals exact forwarded bytes; it supplies no acceptance results.

Six native witness identities/listeners are available by default in one HIO
process. Issuance retains the established first-witness profile. Automated tests
use `--witness-count 1`; six listeners are not a six-witness credential guarantee.

For candidates add `--verifier-source PATH --comply-source PATH` and optionally
`--oracle-source PATH` during preparation. Original directories are never reset.
Comply runs from an isolated copy so Forge cannot dirty the selected source.
Snapshots use Git's tracked/unignored inventory, omit ignored state and Git
internals, and refuse symlinks. For an already built Oracle wheel use
`--oracle-wheel FILE --tool-revision REVISION` with its matching tooling root.
Changes in selected sources/pins require a new run.

The root manifest records public identities, endpoints, deployment addresses and
source/wheel hashes. `verifier/` contains the Verifier-owned sub-environment;
`services/` contains Oracle's native stores; `checkouts/`, `tools/`, `wheels/`,
`contracts.json`, `contracts.log`, `anvil.json` and `logs/` retain build/deployment
and runtime evidence. Public local Anvil fixture keys are code constants; posting
keys travel only through the Oracle child's environment, never public JSON or
process arguments. Never use live operator state as a run root.

CLI parsing, source/artifact handling, process ownership, dependency checkout,
contract deployment and the lost-ACK fixture are separate small modules. The
sole USDC assertion sequence remains `tests/qualification/test_two_services.py`.
It verifies real balance changes and exact-SAID withdrawal, beyond receipt ACKs.
Oracle never imports the Verifier fixture implementation; commands and explicit
artifacts are the shared boundary. None of this tooling ships in the runtime wheel.

## Configuration ownership

The installed service warns and continues when its recorded startup settings
change. Operators own compatibility with retained state; see the
[configuration guidance](../../docs/status-inbox.md#configuration-changes-and-stored-state). Fixture manifests still select reproducible
inputs: prepare a fresh run when changing its deployment or source inputs, and
keep existing run directories for restart and diagnosis.
