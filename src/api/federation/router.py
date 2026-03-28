from fastapi import APIRouter

router = APIRouter(prefix="/api/fed", tags=["federation"])

from . import handlers
