import os
import secrets
import base64

DEFAULT_CONFIG_CONTENT = """# ╔══════════════════════════════════════════════════════════════╗
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

def generate_default_config(path: str = "config.toml") -> str:
    admin_secret = "your_secret_key_here" + secrets.token_hex(20)
    config_content = DEFAULT_CONFIG_CONTENT.format(admin_secret=admin_secret)

    with open(path, "w", encoding="utf-8") as f:
        f.write(config_content)

    os.makedirs("./logs", exist_ok=True)
    os.makedirs("./storage", exist_ok=True)
    os.makedirs("./data", exist_ok=True)
    os.makedirs("./storage/media", exist_ok=True)

    encoded_token = base64.b64encode(f"admin_key:{admin_secret}".encode()).decode()

    print("=" * 60)
    print("NexusGate Initialized!")
    print(f"Generated default config at: {path}")
    print(f"Your admin API key: {admin_secret} (save this, it won't be shown again)")
    print(f"To use it, set header: Authorization: Bearer {encoded_token}")
    print("=" * 60)

    return admin_secret

if __name__ == "__main__":
    generate_default_config()
