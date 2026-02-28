#!/bin/bash
set -euo pipefail

SERVER="root@95.163.230.43"
ENV="${1:-prod}"
FORCE="${2:-}"

# Git checks
if [[ -n $(git status --porcelain) ]]; then
    if [[ "$FORCE" != "--force" ]]; then
        echo "ERROR: Uncommitted changes. Commit first or use --force"
        exit 1
    fi
    echo "WARNING: Deploying with uncommitted changes (--force). Rollback unavailable."
fi

git fetch origin --quiet
if ! git diff --quiet HEAD origin/main 2>/dev/null; then
    if [[ "$FORCE" != "--force" ]]; then
        echo "ERROR: Local HEAD differs from origin/main. Push first or use --force"
        exit 1
    fi
fi

# Deploy tag (only if clean)
if [[ -z $(git status --porcelain) ]]; then
    TAG="deploy-$(date +%Y%m%d-%H%M%S)"
    git tag -a "$TAG" -m "Deploy $ENV from $(git rev-parse --short HEAD)"
    echo "Tagged: $TAG"
fi

case "$ENV" in
  prod)
    echo "=== Deploying PROD (main) ==="
    ssh $SERVER "cd /opt/seo-blog && git pull origin main && docker compose build api && docker compose up -d api"
    # Healthcheck
    sleep 3
    if ssh $SERVER "curl -fsS http://localhost:8000/health >/dev/null 2>&1"; then
        echo "Health OK"
    else
        echo "Health FAILED — check logs: ssh $SERVER 'docker compose -f /opt/seo-blog/docker-compose.yml logs api --tail 50'"
        exit 1
    fi
    ;;
  staging)
    echo "=== Deploying STAGING (dev) ==="
    ssh $SERVER "cd /opt/seo-blog-staging && git pull origin dev && docker compose -f docker-compose.staging.yml build api-staging && docker compose -f docker-compose.staging.yml up -d api-staging"
    echo "Staging deployed (no healthcheck)"
    ;;
  *)
    echo "Usage: ./deploy.sh [prod|staging] [--force]"
    exit 1
    ;;
esac

echo "Deployed successfully."
