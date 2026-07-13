#!/usr/bin/env bash
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1"; exit 1; }

usage() {
    echo "Megatron v0.1.0"
    echo ""
    echo "Usage: bash deploy.sh [command]"
    echo ""
    echo "Commands:"
    echo "  deploy   Full deploy (check env, .env, build, start) [default]"
    echo "  update   Git pull + rebuild + restart"
    echo "  backup   Snapshot the data volume (DB + secrets) → backups/*.tar.gz"
    echo "  restore  Restore the data volume from a backup: restore <file.tar.gz>"
    echo "  reset-password  Reset the admin password (no data loss): reset-password [newpass]"
    echo "  clean    Stop containers and remove all data (DB, secrets, volumes)"
    echo "  logs     Show live logs"
    echo "  status   Show container status"
}

CMD="${1:-deploy}"
shift 2>/dev/null || true

# ── Shared setup ──────────────────────────────

_setup() {
    # Docker
    if ! command -v docker &>/dev/null; then err "Docker not found. curl -fsSL https://get.docker.com | sh"; fi
    if ! docker info &>/dev/null; then err "Docker daemon not running."; fi
    log "Docker OK"

    # Compose
    if docker compose version &>/dev/null 2>&1; then
        COMPOSE="docker compose"
    elif command -v docker-compose &>/dev/null; then
        COMPOSE="docker-compose"
    else
        err "Docker Compose not found. Install: https://docs.docker.com/compose/install/"
    fi
    log "Compose: $COMPOSE"
}

_env() {
    if [ ! -f .env ]; then
        SESSION_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))" 2>/dev/null || openssl rand -base64 48)
        ADMIN_PASS=$(python3 -c "import secrets; print(secrets.token_urlsafe(12))" 2>/dev/null || openssl rand -base64 12)
        INGEST_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))" 2>/dev/null || openssl rand -base64 48)

        cat > .env << EOF
# Four slashes = absolute path. Three would resolve relative to WORKDIR (/app)
# and land outside the mounted volume, so the DB would vanish on recreate.
MEGATRON_DATABASE_URL=sqlite+aiosqlite:////app/data/megatron.db
MEGATRON_SESSION_SECRET=${SESSION_SECRET}
MEGATRON_ADMIN_PASSWORD=${ADMIN_PASS}
MEGATRON_INGEST_TOKEN=${INGEST_TOKEN}
# Public domain (first-boot seed), pre-set to the production domain. It's a DB
# setting — change later in the UI (系统设置 → 域名) without editing env.
MEGATRON_BASE_URL=https://megatron.qshunter.top
MEGATRON_DEEPSEEK_API_KEY=
MEGATRON_DINGTALK_URL=
MEGATRON_DINGTALK_SECRET=
# Host publish port (container listens on 8000). 8010 keeps host :8000 free.
PORT=8010
EOF
        log "Admin password: ${ADMIN_PASS} (save this!)"
        warn "Fill in MEGATRON_DEEPSEEK_API_KEY and DINGTALK_* in .env"
        warn "起服务后在 UI「系统设置 → 域名」里填你的公网域名,推送链接才能在手机上打开。"
    else
        log ".env exists"
    fi
}

PORT() { grep -oP 'PORT=\K\d+' .env 2>/dev/null || echo 8010; }

_wait() {
    local port=$(PORT)
    for i in $(seq 1 30); do
        curl -sf http://localhost:${port}/health &>/dev/null && break
        sleep 1
    done
    if curl -sf http://localhost:${port}/health &>/dev/null; then
        log "Running at http://localhost:${port}"
    else
        warn "Check logs: $COMPOSE logs web"
    fi
}

# ── Commands ───────────────────────────────────

case "$CMD" in
deploy)
    _setup
    echo "  Disk: $(df -h . | tail -1 | awk '{print $4}') available"
    _env
    log "Stopping old containers..."
    $COMPOSE down 2>/dev/null || true
    log "Building..."
    $COMPOSE build --no-cache --quiet 2>&1 | tail -1
    log "Starting..."
    $COMPOSE up -d
    _wait
    ;;

update)
    _setup
    log "Pulling latest code..."
    git pull
    log "Stopping old containers..."
    $COMPOSE down 2>/dev/null || true
    log "Rebuilding..."
    $COMPOSE build --no-cache --quiet 2>&1 | tail -1
    log "Starting..."
    $COMPOSE up -d
    _wait
    ;;

clean)
    _setup
    warn "This will remove ALL data (database, secrets, volumes)."
    read -p "Are you sure? [y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        log "Cancelled."
        exit 0
    fi
    log "Stopping containers..."
    $COMPOSE down -v
    rm -f .env 2>/dev/null || true
    log "Cleaned. Run 'bash deploy.sh' to start fresh."
    ;;

logs)
    _setup
    $COMPOSE logs -f --tail 100
    ;;

status)
    _setup
    $COMPOSE ps
    ;;

backup)
    _setup
    # Resolve the real volume name from the container (compose prefixes it with
    # the project, so don't hard-code "megatron_data").
    VOL=$(docker inspect -f '{{ range .Mounts }}{{ if eq .Destination "/app/data" }}{{ .Name }}{{ end }}{{ end }}' megatron 2>/dev/null || true)
    [ -z "$VOL" ] && err "Container 'megatron' not found — deploy first."
    mkdir -p backups
    TS=$(date +%Y%m%d-%H%M%S)
    OUT="backups/megatron-${TS}.tar.gz"
    warn "Pausing the app briefly for a consistent SQLite snapshot..."
    $COMPOSE stop web >/dev/null 2>&1 || true
    docker run --rm -v "${VOL}":/data:ro -v "$(pwd)/backups":/backup alpine \
        tar czf "/backup/megatron-${TS}.tar.gz" -C /data . || err "Backup failed"
    $COMPOSE start web >/dev/null 2>&1 || $COMPOSE up -d
    log "Saved ${OUT} ($(du -h "$OUT" 2>/dev/null | cut -f1))"
    warn "Copy it off-box too (scp / object storage) — a local copy dies with the disk."
    ;;

restore)
    _setup
    FILE="${1:-}"
    if [ -z "$FILE" ]; then
        echo "Available backups:"; ls -1 backups/*.tar.gz 2>/dev/null || echo "  (none)"
        err "Usage: bash deploy.sh restore backups/megatron-YYYYMMDD-HHMMSS.tar.gz"
    fi
    [ -f "$FILE" ] || err "Not found: $FILE"
    VOL=$(docker inspect -f '{{ range .Mounts }}{{ if eq .Destination "/app/data" }}{{ .Name }}{{ end }}{{ end }}' megatron 2>/dev/null || true)
    [ -z "$VOL" ] && err "Container 'megatron' not found — deploy first (it creates the volume)."
    ABSDIR=$(cd "$(dirname "$FILE")" && pwd)
    BASE=$(basename "$FILE")
    warn "This OVERWRITES all current data in ${VOL} with ${FILE}."
    read -p "Continue? [y/N] " -n 1 -r; echo
    [[ $REPLY =~ ^[Yy]$ ]] || { log "Cancelled."; exit 0; }
    $COMPOSE stop web >/dev/null 2>&1 || true
    docker run --rm -v "${VOL}":/data -v "${ABSDIR}":/backup alpine \
        sh -c "rm -rf /data/* /data/..?* 2>/dev/null; tar xzf /backup/${BASE} -C /data" || err "Restore failed"
    $COMPOSE start web >/dev/null 2>&1 || $COMPOSE up -d
    log "Restored from ${FILE}. Verify: bash deploy.sh logs"
    ;;

reset-password)
    _setup
    # Locked out? Reset admin in the live DB without wiping anything.
    NEWPASS="${1:-}"
    if [ -n "$NEWPASS" ]; then
        $COMPOSE exec -T web python -m megatron.main reset-password --password "$NEWPASS"
    else
        $COMPOSE exec -T web python -m megatron.main reset-password
    fi
    ;;

*)
    usage
    exit 1
    ;;
esac
