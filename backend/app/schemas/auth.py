from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.models.enums import AuthRole


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=120)
    password: SecretStr = Field(max_length=1024)


class SetupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    username: str = Field(min_length=1, max_length=120)
    password: SecretStr = Field(min_length=8, max_length=1024)


class SetupStatusResponse(BaseModel):
    admin_initialized: bool
    configuration_complete: bool


class CsrfResponse(BaseModel):
    csrf_token: str


class PrincipalResponse(BaseModel):
    username: str
    role: AuthRole


class LoginResponse(PrincipalResponse):
    csrf_token: str
