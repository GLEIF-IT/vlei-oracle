"""Nonce claim durability and native KERIpy store ownership."""

from concurrent.futures import ThreadPoolExecutor
import json
import multiprocessing
import os
from pathlib import Path
from threading import Barrier

import lmdb
import pytest

from keri.db import dbing

from vlei_oracle.noncing import NonceLedger, NonceClaim


def test_nonce_ledger_retains_only_issuer_scoped_consumption(tmp_path):
    with dbing.openLMDB(cls=NonceLedger, name="issuer", headDirPath=tmp_path, temp=False) as ledger:
        assert ledger.claim(issuer="issuer", nonce=" exact nonce ")
        with ledger.env.begin(db=ledger.consumed.sdb) as txn:
            rows = list(txn.cursor())
        assert len(rows) == 1
        key, value = rows[0]
        assert len(key) == 64
        assert all(byte in b"0123456789abcdef" for byte in key)
        assert json.loads(value) == {"issuer": "issuer", "nonce": " exact nonce "}
        assert ledger.consumed.get(keys=(key.decode(),)) == NonceClaim("issuer", " exact nonce ")


def test_standard_lifecycle_preserves_persistent_and_cleans_temporary_stores(tmp_path):

    with dbing.openLMDB(cls=NonceLedger, name="issuer", base="tenant", headDirPath=tmp_path, temp=False) as ledger:
        path = Path(ledger.path)
        assert path == tmp_path / "vlei-oracle/nonces/tenant/issuer"
        assert ledger.claim(issuer="issuer", nonce="consumed")
        assert ledger.env.flags()["sync"] and ledger.env.flags()["metasync"]
        assert ledger.env.flags()["lock"]
        assert not ledger.consumed.sdb.flags()["dupsort"]
    assert path.is_dir() and (path / "data.mdb").is_file()
    assert ledger.env is None and not ledger.opened
    with dbing.openLMDB(cls=NonceLedger, name="issuer", base="tenant", headDirPath=tmp_path, temp=False) as ledger:
        assert not ledger.claim(issuer="issuer", nonce="consumed")
    with dbing.openLMDB(cls=NonceLedger, name="temporary") as temporary:
        temporary_path = Path(temporary.path)
        assert temporary.temp and temporary.claim(issuer="issuer", nonce="temporary")
    assert not temporary_path.exists()
    assert path.exists()


def test_deferred_open_and_relative_root_stay_on_the_selected_store(tmp_path, monkeypatch):

    monkeypatch.chdir(tmp_path)
    ledger = NonceLedger(name="issuer", headDirPath="selected", reopen=False)
    assert not ledger.opened and ledger.env is None and ledger.path is None
    assert not (tmp_path / "selected").exists()
    with pytest.raises(ValueError, match="not open"):
        ledger.claim(issuer="issuer", nonce="consumed")
    try:
        assert ledger.reopen()
        assert ledger.claim(issuer="issuer", nonce="consumed")
        path = ledger.path
        ledger.close()
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        assert ledger.reopen(headDirPath=None)
        assert ledger.path == path
        assert Path(path) == tmp_path / "selected/vlei-oracle/nonces/issuer"
        assert not ledger.claim(issuer="issuer", nonce="consumed")
    finally:
        ledger.close()


def test_explicit_root_change_does_not_reuse_a_cached_path(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    with dbing.openLMDB(cls=NonceLedger, name="issuer", headDirPath=first, temp=False) as ledger:
        assert ledger.claim(issuer="issuer", nonce="consumed")
        ledger.reopen(headDirPath=second, reuse=True)
        assert Path(ledger.path) == second / "vlei-oracle/nonces/issuer"
        assert ledger.claim(issuer="issuer", nonce="consumed")
        ledger.reopen(headDirPath=first, reuse=True)
        assert not ledger.claim(issuer="issuer", nonce="consumed")


def test_unavailable_selected_root_never_falls_back(tmp_path, monkeypatch):

    blocked = tmp_path / "blocked"
    blocked.write_text("preserve this file")
    alternate = tmp_path / "alternate"
    monkeypatch.setattr(NonceLedger, "AltHeadDirPath", str(alternate))
    with pytest.raises(OSError):
        with dbing.openLMDB(cls=NonceLedger, name="issuer", headDirPath=blocked, temp=False):
            pytest.fail("an unavailable selected root must not open another store")
    assert not alternate.exists()
    assert blocked.read_text() == "preserve this file"


def test_failed_reopen_cannot_claim_against_a_previous_store(tmp_path):

    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")
    with dbing.openLMDB(cls=NonceLedger, name="issuer", headDirPath=tmp_path, temp=False) as ledger:
        assert ledger.claim(issuer="issuer", nonce="consumed")
        with pytest.raises(OSError):
            ledger.reopen(headDirPath=blocked)
        assert ledger.env is None and not ledger.opened
        with pytest.raises(OSError):
            ledger.reopen(reuse=True)
        with pytest.raises(ValueError, match="not open"):
            ledger.claim(issuer="issuer", nonce="consumed")
        ledger.reopen(headDirPath=tmp_path)
        assert not ledger.claim(issuer="issuer", nonce="consumed")


def test_post_open_failure_closes_resources_and_preserves_persistent_claims(tmp_path, monkeypatch):
    from vlei_oracle import noncing

    options = dict(cls=NonceLedger, name="issuer", headDirPath=tmp_path, temp=False)
    with dbing.openLMDB(**options) as ledger:
        assert ledger.claim(issuer="issuer", nonce="consumed")
    acquired = []

    def fail_binding(*, db, **kwa):
        acquired.append((db, db.env, Path(db.path)))
        raise RuntimeError("claim table initialization failed")

    with monkeypatch.context() as patch:
        patch.setattr(noncing.koming, "Komer", fail_binding)
        for temp in (False, True):
            with pytest.raises(RuntimeError, match="claim table initialization failed"):
                with dbing.openLMDB(**{**options, "temp": temp}):
                    pytest.fail("initialization failure must not yield a ledger")
    for db, env, path in acquired:
        assert db.env is None and not db.opened
        with pytest.raises(lmdb.Error):
            env.begin()
        assert path.exists() is (not db.temp)
    with dbing.openLMDB(**options) as ledger:
        assert not ledger.claim(issuer="issuer", nonce="consumed")


def test_shared_threads_serialize_same_and_distinct_claims(tmp_path):

    with dbing.openLMDB(cls=NonceLedger, name="issuer", headDirPath=tmp_path, temp=False) as ledger:
        for shared in (True, False):
            gate = Barrier(16)

            def consume(index):
                gate.wait(timeout=10)
                return ledger.claim(issuer="issuer", nonce=" shared 🔥 " if shared else f"nonce-{index}")

            with ThreadPoolExecutor(max_workers=16) as pool:
                results = list(pool.map(consume, range(64)))
            assert sum(results) == (1 if shared else 64)
        assert ledger.consumed.cntAll() == 65


def consume_in_process(head, gate, outcomes):
    """Each spawned process owns its own native environment for the same store."""

    with dbing.openLMDB(cls=NonceLedger, name="issuer", headDirPath=head, temp=False) as ledger:
        gate.wait(timeout=15)
        won = ledger.claim(issuer="issuer", nonce="shared 🔥")
        scoped = ledger.claim(issuer=str(os.getpid()), nonce="shared 🔥")
        outcomes.put((won, scoped))


def test_independent_processes_share_native_locking_and_issuer_scope(tmp_path):

    context = multiprocessing.get_context("spawn")
    gate, outcomes = context.Barrier(4), context.Queue()
    processes = [context.Process(target=consume_in_process, args=(str(tmp_path), gate, outcomes))
                 for _ in range(4)]
    try:
        for process in processes:
            process.start()
        results = [outcomes.get(timeout=20) for _ in processes]
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
        assert sum(won for won, _ in results) == 1
        assert all(scoped for _, scoped in results)
    finally:
        for process in processes:
            if process.pid is None:
                continue
            if process.is_alive():
                process.terminate()
            process.join(timeout=10)
        outcomes.close()
        outcomes.join_thread()
    with dbing.openLMDB(cls=NonceLedger, name="issuer", headDirPath=tmp_path, temp=False) as ledger:
        assert not ledger.claim(issuer="issuer", nonce="shared 🔥")
        assert ledger.consumed.cntAll() == 5


def test_unambiguous_keys_preserve_exact_strings(tmp_path):
    pairs = [("ab", "c"), ("a", "bc"), ("issuer", " nonce "), ("issuer", "nonce"),
             ("issuer", "é"), ("issuer", "e\u0301"), ("issuer", "🔥" * 256)]
    with dbing.openLMDB(cls=NonceLedger, name="issuer", headDirPath=tmp_path, temp=False) as ledger:
        for issuer, nonce in pairs:
            assert ledger.claim(issuer=issuer, nonce=nonce)
            assert not ledger.claim(issuer=issuer, nonce=nonce)
        assert {(claim.issuer, claim.nonce) for _, claim in ledger.consumed.getItemIter()} == set(pairs)


def test_inconsistent_or_malformed_claims_fail_closed_without_repair(tmp_path):

    invalid = [b'{"issuer":"issuer","nonce":"another nonce"}', b'not JSON', b'null',
               b'{"issuer":"issuer"}', b'{"issuer":"issuer","nonce":7}',
               b'{"issuer":"issuer","nonce":"nonce","extra":true}']
    with dbing.openLMDB(cls=NonceLedger, name="issuer", headDirPath=tmp_path, temp=False) as ledger:
        assert ledger.claim(issuer="issuer", nonce="nonce")
        keys, _ = next(ledger.consumed.getItemIter())
        key = keys[0].encode("ascii")
        for value in invalid:
            with ledger.env.begin(write=True) as txn:
                txn.put(key, value, db=ledger.consumed.sdb)
            with pytest.raises(ValueError):
                ledger.claim(issuer="issuer", nonce="nonce")
            with ledger.env.begin() as txn:
                assert txn.get(key, db=ledger.consumed.sdb) == value


def test_failed_temporary_reopen_never_clears_previous_persistent_consumption(tmp_path, monkeypatch):
    blocked = tmp_path / "blocked"
    blocked.write_text("not a temporary directory")
    options = dict(cls=NonceLedger, name="issuer", headDirPath=tmp_path, temp=False)
    with dbing.openLMDB(**options) as ledger:
        assert ledger.claim(issuer="issuer", nonce="consumed")
        path = Path(ledger.path)
        with monkeypatch.context() as patch:
            patch.setattr(NonceLedger, "TempHeadDir", str(blocked))
            with pytest.raises(OSError):
                ledger.reopen(temp=True)
        assert not ledger.temp and ledger.env is None and not ledger.opened
        assert (path / "data.mdb").exists()
    assert (path / "data.mdb").exists()
    with dbing.openLMDB(**options) as ledger:
        assert not ledger.claim(issuer="issuer", nonce="consumed")


def test_temporary_reopen_never_reuses_a_previous_persistent_path(tmp_path):
    options = dict(cls=NonceLedger, name="issuer", headDirPath=tmp_path, temp=False)
    with dbing.openLMDB(**options) as ledger:
        assert ledger.claim(issuer="issuer", nonce="consumed")
        persistent = Path(ledger.path)
        ledger.reopen(temp=True, reuse=True)
        temporary = Path(ledger.path)
        assert ledger.temp and temporary != persistent
        assert ledger.claim(issuer="issuer", nonce="consumed")
    assert not temporary.exists()
    assert (persistent / "data.mdb").exists()
    with dbing.openLMDB(**options) as ledger:
        assert not ledger.claim(issuer="issuer", nonce="consumed")
