# Protocol qualification helpers

These are test-owned helpers, outside the installed oracle runtime and CLI.

- `generate_vectors.py` reproduces reference vectors using the installed EOA Verifier.
- `issuance_helpers.py` creates witnessed test issuers and native KERI evidence.
- `contract_fixture.py` performs test contract calls through HIO.
- `requirements.txt` pins qualification dependencies; install the test-only producer
  from `tests/requirements.txt` as well.

Minimal ACDC assembly lives in `tests/association_issuer.py`. There is no oracle
issuer ceremony or association nonce store. EOA Verifier owns accepted-nonce
policy; the oracle receives its signed status. Run the maintained
[two-service qualification](../../docs/two-service-qualification.md).
