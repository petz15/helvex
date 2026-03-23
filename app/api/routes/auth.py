"""Auth API routes — JWT token issuance and current-user info."""

from fastapi import APIRouter, Depends, Form, HTTPException, status
from sqlalchemy.orm import Session

from app import crud
from app.auth import create_access_token, get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.user import TokenResponse, UserRead

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/token",
    response_model=TokenResponse,
    summary="Obtain a JWT Bearer token",
    description=(
        "Standard OAuth2 password flow. POST username + password as form data, "
        "receive a Bearer token for use in the Authorization header."
    ),
)
def login_for_token(
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
) -> TokenResponse:
    user = crud.authenticate(db, username=username, password=password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return TokenResponse(access_token=create_access_token(user.id))


@router.get("/me", response_model=UserRead, summary="Current authenticated user")
def get_me(current_user: User = Depends(get_current_user)) -> UserRead:
    return UserRead.model_validate(current_user)
