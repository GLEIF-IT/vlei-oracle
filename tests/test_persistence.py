"""The fresh application store contains obligations, not evidence snapshots."""

import json

from vlei_oracle.noncing import NonceLedger


def test_nonce_ledger_retains_only_issuer_scoped_consumption(tmp_path):
    with NonceLedger(tmp_path / "nonces") as ledger:
        assert ledger.claim(issuer="issuer", nonce=" exact nonce ")
        with ledger.env.begin(db=ledger.consumed) as txn:
            rows = list(txn.cursor())
        assert len(rows) == 1
        key, value = rows[0]
        assert len(key) == 32
        assert json.loads(value) == {"issuer": "issuer", "nonce": " exact nonce "}
