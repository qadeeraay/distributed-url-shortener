from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, HttpUrl, Field, field_validator
from app.core.base62 import validate_custom_alias


class URLCreateRequest(BaseModel):
    url: HttpUrl = Field(..., description="The original long destination URL to shorten")
    custom_alias: Optional[str] = Field(
        None,
        min_length=4,
        max_length=32,
        description="Optional custom slug (4-32 characters, alphanumeric, hyphens, underscores)"
    )
    expires_in_hours: Optional[int] = Field(
        None,
        ge=1,
        le=8760,  # Max 1 year
        description="Optional URL time-to-live in hours"
    )

    @field_validator("custom_alias")
    @classmethod
    def validate_alias_format(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.strip()
            if not validate_custom_alias(v):
                raise ValueError(
                    "Invalid custom alias. Must be 4-32 alphanumeric characters, dashes, or underscores, "
                    "and cannot match reserved route keywords."
                )
        return v


class URLResponse(BaseModel):
    short_code: str
    short_url: str
    original_url: str
    is_custom: bool
    created_at: datetime
    expires_at: Optional[datetime] = None
    clicks_count: int


class TimelinePoint(BaseModel):
    timestamp: str
    clicks: int
    unique_visitors: int


class URLAnalyticsResponse(BaseModel):
    short_code: str
    original_url: str
    total_clicks: int
    unique_visitors: int
    top_referrers: Dict[str, int]
    top_countries: Dict[str, int]
    browsers: Dict[str, int]
    platforms: Dict[str, int]
    devices: Dict[str, int]
    timeline_last_24h: List[TimelinePoint]


class ClickEventMessage(BaseModel):
    short_code: str
    timestamp: str
    ip: str
    user_agent: str
    referrer: str
