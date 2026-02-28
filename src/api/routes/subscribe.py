"""
Email subscription endpoint — adds contact to Unisender list.
"""

import os
import re
import httpx
import logging
from fastapi import APIRouter
from pydantic import BaseModel, field_validator

logger = logging.getLogger(__name__)

router = APIRouter()

UNISENDER_API_KEY = os.getenv("UNISENDER_API_KEY", "")
UNISENDER_LIST_ID = os.getenv("UNISENDER_LIST_ID", "606")

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


class SubscribeRequest(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL_RE.match(v):
            raise ValueError("invalid email")
        return v


@router.post("/api/subscribe")
async def subscribe(data: SubscribeRequest):
    """Add email to Unisender list."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.unisender.com/ru/api/subscribe",
                params={"format": "json"},
                data={
                    "api_key": UNISENDER_API_KEY,
                    "list_ids": UNISENDER_LIST_ID,
                    "fields[email]": data.email,
                    "double_optin": 3,
                    "tags": "notes-blog",
                },
            )
            result = resp.json()

        if "result" in result:
            logger.info(f"Subscribed {data.email} to Unisender, person_id={result['result'].get('person_id')}")
            return {"ok": True}
        else:
            error = result.get("error", "Unknown error")
            logger.warning(f"Unisender error for {data.email}: {error}")
            return {"ok": False, "error": error}

    except Exception as e:
        logger.error(f"Subscribe failed for {data.email}: {e}")
        return {"ok": False, "error": "Service unavailable"}
