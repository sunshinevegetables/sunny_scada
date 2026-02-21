# DB-Driven Write Endpoint (`/bit_write_signal`)

The `/api/plc/bit_write_signal` endpoint uses **database-driven lookups only**.

---

## Request Format

Provide `plc`, `equipmentLabel`, and `commandTag` to resolve the write datapoint from the database.

```json
{
  "plc": "Main PLC",
  "equipmentLabel": "Screw 4",
  "commandTag": "Screw 4 Control",
  "bit": 1,
  "value": 0
}
```

All fields are required except `equipmentId` and `receiverId` which are optional.

---

## How It Works

1. **Endpoint receives the request** with DB fields (`plc`, `equipmentLabel`, `commandTag`).
2. **System Config Service** queries the database:
   - Finds the PLC by `name`.
   - Joins through `CfgContainer` → `CfgEquipment` → `CfgDataPoint` to match `equipmentLabel` and `commandTag` (datapoint label).
   - Returns the matching `CfgDataPoint` where `category='write'`.
3. **Address is extracted** from the DB datapoint's `address` field.
4. **Bit is validated** against the `CfgDataPointBit` table entries for that datapoint.
5. **Command is queued** with a synthetic `datapoint_id` (format: `db-dp:123`).
6. **Command Service** recognizes the `db-dp:` prefix and resolves address/type/bits from DB instead of YAML.
7. **Command Executor** writes the bit to the PLC using the resolved Modbus address.

---

## Example Frontend Request

```javascript
fetch('/api/plc/bit_write_signal', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${token}`
  },
  body: JSON.stringify({
    plc: "Main PLC",
    equipmentLabel: "Screw 4",
    commandTag: "Screw 4 Control",
    bit: 1,
    value: 0,
    source: "frontend"
  })
})
.then(res => res.json())
.then(data => {
  console.log('Command queued:', data.command_id);
});
```

---

## Database Schema Requirements

Ensure your DB has the following configured:

- **CfgPLC**: The PLC record with matching `name`.
- **CfgContainer**: A container under the PLC.
- **CfgEquipment**: An equipment record under the container with matching `name` (equipmentLabel).
- **CfgDataPoint**: A datapoint with:
  - `owner_type = "equipment"`
  - `owner_id = <equipment.id>`
  - `category = "write"`
  - `label = <commandTag>`
  - `type = "DIGITAL"` (for bit writes)
  - `address = <Modbus 4xxxx address>`
- **CfgDataPointBit**: Bit definition rows for each allowed bit (optional, enforced if present).

---

## Response

```json
{
  "message": "Queued write of value 0 to bit 1 for datapoint db-dp:42 on Main PLC",
  "command_id": "cmd_abc123",
  "status": "queued"
}
```

The command is executed asynchronously by the Command Executor service.

---

## Error Responses

| Status | Detail                                                                 |
|--------|------------------------------------------------------------------------|
| 400    | `equipmentLabel and commandTag are required for DB-driven mode`       |
| 400    | `Write datapoint not found for PLC 'X', equipment 'Y', tag 'Z'`       |
| 400    | `Invalid bit '2' for command tag 'Z'. Allowed bits: [0, 1]`           |
| 429    | `Rate limit exceeded`                                                  |

---

## Migration Notes

- **YAML-based writes are no longer supported**. All write datapoints must be configured in the database.
- Configure datapoints via the System Config API (`/api/config`).
- The `receiverId` field in the request is currently **not used** but is accepted for forward compatibility.
