# Oracle application and persistence separation

## Decision and boundaries

The application prepares valid domain records using detached, bounded reads.
Explicit writers serialize prepared values and commit related records together.
OracleBaser owns LMDB lifecycle and typed table bindings, not receipt policy,
selection, posting decisions, retry budgets, or REST representations.

The runtime has one cooperative application writer. Receipt handling is synchronous:
no yield, RPC, or open transaction spans its read/decide/write unit of work.
Posting may yield for RPC, but no LMDB transaction survives a yield; selection is
rechecked before signing. Offline recovery requires the service to be stopped.
Multiple processes writing application workflow state concurrently are unsupported.
Short writer-side integrity checks remain where they protect phase membership or
immutable signed bytes; they are not a reason to move policy back into storage.

Preserve dedicated phase tables, explicit transition names, FIFO arrival order,
transport replay rejection, terminal revocation, selection isolation, signed-byte
journaling before broadcast, finite retry/dead letters and local-only query state.
No compatibility APIs, migrations, generic transition framework, extra locking,
architecture-check scripts, or on-chain reconciliation are introduced.

## Implementation sequence

1. Receipt service: move duplicate/revision/terminal-state decisions and selection
   out of the database. Prepare posting work before one atomic receipt commit.
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

- Slice 1 implemented: ReceiptService owns preparation; ReceiptWriter commits the
  receipt/status/selection/queue records atomically. Validation recorded in its PR.
- Slice 2 implemented: OracleBaser now only binds tables and owns lifecycle.
  Feature readers return detached bounded reads; PostingWriter owns explicit atomic
  phase writes and integrity guards. ScopeService validates deployment bindings.
  Old Baser workflow methods are removed.
- Slice 3 implemented: PostingService owns policy, PostingDoer schedules it,
  RecoveryService prepares offline retries and EndorsementQuery prepares local
  progress. HTTP resources and CLI adapt these services; runtime composes their
  readers, writers and effects once. Authentication borrows the transport replay
  writer rather than a database handle. Tests follow their application/writer
  owners and retain real receipt, rollback, restart and chain coverage.

## Review and qualification

- [Receipt policy PR #61](https://github.com/GLEIF-IT/vlei-oracle/pull/61): 17 focused
  tests and four receipt tests including the real transaction-boundary assertion.
- [Persistence boundary PR #62](https://github.com/GLEIF-IT/vlei-oracle/pull/62): 20
  focused tests plus actual receipt-write interruption/rollback.
- Final application-service PR records the focused and fresh installed-wheel
  results, including maintained paired qualification. Full qualification is a
  merge gate for every slice. Exact Verifier and Comply pins remain unchanged;
  neither companion needs a source change for these internal Oracle APIs.


## Feature persistence cohesion

The earlier application-wide adapters are removed, not retained as facades.

| Persistence unit | Consumers / atomic responsibility |
|---|---|
| AssociationReader | ReceiptService, PostingService and EndorsementQuery; event, status, claim and selected-association reads. |
| ReceiptWriter | ReceiptService; old-event recording or one receipt/status/selection/queue/sequence commit. |
| PostingReader / PostingWriter | PostingService and RecoveryService; explicit phases, signed journal, confirmed effects, progress and recovery fences. ReceiptService borrows sequence reads and EndorsementQuery borrows posting queries explicitly. |
| ScopeStore | ScopeService deployment bindings only. |
| transport.claim_nonce | A bound callable supplied to ServiceAuthenticator, not a posting writer. |

No cross-feature atomic operation is split into independently committing calls.
Queries retain bounded per-identifier phase lookups. The association-only public
endorsement response, wire values, layout, one-item priority and retry behavior
remain unchanged. ContractEffects remains a focused RPC adapter.

Function names rely on feature context: AssociationReader.selected;
ReceiptWriter.record_and_queue; PostingReader.seq / tx / effects; and
PostingWriter.queued_to_prep / prep_to_pending / pending_to_prep.
`prep` means the preparing phase, `tx` a signed transaction record, and `seq`
the posting sequence and fence record. save_prep and save_pending persist
progress within their named phase. ScopeStore.bind_inbox / bind_posting retain
the existing deployment-binding semantics. No table or wire names change.
