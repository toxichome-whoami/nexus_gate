from fastapi import APIRouter

router = APIRouter(tags=["storage"])

from . import handlers  # noqa:E402

_ = handlers
