"""
Central API router.

All versioned route groups are included here and mounted under /api/v1/
in main.py.
"""

from fastapi import APIRouter

from app.api.routes import admin, auth, fellow


api_router = APIRouter(prefix="/api/v1")

api_router.include_router(auth.router)
api_router.include_router(admin.router)
api_router.include_router(fellow.router)
