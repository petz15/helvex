from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, field_validator


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: str | None = None
    tier: str
    is_active: bool
    email_verified: bool
    is_superadmin: bool
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 8 * 3600


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def password_strong_enough(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def password_strong_enough(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class RegisterRequest(BaseModel):
    username: str
    email: EmailStr
    password: str

    @field_validator("username")
    @classmethod
    def username_valid(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("Username must be at least 3 characters")
        if len(v) > 64:
            raise ValueError("Username too long")
        if not v.replace("_", "").replace("-", "").isalnum():
            raise ValueError("Username may only contain letters, digits, hyphens, and underscores")
        return v

    @field_validator("password")
    @classmethod
    def password_strong_enough(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v
