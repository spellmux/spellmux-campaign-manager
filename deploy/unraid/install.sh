#!/usr/bin/env bash
set -euo pipefail

MODE="${1:---check}"
if [[ "$MODE" != "--check" && "$MODE" != "--apply" && "$MODE" != "--resume" ]]; then
  echo "Usage: install.sh [--check|--apply|--resume]" >&2
  exit 2
fi

APP_ROOT="${CAMPAIGN_APP_ROOT:-/mnt/user/appdata/campaign-manager}"
ARTIFACT_ROOT="${CAMPAIGN_ARTIFACT_ROOT:-$APP_ROOT/artifacts}"
PUBLISH_ROOT="${CAMPAIGN_PUBLISH_ROOT:-$APP_ROOT/publish}"
DATABASE_ROOT="${CAMPAIGN_DATABASE_ROOT:-$APP_ROOT/postgres}"
SECRETS_FILE="${CAMPAIGN_SECRETS_FILE:-$APP_ROOT/secrets.env}"
NETWORK="${CAMPAIGN_NETWORK:-campaign-manager}"
HTTP_PORT="${CAMPAIGN_HTTP_PORT:-8088}"
APP_IMAGE="${CAMPAIGN_IMAGE:-campaign-manager:dev}"
POSTGRES_IMAGE="${CAMPAIGN_POSTGRES_IMAGE:-postgres:17-alpine}"
DATABASE_CONTAINER="campaign-manager-database"
SERVER_CONTAINER="campaign-manager-server"
WORKER_CONTAINER="campaign-manager-worker"

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Required command not found: $1" >&2
    exit 1
  fi
}

container_exists() {
  docker container inspect "$1" >/dev/null 2>&1
}

port_in_use() {
  ss -ltnH | awk '{print $4}' | grep -Eq "(^|:)$HTTP_PORT$"
}

print_plan() {
  cat <<EOF
Campaign Manager Unraid installation plan

Mode:              $MODE
Application image: $APP_IMAGE
Postgres image:    $POSTGRES_IMAGE
Docker network:    $NETWORK
HTTP port:         $HTTP_PORT
Application root:  $APP_ROOT
Artifact root:     $ARTIFACT_ROOT
Publish staging:   $PUBLISH_ROOT
Database root:     $DATABASE_ROOT
Secrets file:      $SECRETS_FILE
Containers:
  - $DATABASE_CONTAINER
  - $SERVER_CONTAINER
  - $WORKER_CONTAINER

OtterWiki and Plex will not be modified.
The OtterWiki repository will not be mounted.
EOF
}

preflight() {
  require_command docker
  require_command ss
  require_command openssl

  if [[ ! -d /mnt/user/appdata ]]; then
    echo "Expected Unraid appdata path does not exist: /mnt/user/appdata" >&2
    exit 1
  fi
  if ! docker image inspect "$APP_IMAGE" >/dev/null 2>&1; then
    echo "Application image is not available locally: $APP_IMAGE" >&2
    exit 1
  fi
  if port_in_use; then
    echo "TCP port $HTTP_PORT is already in use" >&2
    exit 1
  fi
  if [[ "$MODE" == "--resume" ]]; then
    if ! container_exists "$DATABASE_CONTAINER"; then
      echo "Cannot resume without container: $DATABASE_CONTAINER" >&2
      exit 1
    fi
    if [[ ! -f "$SECRETS_FILE" ]]; then
      echo "Cannot resume without secrets file: $SECRETS_FILE" >&2
      exit 1
    fi
    for container in "$SERVER_CONTAINER" "$WORKER_CONTAINER"; do
      if container_exists "$container"; then
        echo "Refusing to replace existing container while resuming: $container" >&2
        exit 1
      fi
    done
    return
  fi
  for container in "$DATABASE_CONTAINER" "$SERVER_CONTAINER" "$WORKER_CONTAINER"; do
    if container_exists "$container"; then
      echo "Refusing to replace existing container: $container" >&2
      exit 1
    fi
  done
  if [[ -e "$SECRETS_FILE" ]]; then
    echo "Refusing to replace existing secrets file: $SECRETS_FILE" >&2
    exit 1
  fi
}

wait_for_database() {
  for _attempt in $(seq 1 60); do
    if docker exec "$DATABASE_CONTAINER" pg_isready -U campaign -d campaign >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  echo "Database did not become ready within 120 seconds" >&2
  docker logs "$DATABASE_CONTAINER" >&2
  return 1
}

load_database_password() {
  local line
  IFS= read -r line < "$SECRETS_FILE"
  if [[ "$line" != CAMPAIGN_DATABASE_PASSWORD=* ]]; then
    echo "Invalid secrets file format: $SECRETS_FILE" >&2
    return 1
  fi
  printf '%s' "${line#CAMPAIGN_DATABASE_PASSWORD=}"
}

finish_installation() {
  local database_password="$1"
  local database_url

  wait_for_database
  database_url="postgresql+psycopg://campaign:$database_password@$DATABASE_CONTAINER:5432/campaign"

  docker run --rm \
    --network "$NETWORK" \
    --env CAMPAIGN_DATABASE_URL="$database_url" \
    "$APP_IMAGE" campaignctl migrate

  docker run --detach \
    --name "$SERVER_CONTAINER" \
    --network "$NETWORK" \
    --restart unless-stopped \
    --publish "$HTTP_PORT:8088" \
    --env CAMPAIGN_ENV=production \
    --env CAMPAIGN_DATABASE_URL="$database_url" \
    --env CAMPAIGN_ARTIFACT_ROOT=/data/artifacts \
    --env CAMPAIGN_PUBLISH_ROOT=/data/publish \
    --volume "$ARTIFACT_ROOT:/data/artifacts" \
    --volume "$PUBLISH_ROOT:/data/publish" \
    --health-cmd "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8088/api/v1/health')\"" \
    --health-interval 30s \
    --health-timeout 5s \
    --health-retries 3 \
    "$APP_IMAGE" >/dev/null

  docker run --detach \
    --name "$WORKER_CONTAINER" \
    --network "$NETWORK" \
    --restart unless-stopped \
    --env CAMPAIGN_ENV=production \
    --env CAMPAIGN_DATABASE_URL="$database_url" \
    --env CAMPAIGN_ARTIFACT_ROOT=/data/artifacts \
    --env CAMPAIGN_PUBLISH_ROOT=/data/publish \
    --volume "$ARTIFACT_ROOT:/data/artifacts" \
    --volume "$PUBLISH_ROOT:/data/publish" \
    "$APP_IMAGE" campaign-worker >/dev/null

  echo
  echo "Installation completed."
  echo "Health: http://$(hostname -I | awk '{print $1}'):$HTTP_PORT/api/v1/health"
  echo "Create the first administrator interactively with:"
  echo "docker exec -it $SERVER_CONTAINER campaignctl create-admin --email YOUR_EMAIL --name YOUR_NAME"
}

apply_installation() {
  install -d -m 0750 -o 99 -g 100 "$APP_ROOT" "$ARTIFACT_ROOT" "$PUBLISH_ROOT"
  # postgres:alpine drops to its internal postgres account (UID/GID 70).
  # The bind mount itself must therefore be traversable by that account.
  install -d -m 0700 -o 70 -g 70 "$DATABASE_ROOT"

  database_password="$(openssl rand -hex 32)"
  umask 077
  printf 'CAMPAIGN_DATABASE_PASSWORD=%s\n' "$database_password" > "$SECRETS_FILE"

  if ! docker network inspect "$NETWORK" >/dev/null 2>&1; then
    docker network create "$NETWORK" >/dev/null
  fi

  docker pull "$POSTGRES_IMAGE"
  docker run --detach \
    --name "$DATABASE_CONTAINER" \
    --network "$NETWORK" \
    --restart unless-stopped \
    --env POSTGRES_DB=campaign \
    --env POSTGRES_USER=campaign \
    --env "POSTGRES_PASSWORD=$database_password" \
    --env PGDATA=/var/lib/postgresql/data/pgdata \
    --volume "$DATABASE_ROOT:/var/lib/postgresql/data" \
    "$POSTGRES_IMAGE" >/dev/null

  finish_installation "$database_password"
}

print_plan
preflight
if [[ "$MODE" == "--check" ]]; then
  echo
  echo "Preflight passed. No changes were made."
  exit 0
fi
if [[ "$MODE" == "--resume" ]]; then
  finish_installation "$(load_database_password)"
else
  apply_installation
fi
