# Oracle status-event processing, selection and publication

Verifier has completed initial verification and application acceptance. Oracle trusts authenticated producer facts.

![Oracle status-event processing, selection and publication](oracle-endorsement-sequence.svg)

<details>
<summary>Editable Mermaid source</summary>

```mermaid
%%{init: {"theme":"base","themeVariables":{"actorBkg":"#003336","actorTextColor":"#FFFFFF","signalColor":"#003336","signalTextColor":"#003336","noteBkgColor":"#E5DCE6","noteTextColor":"#003336","loopTextColor":"#003336","labelTextColor":"#003336","labelBoxBkgColor":"#DFFBC0","labelBoxBorderColor":"#6A7B7F","actorLineColor":"#6A7B7F","lineColor":"#6A7B7F"},"sequence":{"mirrorActors":true,"wrap":true}}}%%
sequenceDiagram
    autonumber
    participant V as EOA Verifier
    participant O as Oracle inbox
    participant P as Oracle posting worker
    participant C as Ethereum registries
    V->>O: Authenticated immutable status event
    O->>O: Validate trusted sender, event identifiers and chain
    O->>O: Commit status event, selection and queued operation
    O-->>V: Authenticated durable-storage ACK
    Note over V,O: ACK does not mean selected or published
    P->>P: Claim one queued operation into preparing
    P->>P: Prepare action from local confirmed effects
    P->>P: Recheck selection and persist signature in pending
    P->>C: Submit the persisted transaction
    alt Confirmed success
        C-->>P: Transaction receipt
        P->>P: Commit effect and prepare next step or complete
    else Retry budget or transaction-receipt deadline exhausted
        P->>P: Move to dead letter for manual intervention
        Note over P,C: An unresolved signature blocks later sends
    end
    Note over P,C: Completed work makes no further RPC calls
```

</details>

[Oracle API](../../../protocol/oracle-api-v1/README.md) defines selection and conflicts. Supersession changes Oracle selection, not Verifier acceptance. See [operations](../../status-inbox.md) for unresolved-transaction limits.
