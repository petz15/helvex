from app.api.routes.admin import router as admin_router
from app.api.routes.auth import router as auth_router
from app.api.routes.companies import router as companies_router
from app.api.routes.invites import router as invites_router
from app.api.routes.jobs import router as jobs_router
from app.api.routes.map import router as map_router
from app.api.routes.notes import router as notes_router
from app.api.routes.ops_settings import router as settings_router
from app.api.routes.orgs import router as orgs_router
from app.api.routes.views import router as views_router
from app.api.routes.workspace import router as workspace_router

__all__ = [
    "admin_router",
    "auth_router",
    "companies_router",
    "invites_router",
    "jobs_router",
    "map_router",
    "notes_router",
    "settings_router",
    "orgs_router",
    "views_router",
    "workspace_router",
]
