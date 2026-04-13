from fastapi import APIRouter

router = APIRouter(tags=["database"])

# We'll attach routes from handlers here
from . import handlers
