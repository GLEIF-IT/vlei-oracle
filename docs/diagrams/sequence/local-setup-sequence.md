# Local independent-service setup

Use explicitly pinned installed applications, fresh demo stores and a separately authorized Oracle writer.

![Local independent-service setup](local-setup-sequence.svg)

<details>
<summary>Editable Mermaid source</summary>

```mermaid
%%{init: {"theme":"base","themeVariables":{"actorBkg":"#003336","actorTextColor":"#FFFFFF","signalColor":"#003336","signalTextColor":"#003336","noteBkgColor":"#E5DCE6","noteTextColor":"#003336","loopTextColor":"#003336","labelTextColor":"#003336","labelBoxBkgColor":"#DFFBC0","labelBoxBorderColor":"#6A7B7F","actorLineColor":"#6A7B7F","lineColor":"#6A7B7F"},"sequence":{"mirrorActors":true,"wrap":true}}}%%
sequenceDiagram
    autonumber
    participant U as Operator
    participant L as Comply launcher
    participant V as Installed Verifier environment
    participant O as Oracle
    participant C as Anvil / contracts
    U->>L: Select asset checkouts, executables and EOA_VERIFIER_PYTHON
    L->>V: Read packaged association schema
    L->>L: Prepare witness / schema service
    L->>C: Deploy contracts and configure wallet policy
    U->>U: Create independent service AIDs and stores
    U->>C: Fund and authorize dedicated Oracle writer
    L->>V: Start verifier with trusted GEDA, chain/RPC and webhook peer
    L->>O: Start Oracle with trusted Verifier, chain and registry addresses
    L->>V: Introduce Oracle public KEL
    L->>O: Introduce Verifier public KEL
```

</details>

See the [portable Comply setup guide](https://github.com/GLEIF-IT/vlei-comply/blob/main/docs/gl1-vlei-local-infra-setup.md). Startup does not issue association credentials.
