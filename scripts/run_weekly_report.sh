#!/bin/bash
# Weekly SEO report runner — called by cron.
# Sources .env, copies script into container, runs with env vars.
set -e
cd /opt/seo-blog

# Source env vars from .env
set -a
source .env
set +a

# Copy script into container (persists until restart, harmless)
docker cp scripts/weekly_seo_report.py seo-blog-api-1:/tmp/weekly_seo_report.py

# Run with only the needed env vars
docker compose exec -T \
  -e YANDEX_WEBMASTER_TOKEN="$YANDEX_WEBMASTER_TOKEN" \
  -e GSC_CLIENT_ID="$GSC_CLIENT_ID" \
  -e GSC_CLIENT_SECRET="$GSC_CLIENT_SECRET" \
  -e GSC_REFRESH_TOKEN="$GSC_REFRESH_TOKEN" \
  -e ALERT_PACHCA_TOKEN="$ALERT_PACHCA_TOKEN" \
  api python3 /tmp/weekly_seo_report.py
