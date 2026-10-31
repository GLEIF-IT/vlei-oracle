# Association ceremony and paired qualification helpers

These tools prepare the current EOA association profile. They do not compose a
credential verifier inside the oracle.

- [ceremony.py](ceremony.py) validates the pre-issuance wallet proof, consumes the
  issuer nonce durably in LMDB, and writes a full association ACDC for KLI.
- [generate_vectors.py](generate_vectors.py) reproduces known-answer profile
  vectors from an existing association ACDC.
- [issuance_helpers.py](issuance_helpers.py) creates witnessed test issuers and
  their native KERI evidence for the independent verifier.
- [contract_fixture.py](contract_fixture.py) encodes test contract calls and
  performs them through HIO on the test-owned chain.
- [requirements.txt](requirements.txt) pins qualification dependencies.

Follow the root [ceremony instructions](../../README.md#ceremony) for issuance.
Each issuer owns one persistent nonce directory across ceremonies. Consumption
commits before credential bytes are exposed; a consumed nonce stays consumed
through restart, concurrency and later output failure.

For the maintained end-to-end scenario, follow the
[two-service qualification](../../docs/two-service-qualification.md). It runs
installed verifier and oracle CLI processes with separate stores, a real witness,
signed webhook delivery and deployed contracts. No combined oracle verifier or
alternate issuance exercise is supported.
