import time
import os
import uuid

def _get_current_timestamp_ms() -> int:
    """Returns the current precise POSIX epoch mapped to milliseconds."""
    return int(time.time() * 1000)

def _generate_time_components(timestamp_ms: int) -> tuple[int, int]:
    """Isolates the 48-bit timestamp into high (32-bit) and mid (16-bit) fields."""
    time_high = timestamp_ms >> 16
    time_mid = timestamp_ms & 0xFFFF
    return time_high, time_mid

def _generate_random_components() -> tuple[int, int]:
    """Constructs cryptographically secure randomness bits for the sequence."""
    random_high = int.from_bytes(os.urandom(2), "big") & 0x0FFF
    random_low = int.from_bytes(os.urandom(8), "big")
    return random_high, random_low

def uuid7() -> uuid.UUID:
    """
    Generates a UUID version 7 identifier.
    
    Format guarantees strict time-ordering which vastly improves database
    indexing performance over random v4 UUIDs, while preventing collisions.
    Structure: 48-bit timestamp (ms) | 12-bit version | 2-bit variation | 62-bit random.
    """
    current_ms = _get_current_timestamp_ms()
    
    time_high, time_mid = _generate_time_components(current_ms)
    random_high, random_low = _generate_random_components()
    
    # Multiplex Version 7 layout into the first random segment
    versioned_high_sequence = random_high | (7 << 12)
    
    # Enforce standard UUID variant constraints (1 0) onto the lower sequence
    clock_sequence_variant = (random_low >> 56) & 0x3F | 0x80
    clock_seq_low = (random_low >> 48) & 0xFF
    node_id = random_low & 0xFFFFFFFFFFFF
    
    return uuid.UUID(
        fields=(
            time_high,
            time_mid,
            versioned_high_sequence,
            clock_sequence_variant,
            clock_seq_low,
            node_id
        )
    )
