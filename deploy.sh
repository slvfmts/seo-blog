#!/bin/bash
set -e

SERVER="root@95.163.230.43"

case "${1:-prod}" in
  prod)
    echo "=== Deploying PROD (main) ==="
    ssh $SERVER "cd /opt/seo-blog && git pull origin main && docker compose build api && docker compose up -d api"
    ;;
  staging)
    echo "=== Deploying STAGING (dev) ==="
    ssh $SERVER "cd /opt/seo-blog-staging && git pull origin dev && docker compose -f docker-compose.staging.yml build api-staging && docker compose -f docker-compose.staging.yml up -d api-staging"
    ;;
  *)
    echo "Usage: ./deploy.sh [prod|staging]"
    exit 1
    ;;
esac
