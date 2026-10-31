"""runtime/test_serving.py."""

import pytest
from falcon import testing

from vlei_oracle.runtime.serving import run_receiver


def test_receiver_reopens_identity_and_keripy_stores(peers, tmp_path, capsys):
    """The real entrypoint preserves its AID and closes its listener across restarts."""
    from dataclasses import replace

    config = replace(
        peers.config, head_dir=str(tmp_path / "service"), name="persistent-inbox"
    )
    with pytest.raises(FileNotFoundError):
        run_receiver(replace(config, verifier_kel=str(tmp_path / "missing.cesr")))
    run_receiver(config, expire=0.03)
    first = capsys.readouterr().out
    run_receiver(config, expire=0.03)
    assert capsys.readouterr().out == first


@pytest.mark.parametrize(
    "method,path", [("POST", "/"), ("GET", "/associations/credential")]
)
def test_receiver_has_no_credential_intake_or_verification_api(peers, method, path):
    response = testing.TestClient(peers.receiver.app).simulate_request(
        path=path, method=method
    )
    assert response.status_code == 404
