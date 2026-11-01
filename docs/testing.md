# Find and run oracle tests

Run from the root using python -m pytest; pytest.ini selects importlib mode and
tests is an explicit package. No PYTHONPATH/sys.path workarounds are required.

| Production subject | Test location / example |
|---|---|
| runtime/cli.py | tests/runtime/test_cli.py |
| Runtime identity/config/composition | tests/runtime/test_serving.py |
| Startup setting warnings and retained journal | tests/runtime/test_scoping.py |
| Exact dependency checkout | tests/integration/test_checkouts.py |
| Environment lifecycle/preflight | tests/integration/test_environment.py |
| StatusEventService and StatusEnd.on_post | python -m pytest tests/receiving/test_status.py |
| EndorsementQuery, HTTP resource and delivery phases | tests/integration/test_publication.py |
| receiving/authing.py | tests/receiving/test_authing.py |
| update_selection | python -m pytest tests/associations/test_selecting.py -k same_le |
| StatusEventWriter / PostingWriter atomic writes | tests/persistence/test_status_events.py and test_posting.py |
| endorsement/decisions.py | tests/endorsement/test_decisions.py |
| ContractEffects, RPC and ABI | tests/endorsement/test_effects.py |
| Transaction restart, retries and recovery | tests/integration/test_recovery.py |
| RPC failures and detached-reader lifetime | tests/integration/test_rpc_lifetime.py |
| Both services, witnessed issuers and real transfers | tests/qualification/test_two_services.py |

Use ::test_function_name for one function or -k expression for a class/method.
Named support modules are tests/support/contracts.py, processes.py and
status_events.py. Shared peer/deployment fixtures retain function/module scope in
conftest. No test imports another test module. Proof/schema conformance belongs to Verifier; Oracle retains mapping/ABI
integration and the maintained paired scenario. Producer completion-gap tests belong to Verifier; Oracle signs its own focused
post-verification fixtures.

Run python -m pytest for the full suite. Full tests need
Foundry 1.5.1, jq, ORACLE_WHEEL and VLEI_COMPLY_DIR pointing to the qualified
checkout. See [installed-wheel qualification](two-service-qualification.md). CI
installs fresh wheels and copies tests, scripts, protocol fixtures and pytest.ini
outside checkout.
Support is excluded from wheels. Run one repository's test package per invocation.

The qualification file is the only maintained paired scenario. Oracle
`python -m scripts.integration.env flow --run-dir RUN` invokes that exact file
against its started environment. Verifier commands own witnessed issuer work;
Comply commands own deployment. No cross-repository Python internals are imported.

## Test and tooling ownership

Group tests by the behavior under assertion, rather than the class that previously
owned it. Keep full contract publication scenarios in integration; persistence
tests isolate atomic event/selection writes or posting journal outcomes. Shared
posting construction and settling live in tests/support/posting.py. The paired
run fixture lives in tests/qualification/conftest.py; genuinely shared peer and
contract fixtures retain their original scopes in tests/conftest.py.

Tooling preparation lives in preparing.py, wheel installation in builds.py, Oracle
identity setup in identity.py, and chain/deployment work in chain.py. services.py
launches Oracle and the response-loss proxy; verifier.py calls the external
Verifier fixture CLI. environment.py sequences lifecycle and qualification.
These are concrete modules within the existing integration tooling package.

OOBI introduction tests exercise real HTTP and the pinned KERIpy resolver.
Resolution requires accepted key state; Oracle also binds it to the authorized
producer AID. Rotation is retrieved from the same OOBI on restart. Startup no
longer imports public KEL files. Credential/TEL evidence transport is unchanged.
