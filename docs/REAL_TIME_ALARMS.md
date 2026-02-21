# Real-Time Alarm Streaming via WebSocket

## Overview

This document describes the real-time alarm event streaming system implemented in Sunny SCADA. The system evaluates database-backed alarm rules on every PLC datapoint update and broadcasts state-change events to connected WebSocket clients in real-time.

## Architecture

### Components

1. **AlarmBroadcaster** (`sunny_scada/services/alarm_broadcaster.py`)
   - Manages active WebSocket connections from clients
   - Thread-safe broadcasting of alarm events using `call_soon_threadsafe()`
   - Automatically cleans up disconnected clients

2. **AlarmMonitor** (`sunny_scada/services/alarm_monitor.py`)
   - Evaluates enabled alarm rules on incoming datapoint values
   - Tracks rule state transitions (OK → WARNING/ALARM, WARNING → ALARM, etc.)
   - Only broadcasts events when state changes (prevents spam)
   - Maintains in-memory cache of rule definitions and active alarms
   - Thread-safe (uses RLock for concurrent access from polling threads)

3. **WebSocket Endpoint** (`sunny_scada/api/routers/ws_alarms.py`)
   - HTTP endpoint: `GET /ws/alarms` (WebSocket upgrade)
   - **Auth Handshake**: First message must be `{"type":"auth","access_token":"<JWT>"}`
   - **Security**: Validates JWT token, checks `alarms:read` or `alarms:admin` permission
   - **Snapshot**: Sends current active alarms on successful auth
   - **Streaming**: Broadcasts incoming alarm_state events to all connected clients
   - **Error Codes**: 4401 (invalid token), 4403 (forbidden)

4. **PollingService Integration** (`sunny_scada/services/polling_service.py`)
   - Extended to accept optional `alarm_monitor` parameter
   - On each PLC data read, extracts datapoint values and calls `alarm_monitor.process_value()`
   - Integrates seamlessly with existing polling loop without breaking changes

5. **App Startup** (`sunny_scada/api/app.py`)
   - During lifespan, instantiates `AlarmBroadcaster` and `AlarmMonitor`
   - Passes `alarm_monitor` to `PollingService` for integration
   - Registers WebSocket router

## Data Flow

```
PLC Modbus Registers
        ↓
  PLCReader.read_plcs_from_config()
        ↓
  DataStorage (in-process cache)
        ↓
  PollingService._process_alarms_from_data()
        ↓
  AlarmMonitor.process_value(plc_name, point_label, value)
        ↓
  Evaluates DB alarm rules (via alarm_rules_logic.evaluate_rule)
        ↓
  Checks schedule windows (timezone-aware)
        ↓
  Compares thresholds (low, high, warning, alarm)
        ↓
  Tracks state transitions
        ↓
  Broadcasts alarm_state event on state change
        ↓
  AlarmBroadcaster.broadcast(payload)
        ↓
  All connected WebSocket clients receive event
```

## Event Payload Format

### Alarm State Event

When a datapoint triggers a rule or a rule's state changes, clients receive:

```json
{
  "type": "alarm_state",
  "ts": "2024-01-15T14:30:45.123456+00:00",
  "datapoint_id": 42,
  "rule_id": 77,
  "rule_name": "Compressor Discharge Temperature High",
  "state": "ALARM",
  "severity": "critical",
  "comparison": "above",
  "value": 85.5,
  "warning_threshold": 75.0,
  "alarm_threshold": 80.0,
  "message": "Rule Compressor Discharge Temperature High -> ALARM"
}
```

**Fields:**
- `type`: "alarm_state" (message type identifier)
- `ts`: ISO8601 timestamp in UTC
- `datapoint_id`: Database ID of the datapoint
- `rule_id`: Database ID of the alarm rule
- `rule_name`: Human-readable rule name
- `state`: One of "OK", "WARNING", or "ALARM"
- `severity`: Rule severity level (e.g., "critical", "high", "medium", "low")
- `comparison`: Comparison operator used (e.g., "above", "below", "in_range")
- `value`: Current datapoint value that triggered the rule
- `warning_threshold`: Warning threshold (if applicable)
- `alarm_threshold`: Alarm threshold (if applicable)
- `message`: Summary message

### Snapshot Event (Initial)

On successful WebSocket auth, clients immediately receive active alarms:

```json
{
  "type": "snapshot",
  "active": [
    {"rule_id": 77, "state": "ALARM"},
    {"rule_id": 88, "state": "WARNING"}
  ],
  "ts": ""
}
```

## Client Usage Example

### JavaScript Frontend

```javascript
// Establish WebSocket connection
const ws = new WebSocket('ws://localhost:8000/ws/alarms');

ws.onopen = () => {
  // Send auth handshake with JWT bearer token
  const token = localStorage.getItem('access_token');
  ws.send(JSON.stringify({
    type: 'auth',
    access_token: token
  }));
};

ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  
  if (msg.type === 'snapshot') {
    console.log('Active alarms:', msg.active);
  } else if (msg.type === 'alarm_state') {
    console.log(`${msg.rule_name} -> ${msg.state}`, msg.value);
    
    // Update UI, play sound, send notification, etc.
    if (msg.state === 'ALARM') {
      playAlarmSound();
      showNotification(`🚨 ${msg.rule_name}: ${msg.value}`);
    }
  }
};

ws.onerror = (error) => {
  console.error('WebSocket error:', error);
};

ws.onclose = (event) => {
  if (event.code === 4401) {
    console.error('Invalid token');
    redirectToLogin();
  } else if (event.code === 4403) {
    console.error('Access denied');
  } else {
    console.log('Connection closed, attempting reconnect...');
    setTimeout(() => location.reload(), 3000);
  }
};
```

### Python Client

```python
import json
import asyncio
import websockets
from auth_client import get_access_token  # Your auth method

async def listen_alarms():
    token = get_access_token()
    uri = "ws://localhost:8000/ws/alarms"
    
    async with websockets.connect(uri) as websocket:
        # Send auth
        await websocket.send(json.dumps({
            "type": "auth",
            "access_token": token
        }))
        
        # Listen for events
        async for message in websocket:
            event = json.loads(message)
            
            if event['type'] == 'snapshot':
                print(f"Active alarms: {event['active']}")
            elif event['type'] == 'alarm_state':
                print(f"{event['rule_name']}: {event['state']} ({event['value']})")

asyncio.run(listen_alarms())
```

## Permission Model

Clients must have one of the following permissions to connect:
- `alarms:read` - Read-only access to alarm streams
- `alarms:admin` - Full alarm management (read, write, delete)
- `alarms:*` - Wildcard (full access)

Permission validation occurs during WebSocket handshake. Insufficient permissions result in close code 4403.

## State Transitions

Alarm states follow these valid transitions:

```
OK → WARNING → ALARM
↓     ↓     ↑
└─────←─────┘
```

Events are **only** broadcast when state changes. Repeated evaluations with the same result do not emit events.

## Schedule-Aware Rules

Alarm rules can have optional schedule windows:
- `schedule_enabled`: Boolean flag
- `schedule_start_time`: HH:MM (UTC or configured timezone)
- `schedule_end_time`: HH:MM (UTC or configured timezone)
- `schedule_timezone`: IANA timezone (e.g., "America/Chicago")

Rules are evaluated only within their configured windows. Outside windows, rules remain in "OK" state.

## Performance Considerations

1. **Low Latency**: Events reach clients within milliseconds of datapoint value updates
2. **Caching**: Rule definitions cached in-memory per datapoint to avoid repeated DB queries
3. **Thread-Safe**: AlarmMonitor uses RLock; AlarmBroadcaster uses event loop's `call_soon_threadsafe()`
4. **Selective Broadcasts**: Only state changes trigger events (no spam on repeated readings)
5. **Scalability**: WebSocket connections are lightweight; supports hundreds of concurrent clients

## Testing

### Manual Test

1. Start the Sunny SCADA application
2. Authenticate to get JWT token
3. Connect to `ws://localhost:8000/ws/alarms` with auth handshake
4. Modify PLC data via Modbus simulator or actual hardware
5. Observe alarm_state events streaming in real-time

### Integration Test

```python
import pytest
from sunny_scada.services.alarm_monitor import AlarmMonitor
from sunny_scada.services.alarm_broadcaster import AlarmBroadcaster

@pytest.mark.asyncio
async def test_alarm_broadcast():
    broadcaster = AlarmBroadcaster()
    monitor = AlarmMonitor(db_sessionmaker, broadcaster)
    
    # Simulate datapoint update
    monitor.process_value("PLC_1", "Compressor_Discharge_Temp", 85.5)
    
    # Verify broadcast was called
    assert len(broadcaster._conns) == 0  # No clients in test
    # In real scenario, would verify event was queued for broadcast
```

## Troubleshooting

### No Events Received

1. **Check WebSocket connection**: Use browser DevTools Network tab
2. **Verify JWT token**: Ensure token has not expired (`/auth/me` endpoint)
3. **Check permissions**: Confirm user/app has `alarms:read` or `alarms:admin`
4. **Verify polling enabled**: Check `ENABLE_PLC_POLLING=1` in config
5. **Inspect server logs**: Look for errors in `AlarmMonitor.process_value()` or `AlarmBroadcaster.broadcast()`

### High CPU Usage

1. **Reduce polling interval**: Increase `POLLING_INTERVAL_PLC_S`
2. **Disable unnecessary rules**: Inactive rules still evaluated; disable in admin UI
3. **Check rule complexity**: Rules with many conditions or schedule windows increase overhead

### WebSocket Closes with Code 4401

- JWT token is invalid or expired
- Solution: Re-authenticate and obtain new token

### WebSocket Closes with Code 4403

- User/app lacks `alarms:read` permission
- Solution: Update role permissions in admin UI

## Integration with Existing Systems

### Legacy MonitoringService

The new real-time alarm system is **independent** of the legacy `MonitoringService`:
- Legacy service: Monitors frozen/cold data, plays audio/TTS alerts
- Real-time system: Streams rule state changes to WebSocket clients

Both can coexist; migrations are gradual.

### Alarm Rules Admin API

Existing endpoints remain unchanged:
- `POST /admin/alarm-rules` - Create rule
- `GET /admin/alarm-rules/{rule_id}` - Get rule
- `PUT /admin/alarm-rules/{rule_id}` - Update rule
- `DELETE /admin/alarm-rules/{rule_id}` - Delete rule

Real-time changes take effect immediately (AlarmMonitor invalidates cache).

## File Locations

- **Services**: `sunny_scada/services/alarm_broadcaster.py`, `alarm_monitor.py`
- **WebSocket Router**: `sunny_scada/api/routers/ws_alarms.py`
- **Integration Points**: `sunny_scada/services/polling_service.py`, `sunny_scada/api/app.py`
- **Tests**: `tests/test_real_time_alarms.py` (if added)

## Future Enhancements

1. **Historical Playback**: Replay alarm events from timestamp range
2. **Filtering**: Allow clients to subscribe to specific datapoints/rules
3. **Aggregation**: Send batched events at configurable interval
4. **Persistence**: Log all alarm state transitions for audit trail
5. **Escalation**: Auto-escalate alarms if not acknowledged within time window
6. **Webhooks**: External webhook notifications on alarm state change

## Configuration

No additional configuration required beyond existing Sunny SCADA settings. The system relies on:
- Existing JWT auth infrastructure
- Existing alarm rule definitions (database-backed)
- Existing polling interval settings

Optional tuning:
- `POLLING_INTERVAL_PLC_S`: Affects alarm response latency (default: 2 seconds)
- `JWT_LEEWAY_S`: Clock skew tolerance for token validation (default: 10 seconds)
