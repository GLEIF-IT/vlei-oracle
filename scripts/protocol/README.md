# Legacy combined-service issuance exercise

The current independent-service qualification is documented in
[the two-service guide](../../docs/two-service-qualification.md). These helpers
remain available for ceremony tooling and the legacy combined-service tests.

- [exercise_issuance.py](exercise_issuance.py): Real delegated credential issuance and IPEX/HTTP verification, including rejection cases.
- [ceremony.py](ceremony.py): Verify the pre-issuance proof, consume the issuer nonce durably, and write a full ACDC for KLI.
- [issuance_helpers.py](issuance_helpers.py): Test actor/store setup and KERIpy evidence workflows.
- [generate_vectors.py](generate_vectors.py): Known-answer vectors derived from an existing association ACDC.
- [requirements.txt](requirements.txt): Reproducible Python dependency versions.

## Run the exercise

Requires Python 3.12.6+, `uv`, Git, and Foundry's `anvil` on `PATH`. From the
oracle checkout:

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e '.[dev]' -r scripts/protocol/requirements.txt
.venv/bin/python scripts/dev/prepare-demo.py --schemas-only
.venv/bin/python scripts/protocol/exercise_issuance.py --schema-dir .demo/checkouts/vLEI/schema/acdc
```

The preparer checks out the pinned public vLEI schemas. Python uses PyPI's
`gleif_keri==1.2.19`. The exercise starts its own Anvil and HIO/Falcon server on local
ephemeral ports, starts one KERI witness, and closes its temporary KERI
stores and processes on exit.

Use a fresh virtual environment when switching from upstream `keri`/`hio`:
the GLEIF packages own those same import paths. The pinned release requires
`gleif_hio==0.6.22` and its declared LMDB constraints.

The test GEDA delegates the QVI identifier and issues its QVI credential. The
QVI issues the chained LE credential. The LE signs and issues an untargeted
association containing the wallet's EIP-712 proof. All identifiers use one key
and one local witness. Actors discover the witness through KERIpy OOBI resolution
and collect actual receipts through the KERIpy Registrar. The oracle independently
processes their signed, receipted KELs, TELs,
credentials, and addressed IPEX Grant over HTTP.

Fresh receivers test missing issuance TEL and KEL anchors without accepting the
association. Removing receipts from an accepted association's real KEL store
keeps verification pending until the receipts return. Assertions also cover
pending source evidence, successful verification, wrong signer,
wrong chain, ordinary contract code, and EIP-7702 delegation. Revoking the LE
prevents acceptance of a new child and makes fresh verification of an already
accepted association fail. The oracle rejects a candidate whose accepted source
has known revocation, even when KERIpy keeps that candidate unaccepted. Missing
source evidence remains pending; absence never establishes invalidity.

## Keep stores for inspection

Add `--keep` to retain GEDA, QVI, LE, and oracle state after the run:

```sh
.venv/bin/python scripts/protocol/exercise_issuance.py \
  --schema-dir .demo/checkouts/vLEI/schema/acdc --keep
```

Each run gets a fresh `issuance-…` KLI base. The issuer nonce ledger is retained
alongside the LE credential store in its own LMDB directory. One open ledger is
owned by the issuer workflow and borrowed by ceremony/replacement calls. Each
claim is an atomic insert-if-absent for the exact issuer and nonce. Consumption
commits before credential bytes are written; storage errors prevent publication.
A committed nonce remains consumed even if subsequent artifact writing fails.
The script prints actual keystore,
event, credential, and oracle-operation paths, plus ready-to-run `kli status`
and `kli vc list --verbose` commands. Add `--issued` to a credential-list command
to inspect credentials issued by that actor. Stores use KERIpy's normal persistent
locations and remain available even if an assertion fails; this option does not
resume an earlier exercise. These test keystores have no passcode.

Anvil and the oracle HTTP service still stop on exit. After a successful run,
the LE credential is revoked by the final test; the retained oracle result is
labelled `initialVerification`; `finalOperations` records the later terminal states.

## Include deployed contracts and transfers

Prepare the pinned checkouts and a fresh environment, including submodules:

```sh
python3.12 scripts/dev/prepare-demo.py --directory .demo-association
source .demo-association/demo.env
```

Use `.demo-association/venv/bin/python` in the commands below. The schemas are
under `.demo-association/checkouts/vLEI/schema/acdc`. Requires authenticated
GitHub CLI access to the GLEIF repositories. The preparer refuses to reset dirty
or mismatched checkouts: choose a fresh directory after a pin changes.

Run with the selected contract checkout and Foundry:

```sh
.demo-association/venv/bin/python scripts/protocol/exercise_issuance.py \
  --schema-dir .demo-association/checkouts/vLEI/schema/acdc \
  --comply-dir .demo-association/checkouts/vlei-comply
```

The same issuance implementation now continues through fresh `DeployAll`
contracts, a dedicated authorized oracle writer, durable signed intents,
endorsements for two wallets, a real allowed wrapper transfer, source revocation,
exact withdrawal, and a real rejected transfer. A separate association revocation
withdraws the beneficiary while its LE source and the originator remain valid. Token balances establish the
outcomes; a successful receipt alone does not establish policy acceptance.
No global wallet admission is used. A real signed EIP-7702 authorization
transaction installs a delegation to deployed test code, and the oracle records
that exact designation before endorsement. This qualifies account eligibility;
the test token code is not a production wallet implementation.
Fixture token/admin calls run in test-side
helpers; all service network waits remain HIO requests.

Add `--process-check` to crash/restart actual oracle CLI processes while source
evidence is missing and while an RPC check is interrupted. With `--comply-dir`,
also crash immediately before broadcast and after broadcast with mining paused.
Recovery reconciles the saved hashes and nonce counts, then observes source
revocation and withdraws. Separate dedicated keys avoid sharing nonce streams.
The same KERIpy stores and AID recover verification without another Grant. Each run prints its
fresh persistent process-test namespace before starting; those test stores are
retained for inspection, including when an assertion fails.

After initial endorsement, a newly issued beneficiary association replaces its
live endorsement. Verified revocation of the superseded association leaves the new
endorsement intact. The fixture separately records live supersession latency.

The contract tests also exercise saved-hash recovery, explicit same-nonce
cancellation, reorg reconciliation with the same signed identity, and wrong-chain
refusal. Add `--two-chain-check` to deploy on independent chains 31337/31338:
the first chain's signed association is rejected on the second, its properly
scoped association is posted there, and both independently withdraw on observed
source revocation. `anvil_setCode` cases remain code-classification simulations,
separate from the actual authorization transaction.

## KLI alignment

Registry inception, issuance, and revocation use KERIpy's `Registrar` and
`Credentialer`, with their completion signals and escrow processing scheduled
under one HIO `Doist`. Each HTTP presentation runs in its own `DoDoer` alongside
the oracle. Anvil control requests use a HIO HTTP client as well.

Add `--kli-check` to issue another finished, pre-signed untargeted ACDC through
the actual `kli vc create --credential` command. The exercise exclusively hands
the LE's stores to KLI while the witness remains live, then reopens them and
checks the exact stored credential and independent HTTP verification. This
option retains a fresh KLI base and prints inspection commands. A separate
receiver preserves the original missing-source test topology.

The oracle's verification coordinator owns an EOA worker with request and result
queues. Local KERI and signature checks remain ordinary functions; RPC waits
use HIO HTTP clients. Account code is read at an identified canonical block using
an EIP-1898 block-hash selector. The operation retains chain ID, block number/hash,
code, and observation time off-chain. Missing blocks, unsupported selectors, or
RPC errors remain pending; they do not establish account invalidity. The
coordinator rechecks credential status before recording
success. RPC failures remain pending for retry. Persisted operations supply the
work list after restart, so interrupted requests and lost cues can be recomputed
without persisting a second queue or copying credential evidence.

The exercise follows `kli vc registry incept`, `kli vc create`, `kli vc revoke`,
and `kli ipex grant`; Grants include the ACDC, issuance event, and KEL anchor.
Association fields go directly into `SerderACDC`, followed by the KLI
full-credential validation/issuance path. The convenience private-credential
constructor adds a subject nonce, which the association schema does not use.

Two deliberate test choices replace CLI transport plumbing: GEDA's local
approval records the same authorizer seal as `kli delegate confirm --interact`,
and issuer/holder credential evidence is replayed directly rather than delivered
through mailboxes. Witness discovery and receipt collection use KERIpy network
doers. Oracle traffic crosses its real CESR HTTP endpoint.
Separate source delivery tests a Grant arriving before its supporting chain.

## Service interface

The service accepts the bundled association schema by default. Start it with:

```sh
--schema-dir .demo/checkouts/vLEI/schema/acdc \
--trusted-geda-aid '<GEDA AID>' --rpc-url http://127.0.0.1:8545 --no-post
```

Send the finished credential and supporting KERI evidence through the CESR
HTTP endpoint and IPEX Grant. Poll `GET /associations/<association SAID>`;
`verificationState: "verified"` records completed initial checks; `managed`
records enrollment for the legacy posting worker. The embedded proof needs no
signature POST. Posting flags and lifecycle are described in the root README.

The obsolete wallet-transfer shell workflow has been removed. This exercise
also covers contract posting and transfers when `--comply-dir` is supplied.
It needs no global wallet whitelist.

The contract extension uses a 60-second initial-publication watchdog and asserts
a 30-second local bound for live replacement, association withdrawal, and source
withdrawal after evidence delivery
(including the selected process/chain checks). It uses one local witness,
auto-mining Anvil, one confirmation and a 50 ms in-process posting interval.
These measured test conditions are not a production service guarantee. RPC or
evidence outages and an unknown consumed writer nonce have no finite bound.
