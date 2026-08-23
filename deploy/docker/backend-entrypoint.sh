#!/usr/bin/env sh
set -eu

cd /opt/backend

export PYTHONPATH=/opt/backend
export DJANGO_SETTINGS_MODULE=project.settings

ensure_log_dir() {
  if [ -n "${LOG_FILE:-}" ]; then
    dir="$(dirname "${LOG_FILE}")"
    mkdir -p "${dir}"
    touch "${LOG_FILE}" || true
  fi
}

wait_for_postgres() {
  host="${POSTGRES_HOST:-postgres}"
  port="${POSTGRES_PORT:-5432}"
  if [ "${HFL_MIGRATION_OUTPUT:-verbose}" = "compact" ]; then
    printf 'HFL_MIGRATION_EVENT\tSTEP\tWaiting for PostgreSQL at %s:%s\n' "${host}" "${port}"
  else
    echo "[entrypoint] waiting for postgres at ${host}:${port}"
  fi
  until python -c "import socket; s=socket.socket(); s.settimeout(1); s.connect(('${host}', int('${port}'))); s.close()" 2>/dev/null; do
    sleep 2
  done
  if [ "${HFL_MIGRATION_OUTPUT:-verbose}" = "compact" ]; then
    printf 'HFL_MIGRATION_EVENT\tOK\tPostgreSQL is reachable\n'
  else
    echo "[entrypoint] postgres is reachable"
  fi
}

emit_migration_event() {
  level="$1"
  message="$2"
  if [ "${HFL_MIGRATION_OUTPUT:-verbose}" = "compact" ]; then
    printf 'HFL_MIGRATION_EVENT\t%s\t%s\n' "${level}" "${message}"
  else
    printf '%s\n' "${message}"
  fi
}

emit_migration_command_failure() {
  label="$1"
  output_file="$2"
  emit_migration_event FAIL "${label} failed"
  # Successful compact runs are intentionally quiet. A failed command keeps
  # the complete Django/entrypoint transcript for diagnosis.
  cat "${output_file}"
}

persist_migration_output() {
  output_file="$1"
  if [ -n "${LOG_FILE:-}" ] && [ -f "${output_file}" ]; then
    cat "${output_file}" >>"${LOG_FILE}" 2>/dev/null || true
  fi
}

run_migrations_and_register() {
  compact="${HFL_MIGRATION_OUTPUT:-verbose}"
  if [ "${compact}" = "compact" ]; then
    emit_migration_event STEP "Applying database migrations"
  else
    echo "[entrypoint] migrate"
  fi

  migrate_output="$(mktemp)"
  if python manage.py migrate --noinput >"${migrate_output}" 2>&1; then
    persist_migration_output "${migrate_output}"
    if [ "${compact}" = "compact" ]; then
      if grep -F "No migrations to apply" "${migrate_output}" >/dev/null 2>&1; then
        emit_migration_event OK "Database schema is current · no migrations pending"
      else
        applied_count="$(grep -cE 'Applying .*\.\.\. OK$' "${migrate_output}" 2>/dev/null || true)"
        if [ "${applied_count}" -gt 0 ] 2>/dev/null; then
          emit_migration_event OK "Database migrations applied · ${applied_count} migration(s)"
        else
          emit_migration_event OK "Database migrations completed"
        fi
      fi
    else
      cat "${migrate_output}"
    fi
  else
    persist_migration_output "${migrate_output}"
    emit_migration_command_failure "Database migration" "${migrate_output}"
    rm -f "${migrate_output}"
    return 1
  fi
  rm -f "${migrate_output}"

  if [ "${compact}" = "compact" ]; then
    emit_migration_event STEP "Collecting Django Admin assets"
  else
    echo "[entrypoint] collectstatic (Django Admin assets only; SPA uses /assets/)"
  fi
  collectstatic_output="$(mktemp)"
  if python manage.py collectstatic --noinput --clear >"${collectstatic_output}" 2>&1; then
    persist_migration_output "${collectstatic_output}"
    # Clearing stale admin assets is intentional, but printing every removed
    # filename makes development and upgrade logs unreadable. Keep the final
    # copy summary and suppress only routine successful deletion details.
    if [ "${compact}" = "compact" ]; then
      static_summary="$(grep -E '[0-9]+ static files copied to' "${collectstatic_output}" | tail -1 || true)"
      if [ -n "${static_summary}" ]; then
        static_summary="${static_summary%% to *}"
        emit_migration_event OK "Django Admin assets are ready · ${static_summary}"
      else
        emit_migration_event OK "Django Admin assets are ready"
      fi
    else
      sed '/^[[:space:]]*Deleting /d' "${collectstatic_output}"
    fi
    rm -f "${collectstatic_output}"
  else
    persist_migration_output "${collectstatic_output}"
    if [ "${compact}" = "compact" ]; then
      emit_migration_event WARN "Django Admin asset cleanup could not be completed; retrying without cleanup"
    else
      cat "${collectstatic_output}"
    fi
    rm -f "${collectstatic_output}"
    if [ "${compact}" = "compact" ]; then
      collectstatic_retry_output="$(mktemp)"
      if python manage.py collectstatic --noinput >"${collectstatic_retry_output}" 2>&1; then
        persist_migration_output "${collectstatic_retry_output}"
        static_summary="$(grep -E '[0-9]+ static files copied to' "${collectstatic_retry_output}" | tail -1 || true)"
        if [ -n "${static_summary}" ]; then
          static_summary="${static_summary%% to *}"
          emit_migration_event OK "Django Admin assets are ready · ${static_summary}"
        else
          emit_migration_event OK "Django Admin assets are ready"
        fi
        rm -f "${collectstatic_retry_output}"
      else
        persist_migration_output "${collectstatic_retry_output}"
        emit_migration_command_failure "Django Admin asset collection" "${collectstatic_retry_output}"
        rm -f "${collectstatic_retry_output}"
        return 1
      fi
    fi
    if [ "${compact}" != "compact" ]; then
      python manage.py collectstatic --noinput
    fi
  fi

  if [ "${compact}" = "compact" ]; then
    emit_migration_event STEP "Registering periodic tasks"
  else
    echo "[entrypoint] register periodic tasks"
  fi
  periodic_output="$(mktemp)"
  if python manage.py register_periodic_tasks >"${periodic_output}" 2>&1; then
    persist_migration_output "${periodic_output}"
    if [ "${compact}" = "compact" ]; then
      periodic_summary="$(grep -E 'Registered [0-9]+ periodic task' "${periodic_output}" | tail -1 || true)"
      if [ -n "${periodic_summary}" ]; then
        emit_migration_event OK "${periodic_summary%.}"
      else
        emit_migration_event OK "Periodic tasks are ready"
      fi
    else
      cat "${periodic_output}"
    fi
  else
    persist_migration_output "${periodic_output}"
    if [ "${compact}" = "compact" ]; then
      emit_migration_event WARN "Periodic task registration was not completed"
      cat "${periodic_output}"
    else
      cat "${periodic_output}"
    fi
  fi
  rm -f "${periodic_output}"

  if [ "${SEED_INITIAL_DATA:-0}" = "1" ]; then
    if [ "${compact}" = "compact" ]; then
      emit_migration_event STEP "Synchronizing initial data"
    else
      echo "[entrypoint] seed initial data"
    fi
    seed_output="$(mktemp)"
    if python manage.py seed_initial_data \
      --org-name "${SEED_ORG_NAME:-HyperFileLens}" \
      --admin-email "${SEED_ADMIN_EMAIL:-admin@hyperfilelens.com}" \
      --admin-password "${SEED_ADMIN_PASSWORD:-Admin@123}" >"${seed_output}" 2>&1; then
      persist_migration_output "${seed_output}"
      if [ "${compact}" = "compact" ]; then
        emit_migration_event OK "Initial data is synchronized · ${SEED_ORG_NAME:-HyperFileLens}"
      else
        cat "${seed_output}"
      fi
    else
      persist_migration_output "${seed_output}"
      if [ "${compact}" = "compact" ]; then
        emit_migration_event WARN "Initial data synchronization was not completed"
        cat "${seed_output}"
      else
        cat "${seed_output}"
      fi
    fi
    rm -f "${seed_output}"
  elif [ "${compact}" = "compact" ]; then
    emit_migration_event OK "Initial data synchronization not requested"
  fi
}

# Gunicorn (:8000) + Daphne (:8001) in one container; this shell stays PID 1 for SIGTERM.
run_api_stack() {
  API_WORKERS="${API_WORKERS:-4}"
  WS_BIND_HOST="${WS_BIND_HOST:-0.0.0.0}"
  WS_BIND_PORT="${WS_BIND_PORT:-8001}"
  DAPHNE_PID=""
  GUNICORN_PID=""

  api_stack_cleanup() {
    if [ -n "${DAPHNE_PID}" ]; then
      kill -TERM "${DAPHNE_PID}" 2>/dev/null || true
      wait "${DAPHNE_PID}" 2>/dev/null || true
    fi
    if [ -n "${GUNICORN_PID}" ]; then
      kill -TERM "${GUNICORN_PID}" 2>/dev/null || true
      wait "${GUNICORN_PID}" 2>/dev/null || true
    fi
  }

  trap api_stack_cleanup INT TERM

  echo "[entrypoint] start daphne on ${WS_BIND_HOST}:${WS_BIND_PORT}"
  daphne -b "${WS_BIND_HOST}" -p "${WS_BIND_PORT}" project.asgi_ws:application &
  DAPHNE_PID=$!

  echo "[entrypoint] wait for websocket delivery route"
  if ! python manage.py ws_recovery_gate complete \
    --host 127.0.0.1 \
    --port "${WS_BIND_PORT}" \
    --timeout "${WS_READY_TIMEOUT_SECONDS:-60}"; then
    api_stack_cleanup
    exit 1
  fi

  echo "[entrypoint] start gunicorn (${API_WORKERS} workers) on 0.0.0.0:8000"
  GUNICORN_TIMEOUT="${GUNICORN_TIMEOUT:-180}"
  gunicorn -w "${API_WORKERS}" \
    --timeout "${GUNICORN_TIMEOUT}" \
    -k uvicorn.workers.UvicornWorker \
    -b 0.0.0.0:8000 \
    project.asgi_http:application &
  GUNICORN_PID=$!

  while kill -0 "${GUNICORN_PID}" 2>/dev/null && kill -0 "${DAPHNE_PID}" 2>/dev/null; do
    sleep 1
  done
  if ! kill -0 "${DAPHNE_PID}" 2>/dev/null; then
    echo "[entrypoint] daphne exited; closing API stack" >&2
    EXIT=1
  else
    wait "${GUNICORN_PID}" || EXIT=$?
    EXIT=${EXIT:-0}
  fi
  api_stack_cleanup
  exit "${EXIT}"
}

require_watchfiles() {
  command -v watchfiles >/dev/null 2>&1 || {
    echo "[entrypoint] watchfiles is required for development commands" >&2
    exit 2
  }
}

DEV_WATCH_IGNORE_PATHS="/opt/backend/media,/opt/backend/staticfiles,/opt/backend/lang-packs"

run_api_dev() {
  ensure_log_dir
  wait_for_postgres
  require_watchfiles
  echo "[entrypoint] supervise backend HTTP/WebSocket API with hot reload"
  exec python /dev-process-supervisor.py \
    --watch /opt/backend \
    --ignore /opt/backend/media \
    --ignore /opt/backend/staticfiles \
    --ignore /opt/backend/lang-packs \
    --max-restarts "${DEV_API_MAX_RESTARTS:-5}" \
    --stable-seconds "${DEV_API_STABLE_SECONDS:-30}" \
    --base-delay "${DEV_API_RESTART_DELAY_SECONDS:-1}" \
    -- /entrypoint.sh api
}

run_worker_dev() {
  ensure_log_dir
  wait_for_postgres
  require_watchfiles
  echo "[entrypoint] supervise celery worker with hot reload"
  exec python /dev-process-supervisor.py \
    --watch /opt/backend \
    --ignore /opt/backend/media \
    --ignore /opt/backend/staticfiles \
    --ignore /opt/backend/lang-packs \
    --max-restarts "${DEV_WORKER_MAX_RESTARTS:-5}" \
    --stable-seconds "${DEV_WORKER_STABLE_SECONDS:-30}" \
    --base-delay "${DEV_WORKER_RESTART_DELAY_SECONDS:-1}" \
    -- celery -A common worker --loglevel=INFO \
    --concurrency="${CELERY_WORKER_CONCURRENCY:-4}" \
    -Q backend,node.lifecycle,node.ingest,source.remote-io,storage.provider-validation
}

run_scheduler_dev() {
  ensure_log_dir
  wait_for_postgres
  require_watchfiles
  echo "[entrypoint] supervise cluster-safe celery scheduler with hot reload"
  exec python /dev-process-supervisor.py \
    --watch /opt/backend \
    --ignore /opt/backend/media \
    --ignore /opt/backend/staticfiles \
    --ignore /opt/backend/lang-packs \
    --max-restarts "${DEV_SCHEDULER_MAX_RESTARTS:-5}" \
    --stable-seconds "${DEV_SCHEDULER_STABLE_SECONDS:-30}" \
    --base-delay "${DEV_SCHEDULER_RESTART_DELAY_SECONDS:-1}" \
    -- python manage.py run_scheduler_leader
}

case "${1:-api}" in
  migrate)
    # Singleton installer job: docker compose --profile tools run --rm migration
    ensure_log_dir
    wait_for_postgres
    run_migrations_and_register
    if [ "${HFL_MIGRATION_OUTPUT:-verbose}" = "compact" ]; then
      emit_migration_event OK "Database initialization completed"
    else
      echo "[entrypoint] migrations complete"
    fi
    exit 0
    ;;
  api)
    ensure_log_dir
    wait_for_postgres
    run_api_stack
    ;;
  api-dev)
    run_api_dev
    ;;
  worker)
    ensure_log_dir
    wait_for_postgres
    echo "[entrypoint] start celery worker"
    exec celery -A common worker --loglevel=INFO \
      --concurrency="${CELERY_WORKER_CONCURRENCY:-4}" \
      -Q backend,node.lifecycle,node.ingest,source.remote-io,storage.provider-validation
    ;;
  worker-dev)
    run_worker_dev
    ;;
  scheduler)
    ensure_log_dir
    wait_for_postgres
    echo "[entrypoint] start cluster-safe celery scheduler leader"
    exec python manage.py run_scheduler_leader
    ;;
  scheduler-dev)
    run_scheduler_dev
    ;;
  *)
    exec "$@"
    ;;
esac
