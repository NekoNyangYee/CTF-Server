from datetime import datetime

from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, field_validator


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1)


class UserRegisterRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1)
    nickname: str | None = Field(default=None, max_length=80)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ChallengeFileResponse(BaseModel):
    id: int
    original_filename: str
    file_path: str
    file_size: int
    mime_type: str | None = None
    created_at: datetime | None = None


class ChallengeResponse(BaseModel):
    id: int
    title: str
    slug: str
    category: str
    description: str | None = None
    score: int
    is_public: bool
    created_at: datetime
    updated_at: datetime | None = None
    files: list[ChallengeFileResponse] = []


class ChallengeUpdateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=50)
    description: str | None = None
    score: int = Field(ge=0)
    is_public: bool = False


class FlagUpdateRequest(BaseModel):
    flag: str = Field(min_length=1, max_length=500)


class VisibilityUpdateRequest(BaseModel):
    is_public: bool


class SubmitFlagRequest(BaseModel):
    nickname: str | None = Field(default=None, max_length=80)
    flag: str = Field(min_length=1, max_length=500)


class SubmitFlagResponse(BaseModel):
    correct: bool
    already_solved: bool = False


class ScoreboardEntry(BaseModel):
    nickname: str
    total_score: int
    solved_count: int
    last_solved_at: datetime


class SettingsUpdateRequest(BaseModel):
    site_name: str | None = None
    flag_prefix: str | None = None
    competition_mode: Literal["individual", "team"] | None = None
    scoreboard_hidden_from: AwareDatetime | None = None
    scoreboard_hidden_until: AwareDatetime | None = None


class TeamRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Name must not be blank")
        return value.strip()


class UserUpdateRequest(BaseModel):
    nickname: str | None = Field(default=None, min_length=1, max_length=80)
    password: str | None = Field(default=None, min_length=1, max_length=72)
    is_active: bool | None = None
    team_id: int | None = Field(default=None, gt=0)

    @field_validator("nickname")
    @classmethod
    def clean_nickname(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Nickname must not be blank")
        return value.strip() if value is not None else None

    @field_validator("password")
    @classmethod
    def password_bytes(cls, value: str | None) -> str | None:
        if value is not None and len(value.encode("utf-8")) > 72:
            raise ValueError("Password must be at most 72 UTF-8 bytes")
        return value
