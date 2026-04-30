import base64
import os
import secrets

# ─────────────────────────────────────────────────────────────────────────────
# Default Template Definitions
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_CONFIG_CONTENT = """
# ╔══════════════════════════════════════════════════════════════╗
# ║                    NexusGate Configuration                   ║
# ╚══════════════════════════════════════════════════════════════╝

[server]
host            = "0.0.0.0"
port            = 4500
workers         = 0
max_connections = 10000
request_timeout = 30
body_limit      = "10mb"

[features]
database   = true
storage    = true
webhook    = true
federation = false
metrics    = true
playground = false

[logging]
level       = "INFO"
format      = "json"
directory   = "./logs"
file_prefix = "nexusgate"

[database.local_cache]
engine = "sqlite"
url    = "./data/cache.db"
mode   = "readwrite"
pool_min = 1
pool_max = 5
dangerous_operations = false

[storage.media]
path       = "./storage/media"
mode       = "readwrite"
limit      = "5gb"

[api_key.admin_key]
mode      = "readwrite"
secret    = "{admin_secret}"
db_scope  = ["*"]
fs_scope  = ["*"]
rate_limit_override = 0
"""

# ─────────────────────────────────────────────────────────────────────────────
# Bootstrap Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _scaffold_directories() -> None:
    """Pre-configures the persistent structural filesystem bounds required for execution."""
    os.makedirs("./logs", exist_ok=True)
    os.makedirs("./storage", exist_ok=True)
    os.makedirs("./data", exist_ok=True)
    os.makedirs("./storage/media", exist_ok=True)


def _render_config_payload(admin_secret: str) -> str:
    """Ijects dynamically generated cryptographic salts strictly into the config template."""
    return DEFAULT_CONFIG_CONTENT.format(admin_secret=admin_secret)


def _write_config_file(path: str, payload: str) -> None:
    """Commits the bootstrapped config to the active execution directory safely."""
    with open(path, "w", encoding="utf-8") as file:
        file.write(payload)


def _print_bootstrap_instructions(path: str, admin_secret: str) -> None:
    """Alerts administrators locally to store the generated bootstrap credential."""
    encoded_token = base64.b64encode(f"admin_key:{admin_secret}".encode()).decode()

    print("=" * 60)
    print("NexusGate Initialized!")
    print(f"Generated default config at: {path}")
    print(f"Your admin API key: {admin_secret} (save this, it won't be shown again)")
    print(f"To use it, set header: Authorization: Bearer {encoded_token}")
    print("=" * 60)


# ─────────────────────────────────────────────────────────────────────────────
# Execution
# ─────────────────────────────────────────────────────────────────────────────


def generate_default_config(path: str = "config.toml") -> str:
    """Auto-generates the local TOML mapping alongside physical database targets."""
    admin_secret = "your_secret_key_here" + secrets.token_hex(20)

    _scaffold_directories()

    config_payload = _render_config_payload(admin_secret)
    _write_config_file(path, config_payload)
    _print_bootstrap_instructions(path, admin_secret)

    return admin_secret


if __name__ == "__main__":
    generate_default_config()
