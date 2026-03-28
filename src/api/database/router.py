from fastapi import APIRouter

router = APIRouter(prefix="/api/db", tags=["database"])

# We'll attach routes from handlers here
from . import handlers
