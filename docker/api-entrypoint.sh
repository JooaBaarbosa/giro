#!/bin/sh
set -e
alembic upgrade head
if [ "${SEED_DEMO:-false}" = "true" ]; then
  python -m giro.seed
fi
exec uvicorn giro.main:create_app --factory --host 0.0.0.0 --port 8000 --proxy-headers
