import re

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

UNIT_MULTIPLIERS = {
    'b': 1,
    'kb': 1024,
    'mb': 1024**2,
    'gb': 1024**3,
    'tb': 1024**4,
    'pb': 1024**5
}

SIZE_UNITS = ["B", "KB", "MB", "GB", "TB", "PB"]

# ─────────────────────────────────────────────────────────────────────────────
# Formatter Functions
# ─────────────────────────────────────────────────────────────────────────────

def parse_size(size_str: str) -> int:
    """
    Parses a human readable size notation into an exact byte count.
    Example: '10mb' -> 10485760
    """
    if not isinstance(size_str, str):
        return int(size_str)
        
    normalized_str = size_str.lower().strip()
    
    # Fast path for purely numeric un-suffixed values
    if normalized_str.isdigit():
        return int(normalized_str)
        
    match = re.match(r'^(\d+)(b|kb|mb|gb|tb|pb)$', normalized_str)
    if not match:
        raise ValueError(f"Invalid size format: {size_str}")
        
    scalar_value = int(match.group(1))
    unit_suffix = match.group(2)
    
    return scalar_value * UNIT_MULTIPLIERS[unit_suffix]


def format_size(size_in_bytes: int) -> str:
    """
    Converts a byte count into a formatted human readable notation.
    Example: 10485760 -> '10.00 MB'
    """
    if size_in_bytes < 0:
        return "0 B"
        
    current_value = float(size_in_bytes)
    
    for unit in SIZE_UNITS:
        if current_value < 1024.0:
            if unit == "B":
                return f"{int(current_value)} {unit}"
            return f"{current_value:.2f} {unit}"
            
        current_value /= 1024.0
        
    return f"{current_value:.2f} PB"
