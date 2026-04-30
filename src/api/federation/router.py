from fastapi import APIRouter

router = APIRouter(tags=["federation"])

from . import handlers  # noqa: E402

_ = handlers  # Imported for side-effects (route registration)
