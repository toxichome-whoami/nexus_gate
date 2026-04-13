from fastapi import APIRouter

router = APIRouter(tags=["federation"])

from . import handlers
