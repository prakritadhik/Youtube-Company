from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.\-]+$")
    password: str = Field(min_length=1, max_length=256)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class NewUser(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.\-]+$")
    password: str = Field(min_length=1, max_length=256)
    role: str = Field(pattern=r"^(viewer|owner)$")


class Decision(BaseModel):
    approve: bool
    note: str = Field(default="", max_length=2000)


class PauseRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class ResumeRequest(BaseModel):
    note: str = Field(default="", max_length=500)


class SettingValue(BaseModel):
    value: Any


class BoardNote(BaseModel):
    note: str = Field(default="", max_length=2000)
