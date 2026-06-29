"""API dependencies.

Dependency injection factories for services, repositories, and authentication.
"""
# ruff: noqa: I001, E402 - Imports structured for Jinja2 template conditionals

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer

from app.core.config import settings
from app.db.session import get_db_session
from sqlalchemy.ext.asyncio import AsyncSession

DBSession = Annotated[AsyncSession, Depends(get_db_session)]


# === Service Dependencies ===

from app.services.user import UserService
from app.services.session import SessionService
from app.services.item import ItemService
from app.services.conversation import ConversationService
from app.services.app_settings import AppSettingsService


def get_user_service(db: DBSession) -> UserService:
    """Create UserService instance with database session."""
    return UserService(db)


def get_session_service(db: DBSession) -> SessionService:
    """Create SessionService instance with database session."""
    return SessionService(db)


UserSvc = Annotated[UserService, Depends(get_user_service)]
SessionSvc = Annotated[SessionService, Depends(get_session_service)]


def get_item_service(db: DBSession) -> ItemService:
    """Create ItemService instance with database session."""
    return ItemService(db)


ItemSvc = Annotated[ItemService, Depends(get_item_service)]


def get_app_settings_service(db: DBSession) -> AppSettingsService:
    """Create AppSettingsService instance with database session."""
    return AppSettingsService(db)


AppSettingsSvc = Annotated[AppSettingsService, Depends(get_app_settings_service)]


def get_conversation_service(db: DBSession) -> ConversationService:
    """Create ConversationService instance with database session."""
    return ConversationService(db)


ConversationSvc = Annotated[ConversationService, Depends(get_conversation_service)]


from app.services.openphone import OpenPhoneService


def get_openphone_service(db: DBSession) -> OpenPhoneService:
    """Create OpenPhoneService instance with database session."""
    return OpenPhoneService(db)


OpenPhoneSvc = Annotated[OpenPhoneService, Depends(get_openphone_service)]


from app.services.whatsapp import WhatsappService


def get_whatsapp_service(db: DBSession) -> WhatsappService:
    """Create WhatsappService instance with database session."""
    return WhatsappService(db)


WhatsappSvc = Annotated[WhatsappService, Depends(get_whatsapp_service)]

# === Authentication Dependencies ===

from app.core.exceptions import AuthenticationError, AuthorizationError
from app.db.models.user import User, UserRole

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_STR}/auth/login")


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    user_service: UserSvc,
) -> User:
    """Get current authenticated user from JWT token.

    Returns the full User object including role information.

    Raises:
        AuthenticationError: If token is invalid or user not found.
    """
    from uuid import UUID

    from app.core.security import verify_token

    payload = verify_token(token)
    if payload is None:
        raise AuthenticationError(message="Invalid or expired token")

    # Ensure this is an access token, not a refresh token
    if payload.get("type") != "access":
        raise AuthenticationError(message="Invalid token type")

    user_id = payload.get("sub")
    if user_id is None:
        raise AuthenticationError(message="Invalid token payload")

    user = await user_service.get_by_id(UUID(user_id))
    if not user.is_active:
        raise AuthenticationError(message="User account is disabled")

    return user


class RoleChecker:
    """Dependency class for role-based access control.

    Usage:
        # Require admin role
        @router.get("/admin-only")
        async def admin_endpoint(
            user: Annotated[User, Depends(RoleChecker(UserRole.ADMIN))]
        ):
            ...

        # Require any authenticated user
        @router.get("/users")
        async def users_endpoint(
            user: Annotated[User, Depends(get_current_user)]
        ):
            ...
    """

    def __init__(self, required_role: UserRole) -> None:
        self.required_role = required_role

    async def __call__(
        self,
        user: Annotated[User, Depends(get_current_user)],
    ) -> User:
        """Check if user has the required role.

        Raises:
            AuthorizationError: If user doesn't have the required role.
        """
        if not user.has_role(self.required_role):
            raise AuthorizationError(
                message=f"Role '{self.required_role.value}' required for this action"
            )
        return user


async def get_current_active_superuser(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Get current user and verify they are a superuser.

    Raises:
        AuthorizationError: If user is not a superuser.
    """
    if not current_user.is_superuser:
        raise AuthorizationError(message="Superuser privileges required")
    return current_user


# === Service-Account Auth (WhatsApp extension) ===

from datetime import UTC, datetime

from fastapi import Header

from app.core.security import verify_api_key, verify_token
from app.repositories import user_repo as user_repo_module


async def authenticate_service_key(
    db: DBSession,
    x_service_api_key: Annotated[str | None, Header(alias="X-Service-Api-Key")] = None,
) -> User:
    """Authenticate a service account via ``X-Service-Api-Key`` header.

    Used by ``POST /api/v1/whatsapp/auth/service-token`` to exchange the
    long-lived API key for a JWT pair. The key is verified in two steps:
    a prefix lookup (12 chars) followed by a bcrypt check on the full key.

    Raises:
        AuthenticationError: If the header is missing, the prefix matches no
            user, or the bcrypt verification fails.
    """
    if not x_service_api_key:
        raise AuthenticationError(message="Missing X-Service-Api-Key header")

    prefix = x_service_api_key[:12]
    user = await user_repo_module.get_by_service_api_key_prefix(db, prefix)
    if not user or not user.service_api_key_hash:
        raise AuthenticationError(message="Invalid API key")
    if not verify_api_key(x_service_api_key, user.service_api_key_hash):
        raise AuthenticationError(message="Invalid API key")
    if not user.is_active or not user.is_service_account:
        raise AuthenticationError(message="Service account is disabled")

    user.service_account_last_used_at = datetime.now(UTC)
    db.add(user)
    await db.flush()
    return user


async def get_service_account(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: DBSession,
) -> User:
    """Get the current service account from a JWT bearer token.

    Mirrors ``get_current_user`` but requires the ``svc: true`` claim
    AND a User row with ``is_service_account=True`` (defense in depth —
    don't trust the claim alone).

    Raises:
        AuthenticationError: If the token is invalid, missing the ``svc``
            claim, or the user is not a service account.
    """
    from uuid import UUID

    payload = verify_token(token)
    if payload is None:
        raise AuthenticationError(message="Invalid or expired token")
    if payload.get("type") != "access":
        raise AuthenticationError(message="Invalid token type")
    if payload.get("svc") is not True:
        raise AuthenticationError(message="Not a service token")

    user_id = payload.get("sub")
    if user_id is None:
        raise AuthenticationError(message="Invalid token payload")

    user = await user_repo_module.get_by_id(db, UUID(user_id))
    if not user or not user.is_active or not user.is_service_account:
        raise AuthenticationError(message="Service account invalid or disabled")
    return user


async def get_current_user_or_service_account(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: DBSession,
) -> User:
    """Accept either a regular user JWT or a service-account JWT.

    Used for endpoints that the Chrome extension reads from with its
    service-account JWT AND that a human admin might also read with a
    regular user JWT. Write endpoints (POST/PATCH/DELETE) stay gated on
    ``CurrentUser`` — only the operator should mutate the whitelist.

    Detection: service tokens carry the ``svc: true`` claim. We dispatch
    to ``get_service_account`` if present, otherwise to
    ``get_current_user``. We do this WITHOUT calling the originals (which
    would each independently verify the token and re-fetch the user) —
    we do one verify_token + one user lookup.
    """
    from uuid import UUID

    payload = verify_token(token)
    if payload is None:
        raise AuthenticationError(message="Invalid or expired token")
    if payload.get("type") != "access":
        raise AuthenticationError(message="Invalid token type")

    user_id = payload.get("sub")
    if user_id is None:
        raise AuthenticationError(message="Invalid token payload")

    user = await user_repo_module.get_by_id(db, UUID(user_id))
    if not user or not user.is_active:
        raise AuthenticationError(message="User account is disabled")

    if payload.get("svc") is True:
        if not user.is_service_account:
            raise AuthenticationError(message="Service token used by non-service account")
        return user

    return user


# Type aliases for dependency injection
CurrentUser = Annotated[User, Depends(get_current_user)]
CurrentSuperuser = Annotated[User, Depends(get_current_active_superuser)]
CurrentAdmin = Annotated[User, Depends(RoleChecker(UserRole.ADMIN))]
CurrentServiceAccount = Annotated[User, Depends(get_service_account)]
CurrentUserOrService = Annotated[User, Depends(get_current_user_or_service_account)]


# WebSocket authentication dependency
from fastapi import WebSocket, Query, Cookie


async def get_current_user_ws(
    websocket: WebSocket,
    token: str | None = Query(None, alias="token"),
    access_token: str | None = Cookie(None),
) -> User:
    """Get current user from WebSocket JWT token.

    Token can be passed either as:
    - Query parameter: ws://...?token=<jwt>
    - Cookie: access_token cookie (set by HTTP login)

    Raises:
        AuthenticationError: If token is invalid or user not found.
    """
    from uuid import UUID

    from app.core.security import verify_token

    # Try query parameter first, then cookie
    auth_token = token or access_token

    if not auth_token:
        await websocket.close(code=4001, reason="Missing authentication token")
        raise AuthenticationError(message="Missing authentication token")

    payload = verify_token(auth_token)
    if payload is None:
        await websocket.close(code=4001, reason="Invalid or expired token")
        raise AuthenticationError(message="Invalid or expired token")

    if payload.get("type") != "access":
        await websocket.close(code=4001, reason="Invalid token type")
        raise AuthenticationError(message="Invalid token type")

    user_id = payload.get("sub")
    if user_id is None:
        await websocket.close(code=4001, reason="Invalid token payload")
        raise AuthenticationError(message="Invalid token payload")

    from app.db.session import get_db_context

    async with get_db_context() as db:
        user_service = UserService(db)
        user = await user_service.get_by_id(UUID(user_id))

    if not user.is_active:
        await websocket.close(code=4001, reason="User account is disabled")
        raise AuthenticationError(message="User account is disabled")

    return user
