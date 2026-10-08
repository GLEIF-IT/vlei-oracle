"""Supported pre-issuance ceremony and durable nonce consumption."""

import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from ceremony import write_credential
from eth_account import Account
from eth_account.messages import encode_typed_data

from vlei_oracle.cli import main
from vlei_oracle.verification.eoa import build_challenge, verify_eoa_proof
from vlei_oracle.verification.schema_cache import association_schemer

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


def test_cli_challenge_and_signature_finalize_before_issuance(tmp_path):
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
        nonce_store=tmp_path / "nonces.db",
        output=tmp_path / "association.json",
    )
    assert creder.sad["u"] == " exact nonce "
    assert "i" not in creder.attrib
    association_schemer().verify(creder.raw)
    verify_eoa_proof(creder.sad)


def test_consumed_nonce_survives_a_new_process(tmp_path):
    challenge, signature = ceremony()
    args = {
        "challenge": challenge,
        "signature": signature,
        "registry": ASSOCIATION["ri"],
        "nonce_store": tmp_path / "nonces.db",
        "output": tmp_path / "first.json",
    }
    write_credential(**args)
    (tmp_path / "challenge.json").write_text(json.dumps(challenge))
    (tmp_path / "signature.txt").write_text(signature)
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/protocol/ceremony.py"),
            "--challenge",
            str(tmp_path / "challenge.json"),
            "--signature",
            str(tmp_path / "signature.txt"),
            "--registry",
            ASSOCIATION["ri"],
            "--nonce-store",
            str(tmp_path / "nonces.db"),
            "--out",
            str(tmp_path / "second.json"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "already consumed" in result.stderr
    assert not (tmp_path / "second.json").exists()


@pytest.mark.parametrize("attempt", range(10))
def test_concurrent_nonce_consumption_allows_one_artifact(tmp_path, attempt):
    challenge, signature = ceremony()

    def finish(index):
        try:
            write_credential(
                challenge=challenge,
                signature=signature,
                registry=ASSOCIATION["ri"],
                nonce_store=tmp_path / "nonces.db",
                output=tmp_path / f"{index}.json",
            )
            return True
        except (ValueError, sqlite3.OperationalError):
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(finish, [1, 2])) == [False, True]
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_invalid_signature_does_not_consume_nonce(tmp_path):
    challenge, signature = ceremony()
    invalid = (
        "0x"
        + Account.sign_message(
            encode_typed_data(full_message=challenge), "0x" + "0" * 63 + "2"
        ).signature.hex()
    )
    args = {
        "challenge": challenge,
        "registry": ASSOCIATION["ri"],
        "nonce_store": tmp_path / "nonces.db",
        "output": tmp_path / "association.json",
    }
    with pytest.raises(ValueError, match="signer does not match"):
        write_credential(signature=invalid, **args)
    assert not args["nonce_store"].exists()
    write_credential(signature=signature, **args)


def test_same_nonce_cannot_finalize_a_different_wallet(tmp_path):
    challenge, signature = ceremony()
    store = tmp_path / "nonces.db"
    write_credential(
        challenge=challenge,
        signature=signature,
        registry=ASSOCIATION["ri"],
        nonce_store=store,
        output=tmp_path / "first.json",
    )
    other_key = "0x" + "0" * 63 + "2"
    other = build_challenge(
        issuer_aid=ASSOCIATION["i"],
        le_vlei_said=ASSOCIATION["e"]["le"]["n"],
        ext_id="eip155:1:" + Account.from_key(other_key).address.lower(),
        nonce=challenge["message"]["nonce"],
    )
    other_signature = (
        "0x"
        + Account.sign_message(
            encode_typed_data(full_message=other), other_key
        ).signature.hex()
    )
    with pytest.raises(ValueError, match="already consumed"):
        write_credential(
            challenge=other,
            signature=other_signature,
            registry=ASSOCIATION["ri"],
            nonce_store=store,
            output=tmp_path / "second.json",
        )
    assert not (tmp_path / "second.json").exists()


def test_nonce_is_never_normalized(tmp_path):
    saids = []
    for index, nonce in enumerate((" nonce ", "nonce")):
        challenge, signature = ceremony(nonce)
        result = write_credential(
            challenge=challenge,
            signature=signature,
            registry=ASSOCIATION["ri"],
            nonce_store=tmp_path / "nonces.db",
            output=tmp_path / f"{index}.json",
        )
        assert result.sad["u"] == nonce
        saids.append(result.said)
    assert saids[0] != saids[1]
