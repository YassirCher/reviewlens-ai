from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AdminLoginRequest(StrictModel):
    email: str = Field(min_length=3, max_length=320)
    password: SecretStr = Field(min_length=1, max_length=1024)


class AdminProfile(StrictModel):
    id: uuid.UUID
    email: str


class AdminSessionResponse(StrictModel):
    admin: AdminProfile
    expires_at: datetime
    absolute_expires_at: datetime


class CsrfResponse(StrictModel):
    csrf_token: str
