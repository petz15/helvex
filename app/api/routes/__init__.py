from app.api.routes.auth import router as auth_router
from app.api.routes.companies import router as companies_router
from app.api.routes.jobs import router as jobs_router
from app.api.routes.map import router as map_router
from app.api.routes.notes import router as notes_router
from app.api.routes.ops_settings import router as settings_router

__all__ = ["auth_router", "companies_router", "notes_router", "jobs_router", "map_router", "settings_router"]
