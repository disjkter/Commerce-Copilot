import sqlite3

from pydantic import BaseModel, Field, field_validator

from .config import Settings, get_settings
from .contracts import UserRecord
from .database import CatalogRepository, get_catalog
from .security import create_access_token, hash_password, verify_password


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized.replace("_", "").isalnum():
            raise ValueError("username may only contain letters, numbers, and underscores")
        return normalized


class PublicUser(BaseModel):
    user_id: str
    username: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: PublicUser


class UsernameTakenError(ValueError):
    pass


class InvalidCredentialsError(ValueError):
    pass


class AuthService:
    def __init__(
        self,
        repository: CatalogRepository | None = None,
        settings: Settings | None = None,
    ):
        self.repository = repository or get_catalog()
        self.settings = settings or get_settings()

    def register(self, credentials: Credentials) -> AuthResponse:
        try:
            user = self.repository.create_user(
                credentials.username,
                hash_password(credentials.password),
            )
        except sqlite3.IntegrityError as exc:
            raise UsernameTakenError("username is already registered") from exc
        return self._response(user)

    def login(self, credentials: Credentials) -> AuthResponse:
        user = self.repository.get_user_by_username(credentials.username)
        if user is None or not verify_password(credentials.password, user.password_hash):
            raise InvalidCredentialsError("invalid username or password")
        return self._response(user)

    def _response(self, user: UserRecord) -> AuthResponse:
        token = create_access_token(
            user.user_id,
            self.settings.auth_secret,
            expires_minutes=self.settings.access_token_minutes,
        )
        return AuthResponse(
            access_token=token,
            user=PublicUser(user_id=user.user_id, username=user.username),
        )
