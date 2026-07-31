"""HTTP API entry point."""

from __future__ import annotations

import re

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from campaign_manager import __version__
from campaign_manager.auth import authenticate, current_user, issue_token, revoke_token
from campaign_manager.config import Settings
from campaign_manager.database import database_session
from campaign_manager.models import Campaign, CampaignMembership, CampaignRole, User
from campaign_manager.schemas import (
    CampaignCreate,
    CampaignResponse,
    LoginRequest,
    TokenResponse,
    UserResponse,
)

_bearer = HTTPBearer(auto_error=False)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().casefold()).strip("-")
    if not slug:
        raise HTTPException(status_code=422, detail="Campaign name cannot produce an empty slug")
    return slug[:100]


def _campaign_response(campaign: Campaign, role: str) -> CampaignResponse:
    return CampaignResponse(
        id=campaign.id,
        slug=campaign.slug,
        name=campaign.name,
        description=campaign.description,
        created_at=campaign.created_at,
        role=role,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings.from_environment()
    app = FastAPI(title="Campaign Manager", version=__version__)

    @app.get("/api/v1/health", tags=["system"])
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "version": __version__,
            "environment": resolved.environment,
        }

    @app.get("/api/v1/ready", tags=["system"])
    def readiness(database: Session = Depends(database_session)) -> dict[str, str]:
        database.execute(text("SELECT 1"))
        return {"status": "ready"}

    @app.post("/api/v1/auth/login", response_model=TokenResponse, tags=["authentication"])
    def login(request: LoginRequest, database: Session = Depends(database_session)) -> TokenResponse:
        user = authenticate(database, request.email, request.password)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )
        raw_token, token = issue_token(database, user)
        return TokenResponse(access_token=raw_token, expires_at=token.expires_at)

    @app.post("/api/v1/auth/logout", status_code=204, tags=["authentication"])
    def logout(
        credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
        database: Session = Depends(database_session),
    ) -> None:
        if credentials is not None:
            revoke_token(database, credentials.credentials)

    @app.get("/api/v1/auth/me", response_model=UserResponse, tags=["authentication"])
    def me(user: User = Depends(current_user)) -> User:
        return user

    @app.get("/api/v1/campaigns", response_model=list[CampaignResponse], tags=["campaigns"])
    def list_campaigns(
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> list[CampaignResponse]:
        statement = (
            select(Campaign, CampaignMembership.role)
            .join(CampaignMembership, CampaignMembership.campaign_id == Campaign.id)
            .where(CampaignMembership.user_id == user.id)
            .order_by(Campaign.name)
        )
        return [_campaign_response(campaign, role) for campaign, role in database.execute(statement)]

    @app.post(
        "/api/v1/campaigns",
        response_model=CampaignResponse,
        status_code=201,
        tags=["campaigns"],
    )
    def create_campaign(
        request: CampaignCreate,
        user: User = Depends(current_user),
        database: Session = Depends(database_session),
    ) -> CampaignResponse:
        campaign = Campaign(
            name=request.name.strip(),
            slug=_slugify(request.slug or request.name),
            description=request.description.strip(),
            created_by_id=user.id,
        )
        database.add(campaign)
        database.flush()
        database.add(
            CampaignMembership(
                campaign_id=campaign.id,
                user_id=user.id,
                role=CampaignRole.OWNER.value,
            )
        )
        try:
            database.commit()
        except IntegrityError as exc:
            database.rollback()
            raise HTTPException(status_code=409, detail="Campaign slug already exists") from exc
        database.refresh(campaign)
        return _campaign_response(campaign, CampaignRole.OWNER.value)

    return app


app = create_app()


def main() -> None:
    import uvicorn

    settings = Settings.from_environment()
    uvicorn.run(
        "campaign_manager.api:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
