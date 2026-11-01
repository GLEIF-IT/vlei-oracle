# Find and run oracle tests

Run from the root using python -m pytest; pytest.ini selects importlib mode and
tests is an explicit package. No PYTHONPATH/sys.path workarounds are required.

| Production subject | Test location / example |
|---|---|
| runtime/cli.py | tests/runtime/test_cli.py |
| Runtime identity/config/composition | tests/runtime/test_serving.py |
| Demo checkout tool | tests/runtime/test_demo_checkout.py |
| StatusEnd.on_post | python -m pytest tests/receiving/test_status.py |
| receiving/authing.py | tests/receiving/test_authing.py |
| update_selection | python -m pytest tests/associations/test_selecting.py -k same_le |
| OracleBaser atomic receipt/journal | tests/persistence/test_basing.py |
| endorsement/decisions.py | tests/endorsement/test_decisions.py |
| ContractEffects, RPC and ABI | tests/endorsement/test_effects.py |
| Signed ingress through real posting/recovery | tests/integration/test_status_posting.py |
| Both services, witnessed issuers and real transfers | tests/qualification/test_two_services.py |

Use ::test_function_name for one function or -k expression for a class/method.
Named support modules are tests/support/issuers.py, contracts.py, processes.py and
status_events.py. Shared peer/deployment fixtures retain function/module scope in
conftest. No test imports another test module. Proof/schema conformance belongs to Verifier; Oracle retains mapping/ABI
integration and the maintained paired scenario. Collection is 61 cases.

Run python -m pytest for the full suite. Full tests need
Foundry 1.5.1 and VLEI_COMPLY_DIR pointing to the qualified checkout. CI installs
fresh wheels and copies tests, scripts and pytest.ini outside checkout.
Support is excluded from wheels. Run one repository's test package per invocation.

The qualification file is the only maintained paired scenario. The comply
launcher changes to the selected oracle root and invokes that exact file with
its installed Python, preserving witnesses, actors, stores and process boundaries.
