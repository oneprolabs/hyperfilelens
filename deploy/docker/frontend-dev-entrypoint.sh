#!/usr/bin/env sh
set -eu

cd /app

lock_sha="$(sha256sum package-lock.json | awk '{print $1}')"
installed_sha="$(cat node_modules/.hfl-package-lock.sha 2>/dev/null || true)"

if [ "${lock_sha}" != "${installed_sha}" ]; then
  echo "[frontend-dev] package-lock.json changed; refreshing node_modules"
  npm ci
  printf '%s\n' "${lock_sha}" > node_modules/.hfl-package-lock.sha
fi

nginx

exec npm run dev -- --host 0.0.0.0
