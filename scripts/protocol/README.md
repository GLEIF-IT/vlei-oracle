# Protocol vectors and qualification dependencies

Generate reference vectors with generate_vectors.py and the installed producer.
requirements.txt pins protocol dependencies; install the producer from
tests/requirements.txt as well.

Test-owned witnessed issuance and ACDC assembly live in tests/support/issuers.py;
contract construction/RPC is in tests/support/contracts.py; processes and signed
status have their own support modules. They are outside the oracle wheel. There
is no issuer ceremony or association nonce store in the oracle.

Run python -m pytest tests/qualification/test_two_services.py from the oracle
root with the qualified installed applications, Foundry and comply checkout.
See [testing](../../docs/testing.md) and
[two-service qualification](../../docs/two-service-qualification.md).
