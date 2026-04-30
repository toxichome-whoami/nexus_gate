import re

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

UNIT_MULTIPLIERS = {
    "b": 1,
    "kb": 1024,
    "mb": 1024**2,
    "gb": 1024**3,
    "tb": 1024**4,
    "pb": 1024**5,
}

SIZE_UNITS = ["B", "KB", "MB", "GB", "TB", "PB"]

# ─────────────────────────────────────────────────────────────────────────────
# Formatter Functions
# ─────────────────────────────────────────────────────────────────────────────


def parse_size(size_str: str) -> int:
    """
    Parses a human readable size notation into an exact byte count.
    Example: '10mb' -> 10485760, '1.5 GB' -> 1610612736
    """
    if not isinstance(size_str, str):
        return int(size_str)

    normalized_str = "".join(size_str.lower().split())

    # Fast path for purely numeric un-suffixed values
    if normalized_str.isdigit() or (
        normalized_str.count(".") == 1 and normalized_str.replace(".", "").isdigit()
    ):
        return int(float(normalized_str))

    match = re.match(r"^([\d\.]+)(b|kb|mb|gb|tb|pb)$", normalized_str)
    if not match:
        raise ValueError(f"Invalid size format: {size_str}")

    scalar_value = float(match.group(1))
    unit_suffix = match.group(2)

    return int(scalar_value * UNIT_MULTIPLIERS[unit_suffix])


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


def normalize_size(size_str: str) -> str:
    """
    Normalizes any size string into clean human-readable format.
    Example: '1gb' -> '1 GB', '10mb' -> '10 MB', '1.5 GB' -> '1.50 GB'
    """
    try:
        return format_size(parse_size(size_str))
    except (ValueError, TypeError):
        return size_str
