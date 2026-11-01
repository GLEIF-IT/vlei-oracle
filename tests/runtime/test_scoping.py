"""Persistent deployment-scope validation at the application startup boundary."""

import pytest

from vlei_oracle.persistence.basing import OracleBaser
from vlei_oracle.persistence.reading import OracleReader
from vlei_oracle.persistence.records import PostingScope
from vlei_oracle.persistence.writing import OracleWriter
from vlei_oracle.runtime.scoping import ScopeService

WALLET = "0x" + "11" * 20


@pytest.mark.parametrize("field", ["chainId", "writer", "endorsement", "identity"])
def test_operation_store_refuses_different_deployment_scope(tmp_path, field):
    store = OracleBaser(name="scope", headDirPath=str(tmp_path))
    scope = PostingScope(31337, WALLET.lower(), WALLET.lower(), WALLET.lower())
    ScopeService(OracleReader(store), OracleWriter(store)).bind_posting(scope)
    store.close()
    reopened = OracleBaser(name="scope", headDirPath=str(tmp_path))
    try:
        ScopeService(OracleReader(reopened), OracleWriter(reopened)).bind_posting(scope)
        with pytest.raises(ValueError, match="posting scope"):
            from dataclasses import replace

            ScopeService(OracleReader(reopened), OracleWriter(reopened)).bind_posting(
                replace(
                    scope, **{field: 31338 if field == "chainId" else "0x" + "ab" * 20}
                )
            )
    finally:
        reopened.close(clear=True)
