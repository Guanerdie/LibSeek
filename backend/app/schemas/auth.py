from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.models.enums import AuthRole


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=120)
    password: SecretStr


class CsrfResponse(BaseModel):
    csrf_token: str


class PrincipalResponse(BaseModel):
    username: str
    role: AuthRole


class LoginResponse(PrincipalResponse):
    csrf_token: str
