from __future__ import annotations

import unittest

from vlei_oracle.posting import keccak_text


class PostingTest(unittest.TestCase):
    def test_keccak_text_is_abi_hex_string(self) -> None:
        value = keccak_text("EAID")

        self.assertTrue(value.startswith("0x"))
        self.assertEqual(len(value), 66)


if __name__ == "__main__":
    unittest.main()
