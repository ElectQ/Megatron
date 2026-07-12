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

        # Asked, not defaulted: every pushed message ends in a link to the day
        # page built from this. A localhost default would send briefs whose only
        # way in is dead on the reader's phone — and prod now refuses to boot on
        # one rather than let that happen quietly.
        printf "Public address of this install (e.g. https://megatron.example.com): "
        read -r BASE_URL
        while [ -z "${BASE_URL}" ] || echo "${BASE_URL}" | grep -qE '://(localhost|127\.0\.0\.1|0\.0\.0\.0)'; do
            warn "Links in the push must open from a phone — a loopback address will not."
            printf "Public address (scheme + host, e.g. https://megatron.example.com): "
            read -r BASE_URL
        done

        cat > .env << EOF
# Four slashes = absolute path. Three would resolve relative to WORKDIR (/app)
# and land outside the mounted volume, so the DB would vanish on recreate.
MEGATRON_DATABASE_URL=sqlite+aiosqlite:////app/data/megatron.db
MEGATRON_SESSION_SECRET=${SESSION_SECRET}
MEGATRON_ADMIN_PASSWORD=${ADMIN_PASS}
MEGATRON_INGEST_TOKEN=${INGEST_TOKEN}
# Where readers reach this install. Every "查看今日详情" link is built from it,
# so it must be an address that resolves from outside this box.
MEGATRON_BASE_URL=${BASE_URL}
MEGATRON_DEEPSEEK_API_KEY=
MEGATRON_DINGTALK_URL=
MEGATRON_DINGTALK_SECRET=
PORT=8000
EOF
        log "Admin password: ${ADMIN_PASS} (save this!)"
        log "Base URL: ${BASE_URL}"
        warn "Fill in MEGATRON_DEEPSEEK_API_KEY and DINGTALK_* in .env"
    else
        log ".env exists"
        if grep -qE '^MEGATRON_BASE_URL=.*(localhost|127\.0\.0\.1)' .env; then
            warn "MEGATRON_BASE_URL is a loopback address — the day-page link in every"
            warn "pushed message will be dead on the reader's phone, and MEGATRON_ENV=prod"
            warn "will refuse to start. Point it at the address readers actually use."
        fi
    fi
}

PORT() { grep -oP 'PORT=\K\d+' .env 2>/dev/null || echo 8000; }

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

*)
    usage
    exit 1
    ;;
esac
