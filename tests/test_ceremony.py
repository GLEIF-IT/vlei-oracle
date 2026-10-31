"""Supported pre-issuance ceremony and durable nonce consumption."""

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest
from ceremony import write_credential
from eth_account import Account
from eth_account.messages import encode_typed_data
from keri.db import dbing

from vlei_oracle.cli import main
from vlei_oracle.noncing import NonceLedger
from vlei_oracle.association.eoa import build_challenge, verify_eoa_proof
from vlei_oracle.association.schemas import association_schemer

ROOT = Path(__file__).resolve().parents[1]
ASSOCIATION = json.loads(
    (ROOT / "protocol/external-association-v1/eoa-v1/association.json").read_text()
)
KEY = "0x" + "0" * 63 + "1"


def ceremony(nonce=" exact nonce "):
    challenge = build_challenge(
        issuer_aid=ASSOCIATION["i"],
        le_vlei_said=ASSOCIATION["e"]["le"]["n"],
        ext_id=ASSOCIATION["a"]["extId"],
        nonce=nonce,
    )
    signature = (
        "0x"
        + Account.sign_message(
            encode_typed_data(full_message=challenge), KEY
        ).signature.hex()
    )
    return challenge, signature


@pytest.fixture
def nonce_ledger(tmp_path):
    with dbing.openLMDB(cls=NonceLedger, name="issuer", headDirPath=tmp_path, temp=False) as ledger:
        yield ledger


def command(tmp_path, nonce_head, output):
    challenge, signature = ceremony()
    (tmp_path / "challenge.json").write_text(json.dumps(challenge))
    (tmp_path / "signature.txt").write_text(signature)
    return [sys.executable, str(ROOT / "scripts/protocol/ceremony.py"),
            "--challenge", str(tmp_path / "challenge.json"),
            "--signature", str(tmp_path / "signature.txt"),
            "--registry", ASSOCIATION["ri"], "--nonce-head-dir", str(nonce_head), "--nonce-name", "issuer",
            "--out", str(output)]


def test_cli_challenge_and_signature_finalize_before_issuance(tmp_path, nonce_ledger):
    challenge_file, signature_file = (
        tmp_path / "challenge.json",
        tmp_path / "signature.txt",
    )
    assert (
        main(
            [
                "challenge",
                "--issuer-aid",
                ASSOCIATION["i"],
                "--le-vlei-said",
                ASSOCIATION["e"]["le"]["n"],
                "--wallet",
                Account.from_key(KEY).address,
                "--chain-id",
                "1",
                "--nonce",
                " exact nonce ",
                "--out",
                str(challenge_file),
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "client",
                "sign",
                "--challenge",
                str(challenge_file),
                "--private-key",
                KEY,
                "--out",
                str(signature_file),
            ]
        )
        == 0
    )
    creder = write_credential(
        challenge=json.loads(challenge_file.read_text()),
        signature=signature_file.read_text().strip(),
        registry=ASSOCIATION["ri"],
        nonce_ledger=nonce_ledger,
        output=tmp_path / "association.json",
    )
    assert creder.sad["u"] == " exact nonce "
    assert "i" not in creder.attrib
    association_schemer().verify(creder.raw)
    verify_eoa_proof(creder.sad)


def test_consumed_nonce_survives_reopen(tmp_path):
    with dbing.openLMDB(cls=NonceLedger, name="issuer", headDirPath=tmp_path, temp=False) as ledger:
        assert ledger.claim(issuer="issuer", nonce=" exact nonce ")
    with dbing.openLMDB(cls=NonceLedger, name="issuer", headDirPath=tmp_path, temp=False) as ledger:
        assert not ledger.claim(issuer="issuer", nonce=" exact nonce ")
        assert ledger.claim(issuer="another issuer", nonce=" exact nonce ")


def test_consumed_nonce_survives_a_new_process(tmp_path):
    nonce_dir = tmp_path / "nonces"
    first = subprocess.run(command(tmp_path, nonce_dir, tmp_path / "first.json"),
                           capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    second = subprocess.run(command(tmp_path, nonce_dir, tmp_path / "second.json"),
                            capture_output=True, text=True)
    assert second.returncode == 1 and "already consumed" in second.stderr
    assert not (tmp_path / "second.json").exists()


def test_concurrent_processes_publish_one_artifact(tmp_path):
    commands = [command(tmp_path, tmp_path / "nonces", tmp_path / f"{i}.json")
                for i in (1, 2)]
    processes = [subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True) for args in commands]
    try:
        results = [process.communicate(timeout=15) for process in processes]
        assert sorted(process.returncode for process in processes) == [0, 1], results
        loser = next(i for i, process in enumerate(processes) if process.returncode == 1)
        assert "already consumed" in results[loser][1]
        assert sum((tmp_path / f"{i}.json").exists() for i in (1, 2)) == 1
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait()


@pytest.mark.parametrize("attempt", range(10))
def test_concurrent_nonce_consumption_allows_one_artifact(tmp_path, nonce_ledger, attempt):
    challenge, signature = ceremony()

    def finish(index):
        try:
            write_credential(challenge=challenge, signature=signature,
                             registry=ASSOCIATION["ri"], nonce_ledger=nonce_ledger,
                             output=tmp_path / f"{index}.json")
            return True
        except ValueError as error:
            assert str(error) == "nonce already consumed by this issuer"
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(finish, [1, 2])) == [False, True]
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_invalid_signature_does_not_consume_nonce(tmp_path, nonce_ledger):
    challenge, signature = ceremony()
    invalid = "0x" + Account.sign_message(
        encode_typed_data(full_message=challenge), "0x" + "0" * 63 + "2"
    ).signature.hex()
    args = dict(challenge=challenge, registry=ASSOCIATION["ri"],
                nonce_ledger=nonce_ledger, output=tmp_path / "association.json")
    with pytest.raises(ValueError, match="signer does not match"):
        write_credential(signature=invalid, **args)
    assert not args["output"].exists()
    write_credential(signature=signature, **args)


def test_existing_output_does_not_consume_nonce(tmp_path, nonce_ledger):
    challenge, signature = ceremony()
    output = tmp_path / "association.json"
    output.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        write_credential(challenge=challenge, signature=signature,
                         registry=ASSOCIATION["ri"], nonce_ledger=nonce_ledger, output=output)
    assert output.read_bytes() == b"existing"
    assert nonce_ledger.claim(issuer=challenge["message"]["issuerAid"],
                              nonce=challenge["message"]["nonce"])


def test_artifact_write_failure_keeps_nonce_consumed(tmp_path, nonce_ledger, monkeypatch):
    challenge, signature = ceremony()
    output, original_open = tmp_path / "association.json", Path.open

    def fail_write(data):
        raise OSError("artifact write failed")

    @contextmanager
    def failing_open(path, *args, **kwargs):
        with original_open(path, *args, **kwargs) as stream:
            if path == output:
                from types import SimpleNamespace
                yield SimpleNamespace(write=fail_write, flush=stream.flush)
            else:
                yield stream

    monkeypatch.setattr(Path, "open", failing_open)
    with pytest.raises(OSError, match="artifact write failed"):
        write_credential(challenge=challenge, signature=signature,
                         registry=ASSOCIATION["ri"], nonce_ledger=nonce_ledger, output=output)
    assert not output.exists()
    assert not nonce_ledger.claim(issuer=challenge["message"]["issuerAid"],
                                  nonce=challenge["message"]["nonce"])


def test_same_nonce_cannot_finalize_a_different_wallet(tmp_path, nonce_ledger):
    challenge, signature = ceremony()
    args = dict(registry=ASSOCIATION["ri"], nonce_ledger=nonce_ledger)
    write_credential(challenge=challenge, signature=signature,
                     output=tmp_path / "first.json", **args)
    other_key = "0x" + "0" * 63 + "2"
    other = build_challenge(issuer_aid=ASSOCIATION["i"],
                            le_vlei_said=ASSOCIATION["e"]["le"]["n"],
                            ext_id="eip155:1:" + Account.from_key(other_key).address.lower(),
                            nonce=challenge["message"]["nonce"])
    other_signature = "0x" + Account.sign_message(
        encode_typed_data(full_message=other), other_key
    ).signature.hex()
    with pytest.raises(ValueError, match="already consumed"):
        write_credential(challenge=other, signature=other_signature,
                         output=tmp_path / "second.json", **args)
    assert not (tmp_path / "second.json").exists()


@pytest.mark.parametrize("nonces", [(" nonce ", "nonce"), ("é", "e\u0301"), ("🔥" * 256,)])
def test_nonce_is_never_normalized(tmp_path, nonce_ledger, nonces):
    saids = []
    for index, nonce in enumerate(nonces):
        challenge, signature = ceremony(nonce)
        result = write_credential(challenge=challenge, signature=signature,
                                  registry=ASSOCIATION["ri"], nonce_ledger=nonce_ledger,
                                  output=tmp_path / f"{index}.json")
        assert result.sad["u"] == nonce
        saids.append(result.said)
    assert len(set(saids)) == len(nonces)


def test_cli_selects_the_standard_persistent_namespace(tmp_path):
    head = tmp_path / "selected"
    args = command(tmp_path, head, tmp_path / "association.json") + ["--nonce-base", "tenant"]
    result = subprocess.run(args, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (head / "vlei-oracle/nonces/tenant/issuer/data.mdb").is_file()
    with dbing.openLMDB(cls=NonceLedger, name="issuer", base="tenant", headDirPath=head, temp=False) as ledger:
        assert not ledger.claim(issuer=ASSOCIATION["i"], nonce=" exact nonce ")


@pytest.mark.parametrize("extra,code,reason", [
    (["--nonce-dir", "retired"], 2, "unrecognized arguments"),
    (["--nonce-name", "/not-relative"], 1, "Not relative"),
])
def test_cli_has_no_path_alias_or_implicit_store_selection(tmp_path, extra, code, reason):
    head, output = tmp_path / "selected", tmp_path / "association.json"
    result = subprocess.run(command(tmp_path, head, output) + extra, capture_output=True, text=True)
    assert result.returncode == code and reason in result.stderr
    assert "Traceback" not in result.stderr
    assert not head.exists() and not output.exists()
