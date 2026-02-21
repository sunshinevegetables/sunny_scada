# Sunny SCADA (Modbus TCP)

This project is a FastAPI-based SCADA/HMI that communicates with PLCs via Modbus TCP.

## Key upgrade included in this repo

### Block-Read Polling (scan-plan)
The polling loop no longer performs one Modbus request per tag. Instead it:
- parses `config/data_points.yaml`
- builds a scan plan (merged register ranges)
- reads each range once (block read) and decodes all tags from the cached registers

This reduces PLC load, reduces timeouts, and increases UI responsiveness.

### Thread-safe Modbus I/O
All Modbus I/O goes through `sunny_scada/modbus_manager.py`:
- one persistent `ModbusTcpClient` per PLC
- a per-PLC lock ensures polling and writes do not interleave
- retry/backoff for transient network errors

## Run

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

pip install -r requirements.txt

# Initialize DB schema (recommended)
alembic upgrade head

uvicorn main:app --host 0.0.0.0 --port 8000
```

Then open: `http://localhost:8000/`

## Security / Authentication (default-deny)

This backend is **default-deny**: every non-allowlisted route requires `Authorization: Bearer <access_token>`.

Anonymous routes are intentionally minimal:
- UI/static assets (`/`, `/admin-panel*`, `/static*`, legacy mounts)
- Auth/token issuance: `/auth/login`, `/auth/refresh`, `/oauth/token`
- Health: `/health`

Swagger docs are only exposed when `ENV=dev` (or `development/local`).

### User login (interactive)

```bash
curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"..."}'

# Call a protected endpoint
curl -s http://localhost:8000/plc_data \
  -H "Authorization: Bearer <access_token>"
```

### App clients (service-to-service, client credentials)

Create an app client from an admin user (returns the secret **once**):

```bash
curl -s -X POST http://localhost:8000/admin/app-clients \
  -H "Authorization: Bearer <admin_access_token>" \
  -H "Content-Type: application/json" \
  -d '{"name":"grafana","role":"admin","allowed_ips":["10.0.0.0/8"]}'
```

Request a short-lived access token (no refresh token; re-issue as needed):

```bash
curl -s -X POST http://localhost:8000/oauth/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -u "<client_id>:<client_secret>" \
  -d "grant_type=client_credentials"

curl -s http://localhost:8000/plc_data \
  -H "Authorization: Bearer <app_access_token>"
```

## Environment variables

| Variable | Default | Meaning |
|---|---:|---|
| `POLLING_INTERVAL_PLC` | `1` | seconds between poll cycles |
| `MODBUS_TIMEOUT_S` | `3` | Modbus TCP socket timeout |
| `MODBUS_RETRIES` | `2` | retries per Modbus operation |
| `MODBUS_BACKOFF_S` | `0.2` | base backoff between retries |
| `MODBUS_MAX_BLOCK_SIZE` | `120` | max registers per block read (typical devices support up to 125) |
| `MODBUS_MAX_GAP` | `2` | allow small holes when merging reads |
| `DEFAULT_PLC_NAME` | `Main PLC` | fallback PLC name for legacy write calls |
| `DATABASE_URL` | `sqlite:///./sunny_scada.db` | SQLAlchemy database URL |
| `AUTO_CREATE_DB` | `0` | dev/test escape hatch to create tables without Alembic |
| `JWT_SECRET_KEY` | **required** | JWT signing secret (do not commit) |
| `ENV` | `prod` | environment mode (`dev` enables Swagger docs) |
| `AUTH_ENABLED` | `1` | set to `0` to disable auth (dev/test only) |
| `JWT_ISSUER` | `sunny_scada` | JWT issuer (`iss`) |
| `JWT_AUDIENCE` | *(empty)* | optional JWT audience (`aud`) |
| `JWT_LEEWAY_S` | `30` | JWT clock skew leeway (seconds) |
| `ACCESS_TOKEN_TTL_S` | `900` | user access token TTL (seconds) |
| `APP_ACCESS_TOKEN_TTL_S` | `3600` | app client access token TTL (seconds) |
| `REFRESH_TOKEN_TTL_S` | `604800` | refresh token TTL (seconds) |
| `TRUSTED_PROXIES` | *(empty)* | IP/CIDR list of trusted proxies that can set X-Forwarded-Proto |
| `INITIAL_ADMIN_PASSWORD` | **required on first run** | bootstrap admin user password |
| `DIGITAL_BIT_MAX` | `15` | max bit index allowed for DIGITAL datapoint bit labels |

Example `.env`:

```bash
ENV=prod
AUTH_ENABLED=1
JWT_SECRET_KEY=sunshinevegetables
JWT_ISSUER=sunny_scada

# Optional hardening
JWT_AUDIENCE=sunny_scada_api
TRUSTED_PROXIES=10.0.0.0/8,127.0.0.1

# Token TTLs (seconds)
ACCESS_TOKEN_TTL_S=900
APP_ACCESS_TOKEN_TTL_S=3600
REFRESH_TOKEN_TTL_S=604800

INITIAL_ADMIN_PASSWORD=admin
```

## System config module (DB-backed)

Authenticated users with `config:read`/`config:write` can CRUD PLCs, containers, equipment, and datapoints under:

- `/api/config/*`

See `docs/SYSTEM_CONFIG_API.md` for endpoints and example payloads.

## Notes about Modbus addressing

This code preserves the project's historical register mapping:

```py
pymodbus_address = address_4x - 40001 + 1
```

If you want strict 0-based addressing (recommended long-term), update
`sunny_scada/scan_plan.py::address_4x_to_pymodbus` and adjust YAML addresses.
