# Admin Panel

This repository includes a minimalistic admin panel (vanilla HTML/CSS/JS) served directly by the FastAPI backend.

## Run

```bash
# create venv, install deps
pip install -r requirements.txt

# run migrations
alembic upgrade head

# start the server
uvicorn sunny_scada.api.app:create_app --factory --reload --host 127.0.0.1 --port 8000
```

## Open

- Admin SPA: `http://127.0.0.1:8000/admin-panel`
  - Hash routes:
    - `#users`
    - `#roles`
    - `#plc`
    - `#access`
- Login: `http://127.0.0.1:8000/admin-panel/login`

## Permissions

The UI hides navigation items based on `/auth/me.permissions`, but **the server is authoritative** and enforces permissions on every API request.

Typical permissions required:

- Users page: `users:admin`
- Roles page: `roles:admin`
- PLC Builder: `config:read` (view), `config:write` (create/update/delete)
- Access Control: `users:admin` (manage user grants) and/or `roles:admin` (manage role grants)

## Notes

- The admin panel is served as static files by the backend; no frontend dev server or build step.
- The API client automatically refreshes the access token once on 401 using `/auth/refresh`, then retries.
- Refresh token rotation is enabled: the UI stores the **new** `refresh_token` returned by `/auth/refresh`.

## App clients (trusted applications)

Admins can create service-to-service clients (OAuth2 client credentials) via:

- `POST /admin/app-clients` (returns `client_secret` **once**)
- `GET /admin/app-clients`
- `PUT /admin/app-clients/{client_id}`
- `POST /admin/app-clients/{client_id}/rotate-secret`

Token issuance:

```bash
curl -s -X POST http://127.0.0.1:8000/oauth/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -u "<client_id>:<client_secret>" \
  -d "grant_type=client_credentials"
```
