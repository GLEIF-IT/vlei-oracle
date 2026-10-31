# Oracle receipt, selection and publication

Verifier has completed initial verification and application acceptance. Oracle trusts authenticated producer facts.

![Oracle receipt, selection and publication](oracle-endorsement-sequence.svg)

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
    O->>O: Atomically commit receipt, newest status and selection
    O-->>V: Authenticated durable-storage ACK
    Note over V,O: ACK does not mean selected or published
    alt Selected accepted association
        P->>P: Read desired action, prepare RPC effect
        P->>P: Recheck durable selection, journal signed intent
        P->>C: Submit registration / exact-wallet endorsement
        C-->>P: Receipt and observed registry state
        P->>P: Record confirmation, reconcile next desired action
    else Rejected or superseded selection
        P->>P: Do not publish this claim
    end
```

</details>

[Oracle API](../../../protocol/oracle-api-v1/README.md) defines selection and conflicts. Supersession changes Oracle selection, not Verifier acceptance. See [operations](../../status-inbox.md) for unresolved-transaction limits.
