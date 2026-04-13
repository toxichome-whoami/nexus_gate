import importlib.machinery
import importlib.util
import os
import sys


# Ensure app directory is in path
sys.path.insert(0, os.path.dirname(__file__))

# Convert the ASGI application to WSGI for cPanel Phusion Passenger
try:
    from a2wsgi import ASGIMiddleware
    from server.app import create_app
    
    # Build the FastAPI app
    fastapi_app = create_app()
    # Expose 'application' as WSGI object expected by Passenger
    application = ASGIMiddleware(fastapi_app)
except ImportError:
    raise RuntimeError("a2wsgi is required to run on cPanel/Passenger. Please install a2wsgi via pip install a2wsgi")
