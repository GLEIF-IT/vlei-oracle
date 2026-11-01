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


def test_changed_startup_settings_warn_without_blocking_service(peers, caplog):
    """Runtime logs both changes and composes posting without logging the private key."""
    from dataclasses import replace

    from hio.base import doing

    from vlei_oracle.persistence.records import PostingScope
    from vlei_oracle.runtime.serving import StatusReceiver

    peers.inbox.scope.pin(
        keys=("posting",),
        val=PostingScope(31337, "0x" + "ab" * 20, "0x" + "cd" * 20, "0x" + "ef" * 20),
    )
    config = replace(
        peers.config,
        chain_id=31338,
        http_port=0,
        rpc_url="http://127.0.0.1:1",
        posting_key="0x" + "0" * 63 + "1",
        endorsement_registry="0x" + "11" * 20,
        identity_registry="0x" + "22" * 20,
    )
    service = StatusReceiver(config, hby=peers.oracle, baser=peers.inbox)
    scheduler = doing.Doist(doers=[service])
    try:
        scheduler.enter()
        assert (
            testing.TestClient(service.app)
            .simulate_get(f"/oobi/{service.hab.pre}")
            .status_code
            == 200
        )
    finally:
        scheduler.exit()
    assert "Oracle inbox settings changed" in caplog.text
    assert "Oracle posting settings changed" in caplog.text
    assert config.posting_key not in caplog.text
