from __future__ import annotations

import unittest

from keri.app import habbing

from vlei_oracle.constants import WALLET_SCHEMA
from vlei_oracle.schema_cache import ACCEPTED_SCHEMA_SAIDS, pin_accepted_schemas, wallet_schemer
from vlei_oracle.serving import ServiceConfig, setup_service


class SchemaCacheTest(unittest.TestCase):
    def test_wallet_schemer_matches_pinned_schema_said(self) -> None:
        schemer = wallet_schemer()

        self.assertEqual(schemer.said, WALLET_SCHEMA)
        self.assertEqual(ACCEPTED_SCHEMA_SAIDS, (WALLET_SCHEMA,))

    def test_pin_accepted_schemas_writes_habery_schema_cache(self) -> None:
        hby = habbing.Habery(name="test-vlei-oracle-schema-cache", temp=True)
        try:
            pin_accepted_schemas(hby)

            schemer = hby.db.schema.get(keys=(WALLET_SCHEMA,))
            self.assertIsNotNone(schemer)
            self.assertEqual(schemer.said, WALLET_SCHEMA)
        finally:
            hby.close(clear=True)

    def test_setup_service_pins_wallet_schema(self) -> None:
        runtime = setup_service(ServiceConfig(name="test-vlei-oracle-service-schema", temp=True, no_post=True))
        try:
            schemer = runtime.hby.db.schema.get(keys=(WALLET_SCHEMA,))
            self.assertIsNotNone(schemer)
            self.assertEqual(schemer.said, WALLET_SCHEMA)
        finally:
            runtime.baser.close(clear=True)
            runtime.rgy.reger.close(clear=True)
            runtime.hby.close(clear=True)


if __name__ == "__main__":
    unittest.main()
