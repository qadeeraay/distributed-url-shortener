import unittest
from app.core.base62 import encode, decode, validate_custom_alias


class TestBase62(unittest.TestCase):
    def test_base62_encoding_decoding_roundtrip(self):
        """Verifies bijective fidelity: decode(encode(n)) == n."""
        test_numbers = [
            0, 1, 61, 62, 63, 100, 999, 14_776_336, 100_000_000, 2**31 - 1, 2**63 - 1
        ]
        for num in test_numbers:
            encoded = encode(num)
            decoded = decode(encoded)
            self.assertEqual(decoded, num, f"Roundtrip failed for {num}: got {decoded}")

    def test_base62_zero_collision(self):
        """
        Simulates high-throughput monotonic sequence generation.
        Mathematically verifies 0 collisions across 10,000 generated keys.
        """
        start_offset = 14_776_336
        sample_size = 10_000
        generated_keys = set()

        for seq_id in range(start_offset, start_offset + sample_size):
            code = encode(seq_id)
            self.assertNotIn(code, generated_keys, f"Collision detected for code {code} at seq {seq_id}")
            generated_keys.add(code)

        self.assertEqual(len(generated_keys), sample_size)

    def test_base62_invalid_inputs(self):
        """Verifies boundary errors on negative numbers or empty strings."""
        with self.assertRaises(ValueError):
            encode(-5)

        with self.assertRaises(ValueError):
            decode("")

        with self.assertRaises(ValueError):
            decode("invalid@char!")

    def test_custom_alias_validation(self):
        """Tests custom slug validation, bounds, and reserved keywords."""
        # Valid aliases
        self.assertTrue(validate_custom_alias("my-cool-link"))
        self.assertTrue(validate_custom_alias("summer_sale_2026"))
        self.assertTrue(validate_custom_alias("DevRel101"))

        # Too short (< 4 chars)
        self.assertFalse(validate_custom_alias("abc"))

        # Too long (> 32 chars)
        self.assertFalse(validate_custom_alias("a" * 33))

        # Invalid characters
        self.assertFalse(validate_custom_alias("my link!"))
        self.assertFalse(validate_custom_alias("slash/test"))
        self.assertFalse(validate_custom_alias("dots.test"))

        # Reserved routes
        self.assertFalse(validate_custom_alias("api"))
        self.assertFalse(validate_custom_alias("metrics"))
        self.assertFalse(validate_custom_alias("healthz"))
        self.assertFalse(validate_custom_alias("docs"))


if __name__ == "__main__":
    unittest.main()
