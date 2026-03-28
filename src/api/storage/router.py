from fastapi import APIRouter

router = APIRouter(prefix="/api/fs", tags=["storage"])

from . import handlers
