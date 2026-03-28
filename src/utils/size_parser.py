import re

def parse_size(size_str: str) -> int:
    """Parse size string to bytes (e.g. '10mb' -> 10485760)."""
    if not isinstance(size_str, str):
        return int(size_str)
        
    size_str = size_str.lower().strip()
    if size_str.isdigit():
        return int(size_str)
        
    match = re.match(r'^(\d+)(b|kb|mb|gb|tb|pb)$', size_str)
    if not match:
        raise ValueError(f"Invalid size format: {size_str}")
        
    val = int(match.group(1))
    unit = match.group(2)
    
    multiplier = {
        'b': 1,
        'kb': 1024,
        'mb': 1024**2,
        'gb': 1024**3,
        'tb': 1024**4,
        'pb': 1024**5
    }
    
    return val * multiplier[unit]
