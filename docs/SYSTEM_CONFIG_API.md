# System configuration API (DB-backed)

This module lets authenticated users with `config:read`/`config:write` manage:

- PLCs (name/ip/port)
- Containers under PLCs
- Equipment under containers
- Data points attached to PLC/container/equipment (metadata only)

It stores configuration metadata in the database. It does **not** execute PLC writes.

## Permissions

- Read endpoints: `config:read`
- Write endpoints: `config:write`

## PLCs

### Create PLC

`POST /api/config/plcs`

```json
{
  "name": "cold_stores",
  "ip": "192.168.1.10",
  "port": 502
}
```

### List PLCs

`GET /api/config/plcs`

### Update PLC

`PATCH /api/config/plcs/{plcId}`

```json
{ "ip": "coldstores.local" }
```

### Delete PLC (safe delete)

`DELETE /api/config/plcs/{plcId}` → rejects if it has dependents

`DELETE /api/config/plcs/{plcId}?force=true` → cascades delete

## Containers (under a PLC)

`GET /api/config/plcs/{plcId}/containers`

`POST /api/config/plcs/{plcId}/containers`

```json
{ "name": "COND-01", "type": "COND" }
```

`PATCH /api/config/containers/{containerId}`

`DELETE /api/config/containers/{containerId}?force=true`

## Equipment (under a Container)

`GET /api/config/containers/{containerId}/equipment`

`POST /api/config/containers/{containerId}/equipment`

```json
{ "name": "EVAP-01", "type": "EVAP" }
```

`PATCH /api/config/equipment/{equipmentId}`

`DELETE /api/config/equipment/{equipmentId}?force=true`

## Data points

Attach to exactly one owner:

- PLC-level: `.../plcs/{plcId}/data-points`
- Container-level: `.../containers/{containerId}/data-points`
- Equipment-level: `.../equipment/{equipmentId}/data-points`

### Create DIGITAL datapoint with bit labels

```json
{
  "label": "CTRL_STS",
  "description": "Control status word",
  "category": "read",
  "type": "DIGITAL",
  "address": "DB10.DBW2",
  "bitLabels": {
    "0": "Ready",
    "1": "Run",
    "7": "Trip",
    "9": "Pump On"
  }
}
```

### Common datapoint endpoints

- `GET /api/config/data-points/{dataPointId}`
- `PATCH /api/config/data-points/{dataPointId}`
- `DELETE /api/config/data-points/{dataPointId}`
