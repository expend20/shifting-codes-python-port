"""Pure Python XTEA reference implementation for testing."""

DELTA = 0x9E3779B9
MASK = 0xFFFFFFFF


def xtea_encrypt(v0: int, v1: int, key: list[int], rounds: int = 32) -> tuple[int, int]:
    """Encrypt a 64-bit block using XTEA.

    Args:
        v0, v1: Two 32-bit halves of the plaintext block.
        key: List of 4 32-bit key words.
        rounds: Number of Feistel rounds (default 32).

    Returns:
        Tuple of two 32-bit ciphertext halves.
    """
    sum_val = 0
    for _ in range(rounds):
        v0 = (v0 + ((((v1 << 4) ^ (v1 >> 5)) + v1) ^ (sum_val + key[sum_val & 3]))) & MASK
        sum_val = (sum_val + DELTA) & MASK
        v1 = (v1 + ((((v0 << 4) ^ (v0 >> 5)) + v0) ^ (sum_val + key[(sum_val >> 11) & 3]))) & MASK
    return v0, v1


def xtea_decrypt(v0: int, v1: int, key: list[int], rounds: int = 32) -> tuple[int, int]:
    """Decrypt a 64-bit block using XTEA.

    Args:
        v0, v1: Two 32-bit halves of the ciphertext block.
        key: List of 4 32-bit key words.
        rounds: Number of Feistel rounds (default 32).

    Returns:
        Tuple of two 32-bit plaintext halves.
    """
    sum_val = (DELTA * rounds) & MASK
    for _ in range(rounds):
        v1 = (v1 - ((((v0 << 4) ^ (v0 >> 5)) + v0) ^ (sum_val + key[(sum_val >> 11) & 3]))) & MASK
        sum_val = (sum_val - DELTA) & MASK
        v0 = (v0 - ((((v1 << 4) ^ (v1 >> 5)) + v1) ^ (sum_val + key[sum_val & 3]))) & MASK
    return v0, v1
