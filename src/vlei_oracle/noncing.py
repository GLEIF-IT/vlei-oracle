"""Issuer-owned, durable consumption of exact ceremony nonces."""

from dataclasses import dataclass
import hashlib
import os

from keri.db import dbing, koming


@dataclass(frozen=True)
class NonceClaim:
    """The exact pair whose consumption is irreversible."""

    issuer: str
    nonce: str


class NonceLedger(dbing.LMDBer):
    """KERIpy-owned LMDB lifecycle with atomic, issuer-scoped nonce claims.

    Owners use openLMDB; ceremony functions borrow the open ledger. LMDB owns
    transaction locking; overlapping writes may raise its native binding error.
    Persistent closure preserves consumption. No expiry or release is supported.
    """

    TailDirPath = "vlei-oracle/nonces"
    AltTailDirPath = TailDirPath
    CleanTailDirPath = "vlei-oracle/clean/nonces"
    AltCleanTailDirPath = CleanTailDirPath
    TempPrefix = "vlei_oracle_nonces_"

    def __init__(self, *, name="issuer-nonces", base="", headDirPath=None,
                 temp=False, reopen=True, **kwa):
        self.consumed: koming.Komer | None = None
        # Freeze relative roots now, rather than selecting another store if cwd changes.
        head = self.HeadDirPath if headDirPath is None else headDirPath
        head = os.path.abspath(os.path.expanduser(head))
        super().__init__(name=name, base=base, headDirPath=head,
                         temp=temp, reopen=reopen, **kwa)

    def reopen(self, **kwa):
        """Open through LMDBer, then bind the non-duplicate typed claim table."""
        head = kwa.get("headDirPath")
        kwa["headDirPath"] = (self.headDirPath if head is None else
                              os.path.abspath(os.path.expanduser(head)))
        # Root/mode changes must not reuse Filer's previous path: a persistent
        # path reused as temporary would be deleted by its owning context.
        temp = kwa.get("temp")
        if (kwa["headDirPath"] != self.headDirPath or
                (temp is not None and bool(temp) != self.temp)):
            kwa["reuse"] = False
        # Pinned Filer retries here on primary-path failure. Keep that retry on
        # the selected root and tail; it must never open another nonce namespace.
        self.AltHeadDirPath = kwa["headDirPath"]
        self.consumed = None
        prior_path, prior_temp = self.path, self.temp
        try:
            super().reopen(**kwa)
            self.consumed = koming.Komer(db=self, subkey="consumed.", schema=NonceClaim)
        except BaseException:
            # Filer changes temp before remaking path. A failed remake must not
            # turn cleanup of the previous persistent path into temporary cleanup.
            if self.path == prior_path:
                self.temp = prior_temp
            self.close(clear=self.temp)
            # A failed remake can leave Filer's cached path pointing at the old
            # store even though its root changed. It must not be reused on retry.
            self.path = None
            raise
        return self.opened

    def claim(self, *, issuer: str, nonce: str) -> bool:
        """Commit one exact claim, reject its duplicate, and fail closed on conflict."""
        if not isinstance(issuer, str) or not isinstance(nonce, str):
            raise TypeError("issuer and nonce must be strings")
        if not self.opened or self.env is None or self.consumed is None:
            raise ValueError("nonce ledger is not open")
        claim = NonceClaim(issuer=issuer, nonce=nonce)
        value = self.consumed.serializer(claim)
        key = hashlib.sha256(value).hexdigest().encode("ascii")
        with self.env.begin(write=True) as txn:
            if txn.put(key, value, db=self.consumed.sdb, overwrite=False):
                return True
            if self.consumed.deserializer(txn.get(key, db=self.consumed.sdb)) != claim:
                raise ValueError("nonce ledger key conflicts with stored consumption")
            return False
