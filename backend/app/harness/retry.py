import random


def backoff(attempt: int, base: float, cap: float) -> float:
    """Seconds to wait before retrying after failed attempt `attempt` (1-based): exponential with full jitter."""
    return random.uniform(0, min(cap, base * 2 ** (attempt - 1)))
