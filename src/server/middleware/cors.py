from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from config.loader import ConfigManager

def setup_cors(app: FastAPI) -> None:
    """Injects dynamically driven Cross-Origin Resource Sharing logic to the pipeline."""
    config = ConfigManager.get()
    
    if not config.server.cors_origins:
        return

    allowed_headers = [
        "X-Request-ID", 
        "X-RateLimit-Limit", 
        "X-RateLimit-Remaining", 
        "X-RateLimit-Reset"
    ]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.server.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=allowed_headers
    )
