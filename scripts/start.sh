#!/bin/sh
set -eu

# In the current single-instance deployment the app owns migrations. Keep this
# switch so Dokploy can later split web and scheduler: only one service needs to
# run Alembic then.
if [ "${MEGATRON_RUN_MIGRATIONS:-1}" = "1" ]; then
  alembic upgrade head
fi

# exec makes uvicorn PID 1. Dokploy's SIGTERM then reaches it directly, letting
# FastAPI run its lifespan shutdown (scheduler shutdown + DB disposal).
exec python -m uvicorn megatron.web.app:app \
  --host 0.0.0.0 \
  --port 8000 \
  --proxy-headers \
  --forwarded-allow-ips="${MEGATRON_FORWARDED_ALLOW_IPS:-*}"
