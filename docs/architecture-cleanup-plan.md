# Commit to the EOA verifier association model

Status: active, 2026-10-31. Baseline: oracle PR #46 merged as `f6b66af`.
This plan owns the architecture cleanup across
[EOA Verifier](https://github.com/GLEIF-IT/eoa-verifier),
[vLEI Oracle](https://github.com/GLEIF-IT/vlei-oracle), and
[vlei-comply](https://github.com/GLEIF-IT/vlei-comply).
Implementation source and tests remain authoritative. Each row below is a
separate, reviewable PR; complete removal precedes new mechanisms.

## Binding decisions

1. EOA Verifier alone accepts native KERI credential evidence, validates the
   wallet proof and initial account state, and observes association revocation.
   Acceptance continues until that exact association is revoked or superseded;
   neither the oracle nor the contracts re-run source-credential or account checks.
2. Oracle authenticates and durably receives verifier status, selects the current
   association for the exact chain/wallet, and posts or withdraws that selection.
   A stored webhook ACK is not an Ethereum transaction receipt.
3. Contracts evaluate the current wallet's identity and exact association
   endorsement. The supported wrapper workflow is a direct, wallet-associated,
   owner-authorized transfer with auditable compliance records.
4. wUSDC is a noncustodial route for adding auditable compliance records. Direct
   USDC transfers may bypass it. Asset-wide USDC enforcement is intentionally
   outside scope and is not a cleanup or readiness blocker.
5. No backward compatibility is required. Delete obsolete commands, APIs,
   storage records, adapters, tests, fixtures and runnable instructions together.
   Do not leave aliases, deprecated shims, migration readers or fallback imports.
   Git history preserves the retired implementation. Do not delete operator data.
6. Keep HIO scheduling, LMDB durability, native KERI evidence stores, explicit
   trust/deployment scopes, and locally encoded contract ABIs with HIO RPC waits.
   Do not add another service concurrency model or persistence abstraction.

## Ordered PRs and acceptance gates

### 1. Remove the combined verifier/oracle runtime (merged: PR #47)

Owner: vlei-oracle. Start from merged PR #46.

- Delete `server legacy-start` and its handler/configuration, `serving.py`,
  credential ingestion/Grant endpoints, parser/mailbox composition, credential
  verification coordinator and EOA account worker, and legacy reconciliation.
- Delete verification/managed-association LMDB records and their APIs. Remove
  cancellation/reorg machinery only used by that runtime. Preserve the active
  trusted-status inbox, ownership selection, signed transaction journal,
  immutable-attempt checks and trust/chain/deployment guards.
- Delete the combined-runtime issuance exercise, restart/replacement fixtures,
  legacy-only tests and runbooks. Remove deployment/demo instructions that still
  offer the retired path. Do not retain a second authority to satisfy a fixture.
- Rehome the live association profile (canonical identifiers, wallet challenge,
  issuer-side proof checks and schema resource) into the `association/` wire-profile
  package with issuer-side helpers. Retain issuer ceremony, nonce ledger and
  protocol vectors;
  these prepare association evidence and do not compose an oracle verifier.
- Inspect remaining compatibility surfaces in the three repositories. Delete
  aliases/fallbacks when encountered in this dependency closure; record separately
  owned removals below rather than quietly preserving them.

Acceptance: retired command fails argument parsing; retired runtime modules are
absent from a fresh wheel; current challenge/ceremony/profile behavior passes;
full installed-wheel oracle suite passes; real two-process verifier/oracle
qualification still proves authenticated receipt, posting, replacement,
revocation, restart/redelivery and accepted/rejected contract transfers. No test
may regain credential verification inside the oracle.

### 2. Remove wrapper workflows incompatible with wallet association v1 (merged: comply #11, oracle #48)

Owner: vlei-comply. [Removal PR #11](https://github.com/GLEIF-IT/vlei-comply/pull/11).
Follow PR 1 immediately, before adding recovery features.

- Remove `transferWithBeneficiary` and `transferFromWithBeneficiary` mediation
  workflows that construct parties without an associated wallet. Remove their
  interfaces, events, helper branches, scripts and tests.
- Remove `PendingTransaction`, pending nonce/storage, queue events, pending
  transfer branches and `resolvePendingTransaction`. Current policy has no
  supported producer of Pending decisions; administrative settlement bypasses
  current policy evaluation. Remove unused decision values/types as their full
  caller graph permits; no empty placeholders for hypothetical future workflows.
- Keep direct wallet-based transfer and allowance/ownership semantics. Evaluate
  the current exact wallet association on every supported route and preserve
  audit records for allowed transfers and meaningful rejection behavior.
- Trace generic KYC/jurisdiction/VASP enrichment and the empty-config
  `CredentialRegistryIdentityValidator` through deploy scripts and actual policy
  consumption. Delete unused v1 scaffolding. Keep a Chainlink/GL1 seam only where
  a current integration consumes it; document the concrete consumer.
- Delete generated-wrapper compatibility APIs/types in `IPolicyManager` and
  backward-compatibility enum/tuple fields in `IPolicy` as their caller graph
  permits. The existing contract launcher already delegates to the paired test;
  preserve that one maintained qualification route. Update packaged ABI
  consumers and oracle contract pin in the same coordinated qualification wave.

Acceptance: Forge unit/integration tests and ABI/caller searches establish that
no removed route remains; exact-wallet valid, revoked, superseded, unassociated
and cross-entity cases behave correctly; fresh deployment and the installed-wheel
paired suite pass against the new contract revision. No compatibility selectors,
old queue settlement or abstract-beneficiary fallback remains.

### 3. Complete PR #49 without application locking (qualified; PR review pending)

Owner: vlei-oracle. Added by Kent on 2026-10-31. This is a separate focused
refactor after the wrapper-removal qualification. Preserve
[vlei-comply PR #11](https://github.com/GLEIF-IT/vlei-comply/pull/11) and
[oracle companion PR #48](https://github.com/GLEIF-IT/vlei-oracle/pull/48).
Both wrapper PRs are merged. Nonce implementation is isolated on
`refactor/nonce-ledger-lmdber`; publication is approved and PR review is pending.

**Publication approved:** Kent authorized committing and pushing the PR on
2026-10-31 after local qualification. Merge remains subject to PR review.

Required source inspection, before implementation:

- Inspect the actual installed, pinned GLEIF KERIpy/HIO implementations of
  `dbing.LMDBer`, `dbing.openLMDB`, `koming.Komer` and `Filer`, plus this
  application's `OracleBaser`. The currently qualified distributions are
  `gleif_keri==1.2.19` and `gleif_hio==0.6.22`. Verify those pins when starting;
  do not design against another local KERIpy checkout or revision.
- Trace environment opening, subdatabase serialization/handles, synchronous
  commit defaults, deferred opening, normal/temporary closure and Filer's
  alternate-directory behavior. Explicit selection of a nonce store must never
  silently redirect consumption into a different store.

Required implementation:

- Keep the NonceLedger domain API (`claim(issuer=..., nonce=...)`), but subclass
  `dbing.LMDBer`. Use standard `name`, `base`, `headDirPath`, `temp` and `reopen`
  construction with ledger-specific directory constants.
- Remove direct `lmdb.open`, manual directory management and custom context-manager
  lifecycle methods. Owning call sites use `dbing.openLMDB(cls=NonceLedger, ...)`;
  ceremony functions borrow the already-open ledger. Persistent closure must
  never delete consumed nonces. Standard temporary closure must clean its store.
- Define a minimal typed `NonceClaim` record containing only `issuer` and `nonce`.
  Bind a non-duplicate `Komer` subdatabase in `reopen()` after the base environment
  opens. Close acquired resources if later initialization fails.
- Preserve the exact issuer and nonce strings, including Unicode and whitespace.
  Retain an unambiguous encoding of the pair and use its SHA-256 digest as a
  fixed-length 64-character hexadecimal Komer key. No normalization, expiry or
  release of consumed nonces is introduced.
- Implement `claim()` with one explicit LMDB write transaction. Insert without
  overwrite; if occupied, deserialize and inspect the existing claim inside that
  same transaction. Return False only for the identical pair; fail closed on
  inconsistent or malformed stored data. Follow OracleBaser's use of Komer
  serialization and subdatabase handles for atomic operations.
- Kent's correction supersedes the original thread-serialization requirement:
  remove the Python mutex, thread pools, barriers and custom coordination from
  nonce handling and its tests. Use native LMDB transactions and process locking.
  The pinned binding may reject overlapping writes on one environment; surface
  that error without retries or a new serialization mechanism. Follow the
  synchronous, single-owner KERIpy/HIO calling model.
- Update CLI storage options, all callers, tests and documentation together.
  Replace positional-path construction and the current `--nonce-dir` selection
  with explicit standard storage options; document the resulting layout after
  inspecting the pinned Filer behavior. No positional-path compatibility
  constructor, deprecated flag aliases, migrations, old-format readers or
  fallback APIs are required.
- Consumption must commit before usable credential bytes are published. Preserve
  proof validation before consumption, existing-output handling and the rule that
  an output failure after commit leaves the nonce consumed.
- Introduce no generic repository framework or unrelated persistence cleanup.

Validation and completion:

1. Run focused checks for persistent reopen, persistent closure without deletion,
   standard temporary cleanup, deferred opening, stable selected paths and rejected
   alternate-directory fallback. Exercise failure during post-open initialization
   and verify acquired resources close.
2. Exercise issuer scoping, exact Unicode/whitespace nonces, native overlapping-
   writer rejection, independent-process contention and inconsistent stored claims. Preserve the
   ceremony's invalid-proof, existing-output and post-commit output-failure
   assertions; do not replace meaningful contention with serial calls.
3. Build and install a fresh oracle wheel outside the checkout. Run the full
   oracle suite and maintained two-process qualification against the appropriate
   qualified contracts and verifier. At this plan update those are contracts
   `4c83e465b3d7937f02b95e805cca5b79f5015078` and verifier
   `486fb42f92b6c3269d7968934448f21121ef08aa`; re-verify merge/pin state at execution.
   Do not restore deleted combined-runtime fixtures or weaken assertions.
4. Report the resulting on-disk storage layout, removed custom lifecycle code,
   exact test/artifact evidence and remaining limitations. Leave the refactor
   uncommitted and unpushed until Kent authorizes publication (now approved).

### 4. Remove association-nonce ownership from vlei-oracle (next PR wave)

Owners: vlei-oracle removal PR, with an EOA Verifier acceptance-policy companion.
This is planned work, not part of the PR #49 locking correction. Kent explicitly
rejected consolidating this issuer ledger into the oracle AID store. The oracle
receives verified status and posts selected associations; it must not own EOA
proof nonce consumption or credential issuance policy.

**Oracle removal:**

- Delete `NonceLedger`, `NonceClaim`, the separate nonce store and all
  `--nonce-*` options and call sites. Do not rename those flags onto oracle
  `--name`/`--base`/`--head-dir`, open the ledger alongside the oracle AID, or move
  issuance nonce claims into OracleBaser/Habery. No aliases or migration readers.
- Remove issuer-facing ceremony/finalization code from the oracle package and
  user CLI. Do not leave `write_credential` as an unchecked credential writer
  after removing its nonce guard. Audit challenge/signing/profile commands for
  the same ownership and remove issuer-only surfaces from the oracle product.
- Keep only consumer-side canonical identifier parsing required by status
  receipt/chain selection. Rehome the minimal credential assembly/signing needed
  for qualification into test-owned issuer helpers or the actual issuer tool;
  keep those helpers outside the installed oracle runtime and production CLI.
- Remove obsolete nonce/ceremony tests and operational recipes with the code.
  Preserve the maintained witnessed issuance and two-process qualification,
  including topology and real transfer/replacement/revocation assertions.
- Keep OracleBaser's authenticated-webhook replay claims and Ethereum signed
  transaction nonce journal. Those are oracle transport/posting state and are
  distinct from the association proof's `u` nonce.

**Verifier acceptance-policy companion:**

- Record an ADR that consumes an exact `(issuerAid, nonce)` only when an
  association becomes application-accepted. Re-delivery of that same association
  SAID is idempotent. A different SAID with an already accepted pair is rejected,
  including another wallet or LE credential. Another issuer may use the same
  nonce. Revocation or supersession never releases a consumed nonce.
- Read the issuer and exact `u` from the verified native association credential;
  do not trust a new webhook field or infer the nonce from status metadata.
  Preserve whitespace and Unicode representation. Use an unambiguous digest key
  and a minimal typed claim that binds issuer, nonce and the accepted SAID.
- Bind claims in the existing VerifierBaser using its current service
  `name`/`base`/`head_dir` and owning LMDB context. Add no separate environment,
  nonce CLI options, mutex, thread pool, locking layer or generic repository.
- Extend the existing `VerifierBaser.accept()` write transaction so the nonce
  claim, accepted association and any outbox obligation commit together. A
  conflicting claim must produce a terminal local `NONCE_REUSED` rejection,
  with no acceptance event/outbox entry and no retry loop. Keep this policy
  outcome distinguishable from invalid signature/evidence.
- Do not consume for pending/missing evidence, failed proof/account checks or
  otherwise unaccepted candidates. Existing accepted-SAID redelivery must return
  the original status/event without re-consuming or issuing another delivery.
- Apply the policy regardless of webhook configuration. Today AcceptanceDoer is
  composed only when a webhook is configured; resolve that domain/delivery
  coupling explicitly in the companion design. Optional delivery must not become
  a bypass of accepted-nonce uniqueness.
- Keep the verifier-to-oracle wire contract unchanged. This acceptance policy
  belongs entirely to the producer; the oracle needs no association nonce field
  or duplicate nonce check.

**Acceptance gates and rollout:**

1. Prove sequential duplicate handling and real independent-process races with
   native LMDB: one accepted SAID for a pair, idempotent same-SAID replay, terminal
   rejection for another SAID, different-issuer reuse, exact Unicode/whitespace,
   restart, revocation/supersession without reuse, and malformed claim rejection.
2. Crash/fault tests cover rollback before acceptance commit and recovery after
   commit but before ACK. There must never be a consumed-but-unaccepted claim or
   accepted-but-unclaimed association. Rejected candidates create no accepted
   outbox event; invalid proofs cannot burn a nonce.
3. Qualify with delivery enabled and disabled. Test the actual verifier intake
   path with a newly issued, otherwise valid second association reusing a nonce;
   do not substitute injected acceptance records for that integration evidence.
4. Use a deliberate fresh acceptance-state baseline for the new policy. Do not
   silently adopt a populated store that has accepted associations but no nonce
   history, or claim global uniqueness across independent verifier stores. No
   historical backfill/migration is implied by this plan.
5. Run verifier focused/full installed-wheel checks, oracle full installed-wheel
   checks and the maintained paired qualification. The oracle wheel/CLI must
   contain no association issuance nonce store or issuer finalization workflow;
   webhook replay rejection and Ethereum posting recovery must remain covered.
6. Review the coordinated changes, merge the verifier policy first if the oracle
   removal PR advances its producer pin, then merge the oracle removal. Publish
   exact qualified revisions and remaining deployment limits. This plan does not
   authorize implementing either ownership PR in the current locking correction.

### 5. Make an empty policy configuration explicit and fail closed

Owner: vlei-comply, `src/policy/PolicyManager.sol`.

The current empty loop returns Pass. Make zero policies reject authorization,
including removal of the final policy, or require an explicit separately governed
bypass mode if a concrete requirement emerges. Do not infer bypass from missing
configuration. Keep the documented AllowAll engine confined to explicit demos.

Acceptance: unconfigured manager, last-policy removal, configured Pass and
configured Fail tests; deploy scripts demonstrate an intentional active policy.

### 6. Close LMDB readers before yielding to RPC

Owner: vlei-oracle, `endorsement/posting.py`.

The status iterator currently holds a read transaction across network yields.
Copy a bounded page out, close the cursor/transaction, then perform effects.
Use a resumable fair scan so a failing or inactive first page cannot starve other
wallets. Do not replace this with an unbounded full-database snapshot.

Acceptance: real LMDB reader lifetime is checked during suspended RPC work;
concurrent receipt/writes continue; multiple pages progress fairly; selected
association is rechecked before a newly prepared effect. Preserve single-writer
ordering and recorded transaction identities.

### 7. Give the current transaction journal bounded recovery

Owner: vlei-oracle, `endorsement/posting.py` and `basing.py`.

A signed intent persisted before submission can have no receipt forever, blocking
all later actions. Add a small, explicit inspect/resubmit/reconcile path for that
same signed intent. Resolve unknown submission, pending receipt, canonical
confirmation/reversion and consumed-nonce conflict deliberately. Never allocate a
second action while the first nonce is unresolved. Do not resurrect the deleted
combined-runtime cancellation/reorg framework.

Acceptance: process interruption before send and after send, lost response,
restart, exact-byte resubmission, conflicting nonce and reorg evidence; later
withdrawals progress after supported recovery. Document operator actions and
limits, rather than calling receipt-only polling recovery.

### 8. Support validated peer key updates during operation

Owners: EOA Verifier and oracle delivery/receipt boundaries.

Startup `--verifier-kel` alone requires file replacement and service restart after
rotation. Add a narrow native KERI update path for the configured peer, with
validated contiguous KEL processing and explicit trust identity. Do not accept
new key material just because an HTTP signature claims a known AID, and do not
bring credential verification back into the oracle.

Acceptance: live peer rotation, stale signer, out-of-order/missing key events,
untrusted peer and restart tests through the real signed transport.

### 9. Establish one signed transport profile and remove duplicate policy

Owners: EOA Verifier `delivery/authing.py` and oracle `authing.py`.

Both maintain the same manual signature-base rules around pinned KERIpy APIs.
First define one authoritative closed profile and shared conformance vectors for
headers, target URI, body digest, nonce, timestamps and ACKs. Then use a narrow
shared implementation only if it reduces real maintenance cost; no general
cross-repository core framework. Remove old import fallbacks and duplicated
implementations when the chosen owner is consumed by both installed wheels.

Acceptance: both installed packages pass the same positive and tampered vectors,
including rotation and replay; cross-process delivery remains qualified without
checkout imports or monkeypatch compatibility.

## Review and completion discipline

For every PR, inventory the inbound callers before deleting an API, then inspect
source, CLI help, tests, scripts, docs and the built wheel for stale entry points.
Tests of intentionally removed behavior leave with that behavior. Retain or add
regressions at surviving authority and durability boundaries, not frozen file
rosters. Run the smallest relevant checks first, then installed artifacts and
paired qualification where shared contracts change. Report exact revisions and
unrun integration limits. Do not treat a green fixture as deployment readiness.

PRs 1 and 2 are merged. Task 3 is qualified without application locking and awaits PR review. Subsequent PRs follow in this order after review; this
plan is not authorization to deploy, migrate stores or merge without review.

## PR 1 qualification record

On 2026-10-31, the full installed-wheel oracle suite passed: **141 passed** in
144.34 seconds. This includes the real two-process paired qualification, with
verifier `486fb42f92b6c3269d7968934448f21121ef08aa`, comply
`cdaa111041e50f56d2820b84cf15f18c96c45ac4` and Foundry 1.5.1. Tests ran from a
temporary source copy against a fresh environment, outside the working checkout.
The final wheel's Python/JSON contents match the current source byte for byte;
all 24 installed oracle modules import, and the removed runtime modules are absent.
Current service CLI help succeeds and the retired command fails parsing. The
suite retains HIO's existing datetime deprecation warnings.

The removed tests exercised intentionally removed behavior. The witnessed issuer,
two application processes, two wallets, real contract transfers, lost ACK,
restart, replacement and exact revocation scenario remains intact. This is local
qualification, not deployment evidence. The active posting-reader lifetime,
receipt-only recovery and other later PRs above remain unfinished.

## PR 2 qualification record

On 2026-10-31, the reduced contract deployment passed **28 Forge tests** with
Foundry 1.5.1 and **four launcher checks**. The oracle companion passed **141
installed-wheel tests** in 153.74 seconds, including the unchanged two-process
witnessed issuance, signed receipt, transfer, replacement, restart and revocation
scenario against the new contract source. The qualified contract revision is
`4c83e465b3d7937f02b95e805cca5b79f5015078`; the verifier remains pinned to
`486fb42f92b6c3269d7968934448f21121ef08aa`.

The wrapper has only direct owner-initiated transfer routes. Mediation, pending
settlement, unused enrichment fields, credential/trusted-issuer deployments and
compatibility aliases are removed. Wallet lookup always resolves the registry's
current identity; caller hints cannot substitute for it. Audit-hash and rejection
regressions pass. The oracle's two packaged registry ABIs match the new build;
the deploy-log parser no longer requires a credential-registry address. CI,
demo setup and current operator guides now select the new contract revision.

Review and merge the contract removal before the oracle pin companion. Use the
oracle companion's head commit as `ORACLE_REVISION` for the contract guide's local
qualification. PR 3 is now the separately requested NonceLedger refactor. Task 4 now removes misplaced association-nonce ownership; task 5 addresses the
existing empty-policy limitation documented in the contract security model. Later operating fixes
remain separate. No storage or contract migration is provided.

## Initial task 3 qualification (superseded by the no-mutex correction)

Implemented on `refactor/nonce-ledger-lmdber`, based on merged oracle
`2b028c9299426a61a7dab68a80b6357d53ac572f`. Kent authorized publishing the
qualified refactor on 2026-10-31; PR review is pending.

The ledger now subclasses LMDBer, binds a typed, non-duplicate `consumed.` Komer
in `reopen()`, and uses explicit insert-without-overwrite plus same-transaction
conflict inspection. The initial candidate retained a per-ledger writer lock; Kent has rejected it.
The correction below removes it. Direct environment
opening, manual directory creation and custom enter/exit lifecycle methods are
removed. Owning call sites use `dbing.openLMDB`; ceremonies borrow the ledger.

Persistent layout: `<head>/vlei-oracle/nonces/<base>/<name>/data.mdb` and
`lock.mdb`. The CLI requires `--nonce-head-dir`, with optional `--nonce-base`
and `--nonce-name` (default `issuer-nonces`). Exact issuer/nonce pairs are stored
as typed NonceClaim records under 64-character hexadecimal SHA-256 keys. Relative
roots are frozen to an absolute path. Primary and alternate Filer directory
settings resolve to the same selected store; unavailable roots fail instead of
redirecting to a fresh namespace. Explicit root or temporary-mode changes override cached-path reuse. Failed
remakes preserve the previous persistent mode and invalidate stale cached paths;
cleanup cannot delete a previous persistent store or retry against the wrong
namespace. Post-open initialization failures close acquired resources and
preserve persistent consumption.

Validation on 2026-10-31:

- **37 focused checks passed from the fresh installed wheel.** Coverage includes
  persistent reopen/closure, temporary cleanup, deferred opening, stable relative
  paths, explicit root changes, rejected fallback, failed initialization cleanup,
  failed temporary reopen and persistent-path protection during temporary reuse,
  Unicode and issuer scoping, 64 contending thread claims and four simultaneous
  independent processes, malformed/conflicting rows, and current CLI selection.
  Existing proof-before-consumption, existing-output and post-commit output-failure
  assertions remain intact, including two-process artifact publication.
- **156 full installed-wheel oracle tests passed in 146.62 seconds**, including
  the unchanged maintained two-process qualification. Verifier:
  `486fb42f92b6c3269d7968934448f21121ef08aa`; qualified contracts:
  `4c83e465b3d7937f02b95e805cca5b79f5015078` (the merged contract tree);
  Foundry 1.5.1. Existing HIO datetime deprecation warnings remain.
- Inspected the installed `gleif_keri==1.2.19`, `gleif_hio==0.6.22` and
  `lmdb==1.8.1` implementations. The fresh environment's dbing, koming and Filer
  source files match those inspected byte for byte. Native synchronous commit
  and process-lock flags are asserted. The wheel's Python/JSON files match the
  working source byte for byte.
- Candidate wheel SHA-256:
  `04ca27c156b56c90e3678e281634e99c54baf4e1a387255f0b7b0dc21d008a00`.

Remaining limits: use one open ledger per store in each process, shared by its
borrowers. Keep persistent namespace selection consistent and use fresh nonces
for a deliberately new store; there is no migration, old-format reader or replay
protection across independently selected empty stores. A post-commit failure can
burn a nonce by design. Native temporary cleanup removes the ledger directory;
the pinned Filer can leave empty ancestor directories. Qualification was local macOS; Linux CI is not run before review. This task adds
no custom parent-directory cleanup or unrelated persistence changes. Publication is approved; the next implementation task after PR review is task 4, removing association-
nonce ownership from the oracle with the verifier-policy companion. The empty-
policy configuration fix follows as task 5.

## Current PR #49 locking correction qualification

Kent's 2026-10-31 correction removes the application mutex and all shared-thread
coordination from nonce handling. The implementation now relies solely on one
native LMDB transaction per claim. There is no application lock, thread pool,
barrier, queue or retry loop. Synchronous caller ordering follows KERIpy/HIO's
normal execution model; native LMDB coordinates separate processes.

The pinned binding rejects overlapping writes on the same environment with
`lmdb.Error` / Resource busy. The removed shared-thread tests required the
application to queue those calls; that is no longer the contract. A deterministic
native-overlap regression now verifies the error propagates without consuming a
nonce. Synchronous duplicate-publication checks remain, as do the actual four-
process ledger test and two-process ceremony test. The process harness now uses
ordinary subprocesses instead of threading, multiprocessing barriers or queues.
No error is converted into a duplicate or hidden by a retry.

**28 focused checks passed. The full fresh installed-wheel suite passed 147 tests
in 153.38 seconds**, including the unchanged maintained two-process qualification.
The reduced count reflects removal of the ten parametrized shared-thread ceremony
cases in favor of one synchronous duplicate case; no maintained paired-system
assertion or topology was weakened. Native durability, issuer scope, exact
Unicode, malformed-claim rejection, failure cleanup and persistent lifecycle
coverage remain. Existing dependency warnings remain.

The qualified contracts remain `4c83e465b3d7937f02b95e805cca5b79f5015078`; verifier
remains `486fb42f92b6c3269d7968934448f21121ef08aa`, with Foundry 1.5.1 and GLEIF
KERI/HIO 1.2.19/0.6.22 plus LMDB 1.8.1. Candidate wheel SHA-256:
`7d6a899d5d9cdd17a7f794a7e4796f17968841b07848154662d681b48726488f`.

This PR deliberately does not move issuer nonce state into the oracle AID store,
rename its temporary nonce flags or implement verifier nonce policy. Task 4 is
the subsequent ownership-removal wave. Oracle transport replay claims and
Ethereum transaction nonces are outside that removal.
