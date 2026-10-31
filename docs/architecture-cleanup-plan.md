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

### 2. Remove wrapper workflows incompatible with wallet association v1 (implemented; review pending)

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

### 3. Make an empty policy configuration explicit and fail closed

Owner: vlei-comply, `src/policy/PolicyManager.sol`.

The current empty loop returns Pass. Make zero policies reject authorization,
including removal of the final policy, or require an explicit separately governed
bypass mode if a concrete requirement emerges. Do not infer bypass from missing
configuration. Keep the documented AllowAll engine confined to explicit demos.

Acceptance: unconfigured manager, last-policy removal, configured Pass and
configured Fail tests; deploy scripts demonstrate an intentional active policy.

### 4. Close LMDB readers before yielding to RPC

Owner: vlei-oracle, `endorsement/posting.py`.

The status iterator currently holds a read transaction across network yields.
Copy a bounded page out, close the cursor/transaction, then perform effects.
Use a resumable fair scan so a failing or inactive first page cannot starve other
wallets. Do not replace this with an unbounded full-database snapshot.

Acceptance: real LMDB reader lifetime is checked during suspended RPC work;
concurrent receipt/writes continue; multiple pages progress fairly; selected
association is rechecked before a newly prepared effect. Preserve single-writer
ordering and recorded transaction identities.

### 5. Give the current transaction journal bounded recovery

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

### 6. Support validated peer key updates during operation

Owners: EOA Verifier and oracle delivery/receipt boundaries.

Startup `--verifier-kel` alone requires file replacement and service restart after
rotation. Add a narrow native KERI update path for the configured peer, with
validated contiguous KEL processing and explicit trust identity. Do not accept
new key material just because an HTTP signature claims a known AID, and do not
bring credential verification back into the oracle.

Acceptance: live peer rotation, stale signer, out-of-order/missing key events,
untrusted peer and restart tests through the real signed transport.

### 7. Establish one signed transport profile and remove duplicate policy

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

PR 1 is merged and PR 2 is implemented and locally qualified. Subsequent PRs
follow in this order after review; this
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
qualification. PR 3 (fail-closed empty policy configuration) is next; this existing
limitation is documented in the contract security model. Later operating fixes
remain separate. No storage or contract migration is provided.
