# Verified revocation and exact withdrawal

The issuer issues credential TEL revocation. Verifier records revoked association status only after KERIpy validates it. Oracle withdraws the corresponding endorsement; replacement selection only supersedes associations.

![Verified revocation and exact withdrawal](revocation-status-sequence.svg)

<details>
<summary>Editable Mermaid source</summary>

```mermaid
%%{init: {"theme":"base","themeVariables":{"actorBkg":"#003336","actorTextColor":"#FFFFFF","signalColor":"#003336","signalTextColor":"#003336","noteBkgColor":"#E5DCE6","noteTextColor":"#003336","loopTextColor":"#003336","labelTextColor":"#003336","labelBoxBkgColor":"#DFFBC0","labelBoxBorderColor":"#6A7B7F","actorLineColor":"#6A7B7F","lineColor":"#6A7B7F"},"sequence":{"mirrorActors":true,"wrap":true}}}%%
sequenceDiagram
    autonumber
    participant E as Issuer / KERI evidence
    participant V as EOA Verifier
    participant O as Oracle
    participant C as Contract registry
    E->>V: Revocation TEL and witnessed issuer KEL anchor
    V->>V: KERIpy validates credential TEL revocation
    V->>V: Record revoked association status and its own delivery event
    V->>O: Authenticated revoked event
    O->>O: Commit receipt, selection and queued withdrawal
    O-->>V: Authenticated ACK for this event/attempt
    O->>C: Withdraw exact association SAID
    alt Current mapping still names revoked SAID
        C->>C: Clear endorsement and emit withdrawal
    else A successor is already mapped
        C->>C: Preserve successor, return false
    end
    C-->>O: Transaction receipt
    Note over V,C: Outages and latency can leave on-chain authority until withdrawal
```

</details>

Missing evidence is not revocation. Already confirmed token transfers are not undone. [Oracle API](../../../protocol/oracle-api-v1/README.md) and [Verifier API](https://github.com/GLEIF-IT/eoa-verifier/blob/master/protocol/verifier-api-v1/README.md) own their respective transitions.
