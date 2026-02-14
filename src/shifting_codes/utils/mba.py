"""Z3-based MBA (Mixed Boolean-Arithmetic) coefficient generation."""

from __future__ import annotations

from collections import deque

from z3 import And, Int, Or, Solver, sat, set_param

from shifting_codes.utils.crypto import CryptoRandom

# 15 Boolean truth tables for 2-bit inputs: f(0,0), f(0,1), f(1,0), f(1,1)
TRUTH_TABLES: list[list[int]] = [
    [0, 0, 0, 1],  # 0:  x & y
    [0, 0, 1, 0],  # 1:  x & ~y
    [0, 0, 1, 1],  # 2:  x
    [0, 1, 0, 0],  # 3:  ~x & y
    [0, 1, 0, 1],  # 4:  y
    [0, 1, 1, 0],  # 5:  x ^ y
    [0, 1, 1, 1],  # 6:  x | y
    [1, 0, 0, 0],  # 7:  ~(x | y)
    [1, 0, 0, 1],  # 8:  ~(x ^ y)
    [1, 0, 1, 0],  # 9:  ~y
    [1, 0, 1, 1],  # 10: x | ~y
    [1, 1, 0, 0],  # 11: ~x
    [1, 1, 0, 1],  # 12: ~x | y
    [1, 1, 1, 0],  # 13: ~(x & y)
    [1, 1, 1, 1],  # 14: -1 (all ones)
]

# Cache for generated coefficients
_cache: deque[list[int]] = deque(maxlen=100)


def generate_linear_mba(
    num_exprs: int = 5, rng: CryptoRandom | None = None
) -> list[int]:
    """Generate MBA coefficients using Z3 constraint solving.

    Returns a list of 15 coefficients, one per truth table entry.
    The coefficients satisfy: sum(coeffs[i] * truthTable[i][j]) == 0 for j in 0..3.
    """
    if rng is None:
        rng = CryptoRandom()

    # Check cache first
    if len(_cache) >= 100:
        coeffs = list(_cache[0])
        _cache.rotate(-1)
        return coeffs

    while True:
        # Pick random truth table indices for the expressions
        exprs = [rng.get_range(15) for _ in range(num_exprs)]

        # Create Z3 variables
        X = [Int(f"a{i}") for i in range(num_exprs)]

        seed_val = rng.get_range(2**31)
        set_param("smt.random_seed", seed_val)
        s = Solver()
        s.set("random_seed", seed_val)

        # For each of the 4 input combinations, the linear combination must be 0
        for j in range(4):
            equ = sum(X[i] * TRUTH_TABLES[exprs[i]][j] for i in range(num_exprs))
            s.add(equ == 0)

        # At least one coefficient must be non-zero
        s.add(Or([X[i] != 0 for i in range(num_exprs)]))

        # Bound coefficients for determinism
        for i in range(num_exprs):
            s.add(And(X[i] >= -10, X[i] <= 10))

        if s.check() != sat:
            continue

        model = s.model()

        # Accumulate coefficients into 15-element array
        coeffs = [0] * 15
        for i in range(num_exprs):
            val = model.eval(X[i])
            coeffs[exprs[i]] += val.as_long()

        _cache.append(list(coeffs))
        return coeffs


def clear_cache() -> None:
    """Clear the MBA coefficient cache."""
    _cache.clear()


def extended_gcd(a: int, b: int) -> tuple[int, int, int]:
    """Extended Euclidean algorithm. Returns (gcd, x, y) such that a*x + b*y = gcd."""
    if b == 0:
        return a, 1, 0
    g, x, y = extended_gcd(b, a % b)
    return g, y, x - (a // b) * y


def modular_inverse(a: int, modulus: int) -> int:
    """Compute modular inverse of a mod modulus."""
    _, x, _ = extended_gcd(a % modulus, modulus)
    return x % modulus


def generate_univariate_poly(
    bit_width: int, rng: CryptoRandom | None = None
) -> tuple[tuple[int, int], tuple[int, int]]:
    """Generate univariate polynomial coefficients for polynomial MBA.

    Returns ((a0, a1), (b0, b1)) where:
    - f(y) = a1*y + a0
    - g(z) = b1*z + b0
    - g(f(y)) = y (they are inverse functions mod 2^bitWidth)
    """
    if rng is None:
        rng = CryptoRandom()

    mask = (1 << bit_width) - 1

    a0 = rng.get_uint64() & mask
    a1 = (rng.get_uint64() | 1) & mask  # Ensure odd for invertibility

    b1 = modular_inverse(a1, 1 << bit_width) & mask
    b0 = (-(b1 * a0)) & mask

    return (a0, a1), (b0, b1)
