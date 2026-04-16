"""
API module initialization.
"""
from .config_routes import router as config_router
from .data_routes import router as data_router
from .stream_routes import router as stream_router

__all__ = [
    'config_router',
    'data_router',
    'stream_router',
]
