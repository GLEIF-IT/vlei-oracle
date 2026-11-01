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
    participant L as Oracle environment CLI
    participant V as Installed Verifier environment
    participant O as Oracle
    participant C as Anvil / contracts
    U->>L: Select exact source revisions and application wheels
    L->>V: Prepare witnesses, schema hosting and source credentials
    L->>C: Invoke Comply deployment and authorize Oracle writer
    L->>O: Prepare independent Oracle AID and stores
    L->>O: Start with authorized Verifier AID/OOBI and registry addresses
    L->>V: Start with trusted GEDA, chain/RPC and optional webhook OOBI
    V->>O: Resolve Oracle OOBI for signed responses
    O->>V: Resolve authorized Verifier OOBI
    Note over V,O: KERIpy validates KEL before authenticated delivery
```

</details>

See the [portable Comply setup guide](https://github.com/GLEIF-IT/vlei-comply/blob/main/docs/gl1-vlei-local-infra-setup.md). Startup does not issue association credentials.
