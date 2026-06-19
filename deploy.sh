#!/usr/bin/env bash
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1"; exit 1; }

echo "============================================"
echo "  Megatron v0.1.0 Deployment"
echo "============================================"
echo ""

# 1. Docker
if ! command -v docker &>/dev/null; then err "Docker not found. curl -fsSL https://get.docker.com | sh"; fi
if ! docker info &>/dev/null; then err "Docker daemon not running."; fi
log "Docker OK"

# 2. Docker Compose
COMPOSE="docker compose"
docker compose version &>/dev/null || COMPOSE="docker-compose"
log "Compose OK"

# 3. Disk
log "Disk: $(df -h . | tail -1 | awk '{print $4}') available"

# 4. .env
if [ ! -f .env ]; then
    SESSION_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))" 2>/dev/null || openssl rand -base64 48)
    ADMIN_PASS=$(python3 -c "import secrets; print(secrets.token_urlsafe(12))" 2>/dev/null || openssl rand -base64 12)
    
    cat > .env << EOF
DATABASE_URL=sqlite+aiosqlite:///./megatron.db
MEGATRON_SESSION_SECRET=${SESSION_SECRET}
MEGATRON_ADMIN_PASSWORD=${ADMIN_PASS}
MEGATRON_DEEPSEEK_API_KEY=
MEGATRON_DINGTALK_URL=
MEGATRON_DINGTALK_SECRET=
PORT=8000
EOF
    log "Admin password: ${ADMIN_PASS} (save this!)"
    warn "Fill in MEGATRON_DEEPSEEK_API_KEY and DINGTALK_* in .env"
else
    log ".env exists, skipping generation."
    warn "To reconfigure, delete .env and re-run deploy.sh"
fi

# 5. Build & Run
log "Building..."
$COMPOSE build --quiet 2>&1 | tail -1

log "Starting..."
$COMPOSE up -d

for i in $(seq 1 30); do
    curl -sf http://localhost:${PORT:-8000}/health &>/dev/null && break
    sleep 1
done

if curl -sf http://localhost:${PORT:-8000}/health &>/dev/null; then
    log "Running at http://localhost:${PORT:-8000}"
    echo "  Login: http://localhost:${PORT:-8000}/ui/login  (admin / password above)"
    echo "  Test:  http://localhost:${PORT:-8000}/ui/tasks  → 点击 Run 手动执行"
else
    warn "Check logs: $COMPOSE logs web"
fi
