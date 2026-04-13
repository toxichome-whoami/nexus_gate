from fastapi import APIRouter

router = APIRouter(tags=["storage"])

from . import handlers
