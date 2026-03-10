"""
Weekly SEO report — Yandex Webmaster + Google Search Console.
Sends summary to Pachka chat every Monday at 15:00 MSK.

Env vars required:
  YANDEX_WEBMASTER_TOKEN
  GSC_CLIENT_ID, GSC_CLIENT_SECRET, GSC_REFRESH_TOKEN
  ALERT_PACHCA_TOKEN
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# --- Config ---
YWM_TOKEN = os.environ["YANDEX_WEBMASTER_TOKEN"]
YWM_USER_ID = "100387890"
YWM_HOST_ID = "https:notes.editors.one:443"
YWM_BASE = f"https://api.webmaster.yandex.net/v4/user/{YWM_USER_ID}/hosts/{YWM_HOST_ID}"

GSC_CLIENT_ID = os.environ["GSC_CLIENT_ID"]
GSC_CLIENT_SECRET = os.environ["GSC_CLIENT_SECRET"]
GSC_REFRESH_TOKEN = os.environ["GSC_REFRESH_TOKEN"]
GSC_SITE = "sc-domain:notes.editors.one"

PACHCA_TOKEN = os.environ["ALERT_PACHCA_TOKEN"]
PACHCA_CHAT_ID = 33569372
PACHCA_API_URL = "https://api.pachca.com/api/shared/v1"


# --- Yandex Webmaster ---
async def fetch_yandex(client: httpx.AsyncClient) -> dict:
    headers = {"Authorization": f"OAuth {YWM_TOKEN}"}
    today = datetime.now().strftime("%Y-%m-%d")
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    two_weeks_ago = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")

    # Summary
    resp = await client.get(f"{YWM_BASE}/summary/", headers=headers)
    summary = resp.json()

    # Search queries this week
    resp = await client.get(
        f"{YWM_BASE}/search-queries/all/history/",
        headers=headers,
        params={
            "query_indicator": ["TOTAL_SHOWS", "TOTAL_CLICKS"],
            "date_from": two_weeks_ago,
            "date_to": today,
        },
    )
    history = resp.json()

    # Compute weekly totals
    this_week_shows = 0
    prev_week_shows = 0
    this_week_clicks = 0
    prev_week_clicks = 0

    for point in history.get("indicators", {}).get("TOTAL_SHOWS", []):
        date = point["date"][:10]
        if date >= week_ago:
            this_week_shows += point["value"]
        else:
            prev_week_shows += point["value"]

    for point in history.get("indicators", {}).get("TOTAL_CLICKS", []):
        date = point["date"][:10]
        if date >= week_ago:
            this_week_clicks += point["value"]
        else:
            prev_week_clicks += point["value"]

    # Diagnostics
    resp = await client.get(f"{YWM_BASE}/diagnostics/", headers=headers)
    diag = resp.json()
    problems = []
    for name, info in diag.get("problems", {}).items():
        if info.get("state") == "PRESENT":
            problems.append(name)

    return {
        "sqi": summary.get("sqi", 0),
        "indexed": summary.get("searchable_pages_count", 0),
        "excluded": summary.get("excluded_pages_count", 0),
        "this_week_shows": int(this_week_shows),
        "prev_week_shows": int(prev_week_shows),
        "this_week_clicks": int(this_week_clicks),
        "prev_week_clicks": int(prev_week_clicks),
        "problems": problems,
    }


# --- Google Search Console ---
async def get_gsc_access_token(client: httpx.AsyncClient) -> str:
    resp = await client.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": GSC_CLIENT_ID,
            "client_secret": GSC_CLIENT_SECRET,
            "refresh_token": GSC_REFRESH_TOKEN,
            "grant_type": "refresh_token",
        },
    )
    return resp.json()["access_token"]


async def fetch_gsc(client: httpx.AsyncClient) -> dict:
    token = await get_gsc_access_token(client)
    headers = {"Authorization": f"Bearer {token}"}
    base = "https://searchconsole.googleapis.com/webmasters/v3"

    today = datetime.now().strftime("%Y-%m-%d")
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    two_weeks_ago = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")

    # This week
    resp = await client.post(
        f"{base}/sites/{GSC_SITE}/searchAnalytics/query",
        headers=headers,
        json={
            "startDate": week_ago,
            "endDate": today,
            "dataState": "all",
        },
    )
    this_week = resp.json()

    # Previous week
    resp = await client.post(
        f"{base}/sites/{GSC_SITE}/searchAnalytics/query",
        headers=headers,
        json={
            "startDate": two_weeks_ago,
            "endDate": week_ago,
        },
    )
    prev_week = resp.json()

    # Top pages this week
    resp = await client.post(
        f"{base}/sites/{GSC_SITE}/searchAnalytics/query",
        headers=headers,
        json={
            "startDate": week_ago,
            "endDate": today,
            "dimensions": ["page"],
            "rowLimit": 5,
            "dataState": "all",
        },
    )
    top_pages = resp.json()

    def extract(data):
        rows = data.get("rows", [{}])
        if rows:
            r = rows[0]
            return {
                "clicks": int(r.get("clicks", 0)),
                "impressions": int(r.get("impressions", 0)),
                "ctr": r.get("ctr", 0),
                "position": r.get("position", 0),
            }
        return {"clicks": 0, "impressions": 0, "ctr": 0, "position": 0}

    tw = extract(this_week)
    pw = extract(prev_week)

    pages = []
    for row in top_pages.get("rows", [])[:5]:
        slug = row["keys"][0].replace("https://notes.editors.one/", "/")
        pages.append({
            "slug": slug,
            "impressions": int(row["impressions"]),
            "clicks": int(row["clicks"]),
            "position": round(row["position"], 1),
        })

    return {
        "this_week": tw,
        "prev_week": pw,
        "top_pages": pages,
    }


# --- Format message ---
def delta_arrow(current: int, previous: int) -> str:
    if previous == 0:
        return f"{current}" if current == 0 else f"{current} (new)"
    diff = current - previous
    pct = round(diff / previous * 100) if previous else 0
    if diff > 0:
        return f"{current} (+{pct}%)"
    elif diff < 0:
        return f"{current} ({pct}%)"
    return f"{current} (=)"


def format_report(yandex: dict, google: dict) -> str:
    lines = [
        f"**SEO-отчёт notes.editors.one** ({datetime.now().strftime('%d.%m.%Y')})",
        "",
        "**Яндекс**",
        f"ИКС: {yandex['sqi']} | Индекс: {yandex['indexed']} стр | Исключено: {yandex['excluded']}",
        f"Показы: {delta_arrow(yandex['this_week_shows'], yandex['prev_week_shows'])}",
        f"Клики: {delta_arrow(yandex['this_week_clicks'], yandex['prev_week_clicks'])}",
    ]
    if yandex["problems"]:
        lines.append(f"Проблемы: {', '.join(yandex['problems'])}")

    lines += [
        "",
        "**Google**",
        f"Показы: {delta_arrow(google['this_week']['impressions'], google['prev_week']['impressions'])}",
        f"Клики: {delta_arrow(google['this_week']['clicks'], google['prev_week']['clicks'])}",
        f"CTR: {google['this_week']['ctr']:.1%} | Ср. позиция: {google['this_week']['position']:.1f}",
    ]

    if google["top_pages"]:
        lines += ["", "Топ страницы (Google):"]
        for p in google["top_pages"]:
            lines.append(f"  [{p['position']}] {p['impressions']}i {p['clicks']}c — {p['slug']}")

    return "\n".join(lines)


# --- Send to Pachka ---
async def send_pachka(client: httpx.AsyncClient, message: str):
    resp = await client.post(
        f"{PACHCA_API_URL}/messages",
        headers={
            "Authorization": f"Bearer {PACHCA_TOKEN}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json={
            "message": {
                "entity_type": "discussion",
                "entity_id": PACHCA_CHAT_ID,
                "content": message,
            }
        },
    )
    resp.raise_for_status()
    logger.info("Pachka message sent: %s", resp.json().get("data", {}).get("id"))


# --- Main ---
async def main():
    async with httpx.AsyncClient(timeout=30) as client:
        yandex, google = await asyncio.gather(
            fetch_yandex(client),
            fetch_gsc(client),
        )

    report = format_report(yandex, google)
    logger.info("Report:\n%s", report)

    async with httpx.AsyncClient(timeout=10) as client:
        await send_pachka(client, report)


if __name__ == "__main__":
    asyncio.run(main())
