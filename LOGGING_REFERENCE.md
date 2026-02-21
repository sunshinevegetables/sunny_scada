# Alarm/Warning Logging Implementation

## Overview

Comprehensive logging has been added to the real-time alarm streaming system to help developers monitor and debug alarm events in the terminal.

## Logging Locations & Messages

### 1. **AlarmMonitor Service** (`sunny_scada/services/alarm_monitor.py`)

#### ALARM Raised - ERROR Level ⛔
```
🔴 [ALARM] Rule 'Compressor Discharge High Temp' (ID: 77) on PLC_1.CompDischarge 
raised alarm (value: 85.5, threshold: 80.0, severity: critical)
```
- **Level**: ERROR (red text in terminal)
- **When**: A datapoint value triggers an alarm rule
- **Info**: Rule name, rule ID, PLC/point location, current value, threshold, and severity

#### WARNING Raised - WARNING Level ⚠️
```
🟡 [WARNING] Rule 'Filter Pressure High' (ID: 88) on PLC_2.FilterPress 
raised warning (value: 72.0, threshold: 70.0, severity: high)
```
- **Level**: WARNING (yellow text in terminal)
- **When**: A datapoint value enters warning state
- **Info**: Rule name, rule ID, PLC/point location, current value, threshold, and severity

#### ALARM/WARNING Acknowledged - INFO Level ✅
```
🟢 [ALARM ACKNOWLEDGED] Rule 'Compressor Discharge High Temp' (ID: 77) on PLC_1.CompDischarge 
transitioned to OK (value: 75.0, severity: critical)
```
- **Level**: INFO (green text in terminal)
- **When**: An alarm/warning clears (value returns to normal range)
- **Info**: Rule name, rule ID, PLC/point location, value in normal range, severity

#### Rule Evaluation Errors - EXCEPTION Level
```
Failed evaluating rule 77
```
- **Level**: ERROR with exception traceback
- **When**: Rule evaluation throws an exception

---

### 2. **AlarmBroadcaster Service** (`sunny_scada/services/alarm_broadcaster.py`)

#### WebSocket Client Registration - DEBUG Level 📡
```
📡 WebSocket client registered. Active connections: 3
```
- **Level**: DEBUG
- **When**: A client connects and registers for alarm events
- **Info**: Total active connections

#### WebSocket Client Unregistration - DEBUG Level
```
📡 WebSocket client unregistered. Active connections: 2
```
- **Level**: DEBUG
- **When**: A client disconnects
- **Info**: Remaining active connections

#### Broadcast Initiated - DEBUG Level 📤
```
📤 Broadcasting alarm event: 'Compressor Discharge High Temp' -> ALARM to 3 client(s)
```
- **Level**: DEBUG
- **When**: Alarm state change event begins broadcasting
- **Info**: Rule name, new state, number of connected clients

#### Broadcast Completion - DEBUG Level ✓
```
✓ Alarm event sent to 3/3 client(s)
```
- **Level**: DEBUG
- **When**: All clients have received the event
- **Info**: Number of clients who received event

#### No Active Connections - DEBUG Level
```
No active WebSocket connections for broadcast
```
- **Level**: DEBUG
- **When**: Trying to broadcast but no clients connected
- **Info**: Event discarded (no recipients)

#### Client Send Failure - DEBUG Level
```
Failed to send to client: [exception details]
```
- **Level**: DEBUG
- **When**: Individual client fails to receive event
- **Info**: Exception details

---

### 3. **WebSocket Endpoint** (`sunny_scada/api/routers/ws_alarms.py`)

#### Connection Accepted - DEBUG Level 🔌
```
🔌 WebSocket connection accepted from 192.168.1.100:54321
```
- **Level**: DEBUG
- **When**: TCP connection established (before auth)
- **Info**: Client IP and port

#### Authentication Successful - INFO Level ✅
```
✅ WS [192.168.1.100:54321] (User:john_doe) Authenticated successfully
```
- **Level**: INFO (green)
- **When**: JWT token validated and permissions confirmed
- **Info**: Client ID, username, authenticated

#### Auth Errors - WARNING Level ⚠️

**Invalid/Missing Auth Message**:
```
⚠️ WS [192.168.1.100:54321] Invalid JSON auth message
⚠️ WS [192.168.1.100:54321] Missing or invalid auth payload
```

**Token Validation**:
```
⚠️ WS [192.168.1.100:54321] Invalid or expired token
```

**User/App Not Found**:
```
⚠️ WS [192.168.1.100:54321] User not found or inactive
⚠️ WS [192.168.1.100:54321] App client not found or inactive
```

**Permission Denied**:
```
⚠️ WS [192.168.1.100:54321] (User:john_doe) Access denied - insufficient permissions
```

#### Snapshot Sent - DEBUG Level 📸
```
📸 WS [192.168.1.100:54321] (User:john_doe) Sent snapshot with 2 active alarm(s)
```
- **Level**: DEBUG
- **When**: Current alarm state sent to newly connected client
- **Info**: Client ID, username, number of active alarms

#### Client Disconnected - INFO Level 👋
```
👋 WS [192.168.1.100:54321] (User:john_doe) Disconnected normally
```
- **Level**: INFO
- **When**: Client closes connection normally
- **Info**: Client ID, username

#### Connection Errors - DEBUG Level
```
WS [192.168.1.100:54321] (User:john_doe) Connection error: [exception]
```
- **Level**: DEBUG
- **When**: Abnormal connection termination
- **Info**: Client ID, username, exception

#### Client Unregistration - DEBUG Level 🔌
```
🔌 WS [192.168.1.100:54321] (User:john_doe) Unregistered from broadcaster
```
- **Level**: DEBUG
- **When**: Client removed from broadcaster registry
- **Info**: Client ID, username

---

## Terminal Output Examples

### Normal Alarm/Warning Cycle

```
2026-02-19 14:35:22 WARNING  sunny_scada.services.alarm_monitor
🟡 [WARNING] Rule 'Filter Pressure High' (ID: 88) on PLC_2.FilterPress 
raised warning (value: 72.0, threshold: 70.0, severity: high)

2026-02-19 14:35:22 DEBUG    sunny_scada.services.alarm_broadcaster
📤 Broadcasting alarm event: 'Filter Pressure High' -> WARNING to 2 client(s)

2026-02-19 14:35:22 DEBUG    sunny_scada.services.alarm_broadcaster
✓ Alarm event sent to 2/2 client(s)

2026-02-19 14:35:45 ERROR    sunny_scada.services.alarm_monitor
🔴 [ALARM] Rule 'Filter Pressure Critical' (ID: 89) on PLC_2.FilterPress 
raised alarm (value: 75.0, threshold: 70.0, severity: critical)

2026-02-19 14:35:45 DEBUG    sunny_scada.services.alarm_broadcaster
📤 Broadcasting alarm event: 'Filter Pressure Critical' -> ALARM to 2 client(s)

2026-02-19 14:35:45 DEBUG    sunny_scada.services.alarm_broadcaster
✓ Alarm event sent to 2/2 client(s)

2026-02-19 14:36:10 INFO     sunny_scada.services.alarm_monitor
🟢 [ALARM ACKNOWLEDGED] Rule 'Filter Pressure Critical' (ID: 89) on PLC_2.FilterPress 
transitioned to OK (value: 68.0, severity: critical)

2026-02-19 14:36:10 DEBUG    sunny_scada.services.alarm_broadcaster
📤 Broadcasting alarm event: 'Filter Pressure Critical' -> OK to 2 client(s)

2026-02-19 14:36:10 DEBUG    sunny_scada.services.alarm_broadcaster
✓ Alarm event sent to 2/2 client(s)
```

### WebSocket Connection Lifecycle

```
2026-02-19 14:35:05 DEBUG    sunny_scada.api.routers.ws_alarms
🔌 WebSocket connection accepted from 192.168.1.100:54321

2026-02-19 14:35:05 INFO     sunny_scada.api.routers.ws_alarms
✅ WS [192.168.1.100:54321] (User:john_doe) Authenticated successfully

2026-02-19 14:35:05 DEBUG    sunny_scada.services.alarm_broadcaster
📡 WebSocket client registered. Active connections: 3

2026-02-19 14:35:05 DEBUG    sunny_scada.api.routers.ws_alarms
📸 WS [192.168.1.100:54321] (User:john_doe) Sent snapshot with 1 active alarm(s)

[client sends multiple alarm events...]

2026-02-19 14:36:15 INFO     sunny_scada.api.routers.ws_alarms
👋 WS [192.168.1.100:54321] (User:john_doe) Disconnected normally

2026-02-19 14:36:15 DEBUG    sunny_scada.services.alarm_broadcaster
📡 WebSocket client unregistered. Active connections: 2

2026-02-19 14:36:15 DEBUG    sunny_scada.api.routers.ws_alarms
🔌 WS [192.168.1.100:54321] (User:john_doe) Unregistered from broadcaster
```

### Authentication Failure

```
2026-02-19 14:35:05 DEBUG    sunny_scada.api.routers.ws_alarms
🔌 WebSocket connection accepted from 192.168.1.100:54322

2026-02-19 14:35:06 WARNING  sunny_scada.api.routers.ws_alarms
⚠️ WS [192.168.1.100:54322] Invalid or expired token

2026-02-19 14:35:06 DEBUG    sunny_scada.services.alarm_broadcaster
🔌 WS [192.168.1.100:54322] (unknown) Unregistered from broadcaster
```

---

## Enabling Verbose Logging

To see debug-level logs in development, run with DEBUG enabled:

### Python
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

### Environment Variable
```bash
export LOGLEVEL=DEBUG
# or on Windows PowerShell:
$env:LOGLEVEL="DEBUG"
```

### In FastAPI startup
```python
logging.getLogger("sunny_scada").setLevel(logging.DEBUG)
```

---

## Log Levels Reference

| Level | Color | Use Case |
|-------|-------|----------|
| DEBUG | Gray | Detailed info (connections, broadcasts, snapshots) |
| INFO | Green | Important events (successful auth, disconnection) |
| WARNING | Yellow | Warning state raised, auth failures |
| ERROR | Red | Alarm state raised, rule eval errors |
| EXCEPTION | Red + Traceback | Unexpected errors with full stack trace |

---

## Symbols Used

| Symbol | Meaning |
|--------|---------|
| 🔴 | Alarm raised (critical state) |
| 🟡 | Warning raised |
| 🟢 | Acknowledged/OK state |
| 🔌 | WebSocket connection event |
| 📡 | Client registration/unregistration |
| 📤 | Broadcasting alarm event |
| ✓ | Successful send to clients |
| 📸 | Snapshot sent |
| 👋 | Client disconnected |
| ⚠️ | Warning/error condition |
| ✅ | Authenticated successfully |

---

## Monitoring for Developers

### Watch for Real-Time Alarms
```bash
# Terminal 1: Run server
python -m uvicorn sunny_scada.api.app:create_app --reload --log-level debug

# Terminal 2: Grep for alarm events
tail -f logs/app.log | grep "\[ALARM\]\|\[WARNING\]"
```

### Count Active WebSocket Connections
```bash
tail -f logs/app.log | grep "Active connections"
```

### Monitor Client Connections
```bash
tail -f logs/app.log | grep "Authenticated successfully\|Disconnected normally"
```

### Track Broadcasting Performance
```bash
tail -f logs/app.log | grep "Broadcasting\|\[client(s)\]"
```

---

## Performance Note

- DEBUG logs have minimal performance impact (only written if logger is at DEBUG level)
- INFO/WARNING/ERROR logs use efficient string formatting
- No performance degradation in production (set logging to INFO or higher)
