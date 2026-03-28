from fastapi.middleware.cors import CORSMiddleware
from config.loader import ConfigManager
from fastapi import FastAPI

def setup_cors(app: FastAPI) -> None:
    config = ConfigManager.get()
    
    if config.server.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=config.server.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["X-Request-ID", "X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"]
        )
