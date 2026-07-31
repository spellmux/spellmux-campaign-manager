"""Campaign-scoped authorization helpers."""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from campaign_manager.models import CampaignMembership, User


def require_campaign_role(
    database: Session,
    user: User,
    campaign_id: uuid.UUID,
    allowed_roles: set[str] | None = None,
) -> CampaignMembership:
    membership = database.scalar(
        select(CampaignMembership).where(
            CampaignMembership.campaign_id == campaign_id,
            CampaignMembership.user_id == user.id,
        )
    )
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found")
    if allowed_roles is not None and membership.role not in allowed_roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient campaign role")
    return membership

