"""Cryptographic random number generator for obfuscation passes."""

import random
import secrets


class CryptoRandom:
    """Random number generator for obfuscation passes.

    Uses `secrets` for production (cryptographically secure),
    `random.Random(seed)` for deterministic testing.
    """

    def __init__(self, seed: int | None = None):
        self._seeded = seed is not None
        if self._seeded:
            self._rng = random.Random(seed)
        else:
            self._rng = None

    def get_uint32(self) -> int:
        if self._seeded:
            return self._rng.getrandbits(32)
        return secrets.randbits(32)

    def get_uint64(self) -> int:
        if self._seeded:
            return self._rng.getrandbits(64)
        return secrets.randbits(64)

    def get_range(self, max_val: int) -> int:
        """Return a random integer in [0, max_val)."""
        if max_val <= 0:
            return 0
        if self._seeded:
            return self._rng.randrange(max_val)
        return secrets.randbelow(max_val)

    def get_bool(self) -> bool:
        return self.get_range(2) == 1
