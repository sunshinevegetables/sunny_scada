# Garmin Watch Polling API

Base path: `/api/watch`

This API is polling-only (no SSE/WebSocket) and is intended for watch clients that poll every 15 seconds.

## Authentication

### `POST /api/watch/token`

Request body:

```json
{
  "username": "admin",
  "password": "***"
}
```

Response:

```json
{
  "access_token": "<JWT>",
  "token_type": "bearer",
  "expires_at": "2026-02-22T18:30:00Z"
}
```

JWT claims include:

- `sub` (user id)
- `scope="watch"`
- `exp`

Token TTL is configurable with `WATCH_TOKEN_TTL_HOURS` and clamped to 24–72 hours (default 48h).

## Endpoints

### `GET /api/watch/datapoints?q=<search>&equipment_id=<id>&limit=50`

- Requires `Authorization: Bearer <watch-token>`
- `q` is case-insensitive label search
- `equipment_id` filters owner equipment
- `limit` default 50, max 100
- Labels are truncated to 32 chars
- Unauthorized datapoints are silently omitted

### `GET /api/watch/datapoints/latest?ids=1,2,3`

- Requires `Authorization: Bearer <watch-token>`
- `ids` is required comma-separated cfg datapoint IDs
- Max 6 IDs per call (more than 6 returns HTTP 400)
- Missing/nonexistent/unauthorized IDs are omitted (no 404)

Response shape:

```json
{
  "ts": "2026-02-21T10:15:00Z",
  "values": {
    "1": {
      "value": 4.6,
      "unit": "°C",
      "quality": "good",
      "timestamp": "2026-02-21T10:14:58Z"
    }
  }
}
```

Quality values:

- `no_data`: no value ever seen
- `stale`: latest timestamp older than `WATCH_STALE_AFTER_S` (default 120s)
- `error`: fault/bad-quality indicator present in latest snapshot
- `good`: otherwise

## Datapoint ID range

- The IDs used by watch endpoints are `cfg_data_points.id` values from table `cfg_data_points`.
- Supported IDs are positive integers (`>=1`).

## Rate limiting

- Applied to watch-scoped tokens on `/api/watch/*` (except `/api/watch/token`).
- Config: `WATCH_RATE_LIMIT_PER_MIN` (clamped to 50–100 requests/min).

## Curl examples

```bash
curl -X POST http://localhost:8000/api/watch/token \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"TestPassword!12345"}'
```

```bash
curl "http://localhost:8000/api/watch/datapoints?q=temp&limit=50" \
  -H "Authorization: Bearer <JWT>"
```

```bash
curl "http://localhost:8000/api/watch/datapoints/latest?ids=1,2,3" \
  -H "Authorization: Bearer <JWT>"
```
