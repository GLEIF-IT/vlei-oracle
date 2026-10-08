"""The fresh application store contains obligations, not evidence snapshots."""

import json
import sqlite3

from ceremony import consume_nonce
from vlei_oracle.basing import OracleBaser, PostingScope, TransactionRecord


def test_serialized_records_contain_only_current_recovery_fields(tmp_path):
    store = OracleBaser(name="persistence", headDirPath=str(tmp_path))
    try:
        work = store.start_verification(association_said="association", grant_said="grant")
        store.bind_posting_scope(PostingScope(31337, "writer", "endorsement", "identity"))
        expected = {
            store.verifications: {"associationSaid", "grantSaid", "state", "failureReason"},
            store.scope: {"chainId", "writer", "endorsement", "identity"},
        }
        for table, fields in expected.items():
            key = b"association" if table is store.verifications else b"posting"
            assert set(json.loads(bytes(store.getVal(table.sdb, key)))) == fields
        store.complete_verification(work)
        assert store.get_verification("association") is None
        member = store.get_managed("association")
        member.state = "withdrawn"
        store.pin_managed(member)
        intent = store.pin_transaction(TransactionRecord(
            txHash="hash", rawTransaction="raw", nonce=0, gasPrice=1,
            associationSaid="association", action="withdrawEndorsement",
        ))
        intent.cancelHash, intent.cancelRaw = "cancel", "cancel-raw"
        store.pin_transaction(intent)
        store.complete_transaction(intent, intent.cancelHash, True)
        store.close()
        store.reopen()
        assert set(json.loads(bytes(store.getVal(store.managed.sdb, b"association")))) == {
            "associationSaid", "state", "failureReason",
        }
        saved = json.loads(bytes(store.getVal(store.transactions.sdb, b"hash")))
        assert set(saved) == {
            "txHash", "rawTransaction", "nonce", "gasPrice", "associationSaid",
            "action", "state", "cancelHash", "cancelRaw", "failureReason",
        }
        assert saved["state"] == "cancelled"
        assert store.get_managed("association").state == "withdrawn"
        assert store.start_verification(association_said="association", grant_said="reordered") is None
        status = store.presentation_status("association")
        assert status["transactions"] == store.transaction_status("association")
        assert not {"verified", "txHashes", "createdAt", "updatedAt", "retryCount"}.intersection(status)
    finally:
        store.close(clear=True)


def test_nonce_ledger_retains_only_issuer_scoped_consumption(tmp_path):
    ledger = tmp_path / "nonces.db"
    consume_nonce(ledger, issuer="issuer", nonce=" exact nonce ")
    with sqlite3.connect(ledger) as connection:
        assert [column[1] for column in connection.execute("PRAGMA table_info(consumed)")] == ["issuer", "nonce"]
        assert connection.execute("SELECT * FROM consumed").fetchall() == [("issuer", " exact nonce ")]
