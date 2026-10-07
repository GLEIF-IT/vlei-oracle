# Association issuance and verification

- [exercise_issuance.py](exercise_issuance.py): Real delegated credential issuance and IPEX/HTTP verification, including rejection cases.
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
`gleif_keri==1.2.15rc1`. The exercise starts its own Anvil and HIO/Falcon server on local
ephemeral ports and closes its temporary KERI stores and processes on exit.

Use a fresh virtual environment when switching from upstream `keri`/`hio`:
the GLEIF packages own those same import paths. The RC requires
`gleif_hio==0.6.20rc3` and `lmdb<3`.

The test GEDA delegates the QVI identifier and issues its QVI credential. The
QVI issues the chained LE credential. The LE signs and issues an untargeted
association containing the wallet's EIP-712 proof. All identifiers use one key
and no witnesses. The oracle independently processes their signed KELs, TELs,
credentials, and addressed IPEX Grant over HTTP.

Assertions cover pending evidence, successful verification, wrong signer,
wrong chain, ordinary contract code, and EIP-7702 delegation. Revoking the LE
prevents acceptance of a new child and makes fresh verification of an already
accepted association fail. KERIpy leaves the unaccepted child pending; the
exercise does not mistake that state for successful verification.

## Keep stores for inspection

Add `--keep` to retain GEDA, QVI, LE, and oracle state after the run:

```sh
.venv/bin/python scripts/protocol/exercise_issuance.py \
  --schema-dir .demo/checkouts/vLEI/schema/acdc --keep
```

Each run gets a fresh `issuance-…` KLI base. The script prints actual keystore,
event, credential, and oracle-operation paths, plus ready-to-run `kli status`
and `kli vc list --verbose` commands. Add `--issued` to a credential-list command
to inspect credentials issued by that actor. Stores use KERIpy's normal persistent
locations and remain available even if an assertion fails; this option does not
resume an earlier exercise. These test keystores have no passcode.

Anvil and the oracle HTTP service still stop on exit. After a successful run,
the LE credential is revoked by the final test; the retained oracle result is
a snapshot of its earlier successful verification.

## KLI alignment

Registry inception, issuance, and revocation use KERIpy's `Registrar` and
`Credentialer`, with their completion signals and escrow processing scheduled
under one HIO `Doist`. Each HTTP presentation runs in its own `DoDoer` alongside
the oracle. Anvil control requests use a HIO HTTP client as well.

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
and issuer/holder evidence is replayed directly rather than discovered through
witnesses or mailboxes. Oracle traffic still crosses its real CESR HTTP endpoint.
Separate source delivery tests a Grant arriving before its supporting chain.

## Service interface

Association mode adds these arguments to `vlei-oracle server start`:

```sh
--association-schema protocol/external-association-v1/association.schema.json \
--schema-dir .demo/checkouts/vLEI/schema/acdc \
--trusted-geda-aid '<GEDA AID>' --rpc-url http://127.0.0.1:8545 --no-post
```

Send the finished credential and supporting KERI evidence through the CESR
HTTP endpoint and IPEX Grant. Poll `GET /presentations/<association SAID>`;
`verified` includes `wallet`, `identityRef`, and `associationSaid`. The embedded
proof needs no signature POST. This result records verification at that time;
it does not create or maintain an on-chain endorsement.

The existing wallet-transfer shell exercise and vlei-comply stack scripts still
exercise the legacy contract format. This smaller exercise covers the new
association path without requiring those contracts or witness services.
