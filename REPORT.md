# Sunny SCADA Backend - Cycle 2 Completion Report

## Current architecture summary (≤20 lines)
- FastAPI app (`main.py` → `sunny_scada/api/app.py`) serves REST APIs and static frontend.
- PLC reads: `PollingService` calls `PLCReader` which uses `ModbusService` (pymodbus) and stores latest values in `DataStorage`.
- Existing `/plc_data` endpoint returns the current `DataStorage` snapshot (unchanged).
- PLC writes: new command pipeline (`/commands`) validates writes against `data_points.yaml` and executes asynchronously per-PLC.
- `data_points.yaml` is edited via config-admin APIs using ruamel.yaml round-trip + file-lock + atomic writes.
- Auth: JWT access tokens + refresh tokens (DB) with Argon2 password hashing + lockout.
- RBAC: roles/permissions with wildcard expansion (e.g., `alarms:*`).
- Persistence: SQLAlchemy ORM; Alembic migrations in `alembic/`.
- Logs: server/client logs, command log, alarm log, audit log stored in DB with query APIs.
- Historian: background sampling from in-memory storage snapshot + hourly rollups; trends query APIs.
- Maintenance (CMMS-lite): equipment, vendors, spare parts, breakdowns, schedules, work orders.
- Scheduler: APScheduler (optional) for retention cleanup, historian sampling/rollups, and maintenance schedule generation.

## Implementation notes / key decisions
- Kept the existing polling/reader/storage architecture and preserved `/plc_data` response shape.
- Implemented “write safety” by mapping all writes to configured `write:` datapoints in `data_points.yaml` only; no arbitrary register/address writes.
- Implemented per-PLC serialized write execution using a per-PLC queue + worker thread (non-blocking HTTP).
- Implemented process-local rate limiting (safe default). For multi-instance deployments, swap to Redis.
- Used ruamel.yaml + portalocker + atomic temp-file rename for safe YAML round-trip edits.
- Used DB audit logging for all config/admin/command/alarm actions; config revisions stored with before/after YAML snapshots and diffs.
- Historian stores numeric INTEGER/REAL values from current storage snapshot; aggregation uses hourly rollup table.
- Maintenance scheduling is “tick based” (interval/cron) using croniter; generates work orders with concurrency-safe codes.
- Secure defaults: CORS origins default to empty (same-origin). Set explicit origins via env.

## Files changed list
### Added
- `sunny_scada/api/errors.py`
- `sunny_scada/api/middleware.py`
- `sunny_scada/api/routers/commands.py`
- `sunny_scada/api/routers/logs.py`
- `sunny_scada/api/routers/alarms.py`
- `sunny_scada/api/routers/admin.py`
- `sunny_scada/api/routers/maintenance.py`
- `sunny_scada/api/routers/trends.py`
- `sunny_scada/services/rate_limiter.py`
- `sunny_scada/services/command_executor.py`
- `sunny_scada/services/command_service.py`
- `sunny_scada/services/retention_service.py`
- `sunny_scada/services/historian_service.py`
- `sunny_scada/services/maintenance_scheduler.py`
- `sunny_scada/services/db_log_handler.py`
- `alembic.ini`
- `alembic/env.py`
- `alembic/versions/*_initial_schema.py`
- `tests/*`
- `REPORT.md`

### Modified
- `sunny_scada/api/app.py`
- `sunny_scada/api/deps.py`
- `sunny_scada/api/routers/__init__.py`
- `sunny_scada/api/routers/plc.py` (write endpoint queues a command; still returns `message`)
- `sunny_scada/db/models.py` (added Cycle 2 tables)
- `sunny_scada/core/settings.py` (new env vars)
- `requirements.txt`

### Removed
- None

## How to run
### Dependencies
```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Required environment variables
- `JWT_SECRET_KEY` (required)
- `INITIAL_ADMIN_PASSWORD` (required on first run to bootstrap admin user)

### Common optional environment variables
- `DATABASE_URL` (default: `sqlite:///./sunny_scada.db`)
- `AUTO_CREATE_DB=1` (dev convenience; prod should use migrations)
- `CORS_ALLOW_ORIGINS` (comma-separated list or `*`; default blocks cross-origin)
- `CLIENT_LOG_TOKEN` (optional shared token for unauthenticated client log ingestion)
- `ENABLE_SCHEDULER=1|0`, `ENABLE_HISTORIAN=1|0`, `ENABLE_MAINT_SCHEDULER=1|0`
- Retention policy days:
  - `RETENTION_SERVER_LOGS_DAYS`, `RETENTION_AUDIT_LOGS_DAYS`, `RETENTION_COMMANDS_DAYS`, `RETENTION_ALARMS_DAYS`,
    `RETENTION_HISTORIAN_RAW_DAYS`, `RETENTION_HISTORIAN_ROLLUP_DAYS`

### DB migrations (recommended)
```bash
alembic upgrade head
```

### Start
```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```
OpenAPI: `http://localhost:8000/docs`

## New endpoints overview (brief examples)
### Auth
- `POST /auth/login` → `{access_token, refresh_token}`
- `POST /auth/refresh` → rotate tokens
- `POST /auth/logout` → revoke refresh token
- `GET /auth/me`

### Config Admin (YAML)
- `GET /config/plcs`
- `POST /config/plcs` `{plc_id, content}`
- `POST /config/validate`
- `GET /config/revisions`
- `POST /config/rollback/{revision_id}`

### Commands
- `POST /commands` `{plc_name, datapoint_id, kind, value, bit}` → queued
- `GET /commands` and `GET /commands/{command_id}`
- `POST /commands/{command_id}/cancel`

### Logs/Alarms
- `POST /logs/client` (auth or `X-Client-Log-Token`)
- `GET /logs/server`, `/logs/commands`, `/logs/alarms`, `/logs/audit`
- `POST /alarms`, `POST /alarms/{alarm_id}/ack`

### Maintenance
- `/maintenance/equipment`, `/vendors`, `/spare_parts`, `/breakdowns`, `/task_templates`, `/schedules`, `/work_orders`

### Historian
- `GET /trends?...` and `GET /trends/latest?...`

### Health
- `GET /health`
- `GET /health/plcs`

## Security checklist implemented
- ✅ JWT auth + refresh tokens stored hashed in DB; logout revokes refresh token
- ✅ Argon2 password hashing
- ✅ Lockout policy on repeated failed logins
- ✅ RBAC permissions with wildcard support; least-privilege checks per endpoint
- ✅ PLC writes restricted to configured writable datapoints in `data_points.yaml` only
- ✅ Type/range validation for command writes; bit writes restricted to configured bits
- ✅ Non-blocking write pipeline with per-PLC serialization
- ✅ Rate limiting (process-local) for commands and client log ingestion
- ✅ Concurrency-safe `data_points.yaml` edits: file locking + atomic write + round-trip YAML
- ✅ Full traceability: audit log records user_id, timestamp, and client_ip for config/admin/command/alarm actions
- ✅ Secure headers middleware + request size limiting
- ✅ CORS defaults to locked down (same-origin); configurable via env
- ✅ No hardcoded secrets (JWT/admin password via env only)
