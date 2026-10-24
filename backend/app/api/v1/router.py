from fastapi import APIRouter

from app.api.v1 import agent, catalog, health, orders, webhooks

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(catalog.router)
api_router.include_router(orders.router)
api_router.include_router(webhooks.router)
api_router.include_router(agent.router)
