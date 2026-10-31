"""Issuer-owned, durable consumption of exact ceremony nonces."""

import hashlib
import json
from pathlib import Path
from threading import Lock

import lmdb


class NonceLedger:
    """Own one LMDB environment; borrowers share it for this process's lifetime.

    Other processes open their own environments. Claims commit before returning;
    closing never removes persistent consumption. No expiry or release is supported.
    """

    def __init__(self, path):
        self._writer = Lock()
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.env = lmdb.open(str(self.path), map_size=100 * 1024 * 1024,
                             max_dbs=1, mode=0o600)
        try:
            self.consumed = self.env.open_db(b"consumed")
        except BaseException:
            self.env.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.env.close()

    def claim(self, *, issuer: str, nonce: str) -> bool:
        """Commit the first exact issuer/nonce claim; return False for a duplicate.

        Hash an unambiguous encoding to fit LMDB keys even for long Unicode nonces.
        Retain the exact pair and fail closed on any inconsistent existing value.
        LMDB's default locking and synchronous commit provide serialization/durability.
        """
        value = json.dumps({"issuer": issuer, "nonce": nonce}, ensure_ascii=False,
                           separators=(",", ":"), sort_keys=True).encode("utf-8")
        key = hashlib.sha256(value).digest()
        # Serialize threads before entering the binding's single active write slot.
        # Independent processes coordinate through LMDB's native writer lock.
        with self._writer, self.env.begin(write=True, db=self.consumed) as txn:
            if txn.put(key, value, overwrite=False):
                return True
            if txn.get(key) != value:
                raise ValueError("nonce ledger key conflicts with stored consumption")
            return False
