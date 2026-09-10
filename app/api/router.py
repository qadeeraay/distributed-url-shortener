from fastapi import APIRouter
from app.api.v1.urls import router as urls_router

api_router = APIRouter()
api_router.include_router(urls_router)
