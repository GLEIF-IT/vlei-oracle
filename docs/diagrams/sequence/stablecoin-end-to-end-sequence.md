# Witnessed associations to audited token transfers

This is the single full-system scenario, maintained alongside [paired qualification](../../two-service-qualification.md). It repeats association acceptance for both wallets.

![Witnessed associations to audited token transfers](stablecoin-end-to-end-sequence.svg)

<details>
<summary>Editable Mermaid source</summary>

```mermaid
%%{init: {"theme":"base","themeVariables":{"actorBkg":"#003336","actorTextColor":"#FFFFFF","signalColor":"#003336","signalTextColor":"#003336","noteBkgColor":"#E5DCE6","noteTextColor":"#003336","loopTextColor":"#003336","labelTextColor":"#003336","labelBoxBkgColor":"#DFFBC0","labelBoxBorderColor":"#6A7B7F","actorLineColor":"#6A7B7F","lineColor":"#6A7B7F"},"sequence":{"mirrorActors":true,"wrap":true}}}%%
sequenceDiagram
    autonumber
    participant I as Witnessed GEDA / QVI / LE issuers
    participant V as Installed EOA Verifier
    participant O as Installed Oracle
    participant C as Registries and wUSDC on Anvil
    participant U as Wallet owner
    I->>V: Source chain and association proof over IPEX / CESR
    V->>V: Initial validation, account observation and acceptance-time nonce claim
    V->>O: Signed accepted status
    O->>O: Commit receipt, selection and queued operation
    O-->>V: Signed ACK (drop once, retry exact event)
    O->>C: Publish selected exact-wallet mapping
    C-->>O: Confirm transaction receipt
    U->>C: Approve underlying USDC to wUSDC
    U->>C: Owner calls wrapper transfer
    C->>C: Evaluate both wallet endorsements, move USDC on Pass
    Note over U,C: Check actual sender/recipient balances
    V->>V: Restart from preserved store without another Grant
    O->>O: Resume durable phases after independent restart
    I->>V: Higher-issuance same-LE replacement
    V->>O: Accepted replacement status
    O->>C: Replace selected endorsement
    I->>V: Verified revocation evidence for old, then current SAID
    V->>O: Separate terminal events and ACKs
    O->>C: Old withdrawal preserves successor, current withdrawal clears it
    U->>C: Wrapper transfer rejected, token balances unchanged
```

</details>

Selection and receipt do not change upstream acceptance. The fixture also rejects reuse of an accepted issuer/nonce by another credential. Direct USDC transfers can bypass wUSDC; its role is auditable compliance for routed transfers. No production-readiness or malformed-stream liveness claim follows from this scenario.
