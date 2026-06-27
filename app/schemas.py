from datetime import datetime

from pydantic import BaseModel, Field


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
