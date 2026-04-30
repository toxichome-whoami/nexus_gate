import os
import sys

# Ensure the root application directory takes precedence in the import path
sys.path.insert(0, os.path.dirname(__file__))


def _build_wsgi_bridge():
    """
    Compiles the async ASGI application into a synchronous WSGI application.
    Required for executing NexusGate inside cPanel Phusion Passenger environments.
    """
    try:
        from a2wsgi import ASGIMiddleware

        from server.app import create_app

        fastapi_app = create_app()
        return ASGIMiddleware(fastapi_app)  # type: ignore

    except ImportError:
        raise RuntimeError(
            "a2wsgi is required to run on cPanel/Passenger. Please install a2wsgi via pip install a2wsgi"
        )


# Expose 'application' globally — This is the magic variable Passenger searches for.
application = _build_wsgi_bridge()
