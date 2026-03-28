import time
import os
import uuid

def uuid7() -> uuid.UUID:
    """Generate a UUID version 7 (time-ordered).
    Format: 48 bits timestamp (ms) | 12 bits version | 2 bits var | 62 bits random.
    """
    timestamp_ms = int(time.time() * 1000)
    
    # 48 bits of timestamp
    time_high = timestamp_ms >> 16
    time_mid = timestamp_ms & 0xFFFF
    
    # 12 bits version 7, 62 bits random
    rand_a = int.from_bytes(os.urandom(2), "big") & 0x0FFF
    rand_b_bytes = os.urandom(8)
    rand_b = int.from_bytes(rand_b_bytes, "big")
    
    # Apply version 7 to rand_a
    time_hi_and_version = rand_a | (7 << 12)
    
    # Apply variant (10) to clock_seq_hi_and_reserved
    clock_seq_hi_and_reserved = (rand_b >> 56) & 0x3F | 0x80
    clock_seq_low = (rand_b >> 48) & 0xFF
    node = rand_b & 0xFFFFFFFFFFFF
    
    return uuid.UUID(
        fields=(
            time_high,
            time_mid,
            time_hi_and_version,
            clock_seq_hi_and_reserved,
            clock_seq_low,
            node
        )
    )
