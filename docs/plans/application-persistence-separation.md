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

- Slice 1 implemented: ReceiptService owns preparation; OracleWriter commits the
  receipt/status/selection/queue records atomically. Validation recorded in its PR.
- Slices 2 and 3 pending.
