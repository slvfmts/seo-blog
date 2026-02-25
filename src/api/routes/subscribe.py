"""
Email subscription endpoint — adds contact to Unisender list.
"""

import httpx
import logging
from fastapi import APIRouter, Request
from pydantic import BaseModel, EmailStr

logger = logging.getLogger(__name__)

router = APIRouter()

UNISENDER_API_KEY = "***REDACTED***"
UNISENDER_LIST_ID = "606"


class SubscribeRequest(BaseModel):
    email: EmailStr


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
