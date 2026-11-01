# Oracle application and persistence separation

Current transport decision: the [optional HTTP signatures plan](https://github.com/GLEIF-IT/eoa-verifier/blob/master/docs/plans/optional-http-signatures.md) supersedes earlier mutual-ACK and transport replay-claim requirements.


## Decision and boundaries

The application prepares valid domain records using detached, bounded reads.
Explicit writers serialize prepared values and commit related records together.
OracleBaser owns LMDB lifecycle and typed table bindings, not status-event policy,
selection, posting decisions, retry budgets, or REST representations.

The runtime has one cooperative application writer. Status-event handling is synchronous:
no yield, RPC, or open transaction spans its read/decide/write unit of work.
Posting may yield for RPC, but no LMDB transaction survives a yield; selection is
rechecked before signing. Offline recovery requires the service to be stopped.
Multiple processes writing application workflow state concurrently are unsupported.
Short writer-side integrity checks remain where they protect phase membership or
immutable signed bytes; they are not a reason to move policy back into storage.

Preserve dedicated phase tables, explicit transition names, FIFO arrival order,
event deduplication, terminal revocation, selection isolation, signed-byte
journaling before broadcast, finite retry/dead letters and local-only query state.
No compatibility APIs, migrations, generic transition framework, extra locking,
architecture-check scripts, or on-chain reconciliation are introduced.

## Implementation sequence

1. Status-event service: move duplicate/revision/terminal-state decisions and selection
   out of the database. Prepare posting work before one atomic status-event commit.
2. Persistence boundary: extract detached readers and explicit atomic writers;
   move scope validation above persistence and remove all workflow APIs from Baser.
3. Posting and adapters: extract posting, recovery and query application services;
   keep scheduling and HTTP adapters thin. Update documentation and qualify the
   final installed wheel with the maintained exact Verifier/Comply pins.

Each slice is a separate reviewable PR, merged after focused checks and CI pass.
Use existing tests for replay, reordered revocation, replacement, rollback,
restart, retries and withdrawal. Add focused boundary tests where existing
assertions cannot distinguish preparation from an open write transaction.
Run the full installed-wheel suite including paired qualification at the final
revision. Do not weaken assertions or change integration topology.

## Progress

- Slice 1 implemented: StatusEventService owns preparation; StatusEventWriter commits the
  event/status/selection/queue records atomically. Validation recorded in its PR.
- Slice 2 implemented: OracleBaser now only binds tables and owns lifecycle.
  Feature readers return detached bounded reads; PostingWriter owns explicit atomic
  phase writes and integrity guards. Startup functions record public settings and warn on changes.
  Old Baser workflow methods are removed.
- Slice 3 implemented: PostingService owns policy, PostingDoer schedules it,
  RecoveryService prepares offline retries and EndorsementQuery prepares local
  progress. HTTP resources and CLI adapt these services; runtime composes their
  readers, writers and effects once. Signature verification is stateless; no transport replay writer remains. Tests follow their application/writer
  owners and retain real status-event, rollback, restart and chain coverage.

## Review and qualification

- [Status-event policy PR #61](https://github.com/GLEIF-IT/vlei-oracle/pull/61): 17 focused
  tests and four status-event tests including the real transaction-boundary assertion.
- [Persistence boundary PR #62](https://github.com/GLEIF-IT/vlei-oracle/pull/62): 20
  focused tests plus actual status-event-write interruption/rollback.
- Final application-service PR records the focused and fresh installed-wheel
  results, including maintained paired qualification. Full qualification is a
  merge gate for every slice. Exact Verifier and Comply pins remain unchanged;
  neither companion needs a source change for these internal Oracle APIs.


## Feature persistence cohesion

The earlier application-wide adapters are removed, not retained as facades.

| Persistence unit | Consumers / atomic responsibility |
|---|---|
| AssociationReader | StatusEventService, PostingService and EndorsementQuery; event, status, claim and selected-association reads. |
| StatusEventWriter | StatusEventService; old-event recording or one event/status/selection/queue/sequence commit. |
| PostingReader / PostingWriter | PostingService and RecoveryService; explicit phases, signed journal, confirmed effects, progress and recovery fences. StatusEventService borrows sequence reads and EndorsementQuery borrows posting queries explicitly. |
| ScopeStore | Advisory inbox/posting startup records; read/save only. |
| HttpSignatures | Stateless signature checks; StatusEnd separately authorizes the configured producer. |

No cross-feature atomic operation is split into independently committing calls.
Queries retain bounded per-identifier phase lookups. The association-only public
endorsement response, wire values, layout, one-item priority and retry behavior
remain unchanged. ContractEffects remains a focused RPC adapter.

Function names rely on feature context: AssociationReader.selected;
StatusEventWriter.record_and_queue; PostingReader.seq / tx / effects; and
PostingWriter.queued_to_prep / prep_to_pending / pending_to_prep.
`prep` means the preparing phase, `tx` a signed transaction record, and `seq`
the posting sequence and fence record. save_prep and save_pending persist
progress within their named phase. ScopeStore.save_inbox / save_posting persist
the last observed settings for startup warnings. No table or wire names change.

## Advisory startup settings

The operator owns compatibility with retained state. Replace ScopeService and
immutable bindings with explicit note_inbox/note_posting startup functions:
compare, warn, save the latest public settings, and continue. No journal,
selection, phase, signature or confirmed-effect record changes with the settings.
Tests cover every compared field, quiet first/unchanged starts, changes back,
retained signed work/effects and real runtime composition. README and operator
instructions explain that a warning performs no migration or replay.

## Status-event and transaction-receipt terminology

receiving/status_events.py exposes StatusEventService;
persistence/status_events.py exposes StatusEventWriter. The HTTP endpoint still
owns authentication and the signed ACK; the service prepares status-event and
selection work; the writer commits prepared records atomically. record_event and
record_and_queue retain their short names in that explicit class context.

ContractEffects.transaction_receipt returns txReceipt, the Ethereum RPC result;
txRecord remains the persisted transaction journal record. A signed ACK confirms
durable status-event storage, not selection or Ethereum publication. KERI witness
receipts and Ethereum transaction receipts retain those qualified names.

No wire values, event IDs, signed payloads, LMDB keys/subdatabases, transaction
boundaries, retry policy or business behavior change. The coordinated PRs record
focused, installed-wheel and paired qualification results and exact pins.

## Follow-up: test and tooling organization

Preserve the component and explicit phase-transition design. Reclassify policy
tests under their feature, name atomic persistence tests by their record family,
and keep real cross-component scenarios under integration. Separate concrete
tooling preparation and service-launch responsibilities while keeping lifecycle
sequencing explicit. Update the test guide with current paths. This follow-up
changes neither application source nor protocol/storage behavior; qualification
results and exact dependency pins are recorded in the organization PRs.
