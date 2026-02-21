# Write Commands Migration to Database-Only

## Overview
All PLC write commands now use **database-driven configuration only**. The system no longer loads or references `data_points.yaml` for write operations.

## Changes Made

### 1. PLCWriter Class (`sunny_scada/plc_writer.py`)
**Removed:**
- `data_points_file` parameter from constructor
- `load_write_points()` method
- `write_signals` attribute (YAML data storage)
- `find_write_register()` method
- `_find_point_in_tree()` helper function
- `yaml` import

**Current State:**
- Constructor now only requires `ModbusService`
- Class provides low-level Modbus write operations only:
  - `bit_write_signal()` - writes single bit
  - `write_register()` - writes full register
- All address/bit validation is handled by CommandService using DB queries

### 2. CommandService (`sunny_scada/services/command_service.py`)
**Removed:**
- `data_points: DataPointsService` parameter from constructor
- `self._dp` attribute (unused)
- `DataPointsService` import

**Current Behavior:**
- Enforces DB-only datapoint identifiers (format: `db-dp:123`)
- Queries `CfgDataPoint` table to retrieve:
  - Address (Modbus register)
  - Type (DIGITAL, INTEGER, etc.)
  - Allowed bits (from `CfgDataPointBit` table)
- Validates write parameters against DB configuration
- Stores resolved address/bit/value in command payload

### 3. CommandExecutor (`sunny_scada/services/command_executor.py`)
**Updated:**
- Documentation updated to reference "database" instead of "data_points.yaml"
- Execution logic unchanged (uses payload from CommandService)

### 4. Application Initialization (`sunny_scada/api/app.py`)
**Updated:**
```python
# Before:
app.state.plc_writer = PLCWriter(
    modbus=app.state.modbus,
    data_points_file=_resolve(settings.data_points_file),
)

app.state.command_service = CommandService(
    modbus=app.state.modbus,
    data_points=app.state.data_points_service,
    ...
)

# After:
app.state.plc_writer = PLCWriter(
    modbus=app.state.modbus,
)

app.state.command_service = CommandService(
    modbus=app.state.modbus,
    executor=app.state.command_executor,
    ...
)
```

### 5. Documentation Updates (`REPORT.md`)
- Updated architecture summary to reflect DB-driven writes
- Removed references to `data_points.yaml` for write operations
- Clarified that write configuration is database-managed

## Database Schema Requirements

For write operations to work, the following tables must be populated:

### Required Tables
1. **CfgPLC** - PLC configurations
   - `name` - PLC identifier (matches Modbus config)
   
2. **CfgContainer** - Hierarchical containers
   - Links PLCs to equipment groups

3. **CfgEquipment** - Equipment instances
   - `label` - Equipment identifier (e.g., "COMP_1")
   - Links to container

4. **CfgDataPoint** - Write datapoints
   - `label` - Command tag (e.g., "COMP_1_WR")
   - `address` - Modbus register address (e.g., "41336")
   - `type` - Data type ("DIGITAL", "INTEGER")
   - `category` - Must be "write"
   - Links to equipment

5. **CfgDataPointBit** - Bit definitions for DIGITAL datapoints
   - `bit` - Bit position (0-15)
   - `label` - Bit description (e.g., "START", "STOP")
   - Links to datapoint

## Frontend Integration

### Request Format
```javascript
POST /api/plc/bit_write_signal
{
  "plc": "Main PLC",              // PLC name from CfgPLC
  "equipmentLabel": "COMP_1",     // Equipment label from CfgEquipment
  "commandTag": "COMP_1_WR",      // Datapoint label from CfgDataPoint
  "bit": 0,                       // Bit position (0-15)
  "value": 1                      // Bit value (0 or 1)
}
```

### Example: Start Compressor 1
```javascript
const response = await fetch('/api/plc/bit_write_signal', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${accessToken}`
  },
  body: JSON.stringify({
    plc: "Main PLC",
    equipmentLabel: "COMP_1",
    commandTag: "COMP_1_WR",
    bit: 0,  // BIT 0 = START
    value: 1
  })
});

const result = await response.json();
// Returns: { command_id: "...", status: "queued", message: "..." }
```

## Migration Path

### From YAML to Database

1. **Extract write datapoints from `data_points.yaml`**
   ```yaml
   # Example YAML structure
   write:
     COMP_1_WR:
       address: 41336
       type: DIGITAL
       bits:
         BIT 0: START
         BIT 1: STOP
   ```

2. **Create database records**
   ```sql
   -- 1. Ensure PLC exists
   INSERT INTO cfg_plc (name, ...) VALUES ('Main PLC', ...);
   
   -- 2. Create container (if needed)
   INSERT INTO cfg_container (plc_id, label, ...) VALUES (...);
   
   -- 3. Create equipment
   INSERT INTO cfg_equipment (container_id, label, ...) VALUES (..., 'COMP_1', ...);
   
   -- 4. Create write datapoint
   INSERT INTO cfg_data_point (equipment_id, label, address, type, category, ...)
   VALUES (..., 'COMP_1_WR', '41336', 'DIGITAL', 'write', ...);
   
   -- 5. Create bit definitions
   INSERT INTO cfg_data_point_bit (data_point_id, bit, label)
   VALUES (..., 0, 'START'), (..., 1, 'STOP'), ...;
   ```

3. **Use System Config API** (recommended)
   - Use `/api/system-config/plcs` endpoints to create PLC hierarchy
   - Use `/api/system-config/equipment` to manage equipment
   - Use `/api/system-config/datapoints` to create write datapoints

## Benefits of DB-Driven Approach

1. **Dynamic Configuration**
   - Add/modify write points without code deployment
   - Changes take effect immediately

2. **Better Validation**
   - Structured schema enforces data integrity
   - Foreign key relationships ensure consistency

3. **Audit Trail**
   - Database changes can be logged and tracked
   - Version control through migrations

4. **Scalability**
   - Easier to query and filter datapoints
   - Better performance for large configurations

5. **No File Locking Issues**
   - Database handles concurrency natively
   - No YAML parsing overhead

## Backward Compatibility

**Read Operations:**
- `PLCReader` still uses `data_points.yaml` for read datapoints
- No changes required for read functionality
- Storage layer unchanged

**Write Operations:**
- **YAML-based writes are no longer supported**
- All writes must use database-configured datapoints
- Frontend must be updated to use DB lookup approach

## Testing

All existing tests continue to pass:
- Tests mock `PLCWriter` methods directly
- No changes required to test fixtures
- Command creation tests validate DB queries

## Documentation References

- [DB_WRITE_ENDPOINT.md](docs/DB_WRITE_ENDPOINT.md) - Detailed endpoint documentation
- [SYSTEM_CONFIG_API.md](docs/SYSTEM_CONFIG_API.md) - Database configuration API
- [QUICKSTART_ALARMS.md](QUICKSTART_ALARMS.md) - General system quickstart

## Summary

The write command system is now fully database-driven, eliminating dependency on `data_points.yaml` for write operations. This provides better scalability, validation, and dynamic configuration capabilities while maintaining the same command execution architecture.
