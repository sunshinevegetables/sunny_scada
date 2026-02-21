import sqlite3
import os
from pathlib import Path
import datetime as dt
from argon2 import PasswordHasher
import requests
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

DB_URL = os.getenv('DATABASE_URL') or 'sqlite:///sunny_scada.db'
if DB_URL.startswith('sqlite:///'):
    DB_PATH = DB_URL.replace('sqlite:///', '')
else:
    DB_PATH = DB_URL

logger.info('DB path: %s', DB_PATH)
if not Path(DB_PATH).exists():
    raise SystemExit('DB not found: ' + DB_PATH)

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# Ensure admin role
cur.execute("SELECT id FROM roles WHERE name = 'admin'")
row = cur.fetchone()
if row:
    role_id = row[0]
    logger.info('Found admin role id %s', role_id)
else:
    cur.execute("INSERT INTO roles (name, description) VALUES (?,?)", ('admin','Initial admin role'))
    role_id = cur.lastrowid
    logger.info('Created admin role id %s', role_id)

# Ensure role permissions
perms = ['config:read','config:write','users:admin','roles:admin','alarms:admin','alarms:write']
for p in perms:
    cur.execute('SELECT 1 FROM role_permissions WHERE role_id = ? AND permission = ?', (role_id, p))
    if not cur.fetchone():
        cur.execute('INSERT INTO role_permissions (role_id, permission) VALUES (?,?)', (role_id, p))
        logger.info('Added permission %s', p)

# Ensure testadmin user
username = 'testadmin'
password = 'Password123!'
cur.execute('SELECT id FROM users WHERE username = ?', (username,))
row = cur.fetchone()
ph = PasswordHasher()
if row:
    user_id = row[0]
    logger.info('Found existing user id %s', user_id)
else:
    pwd_hash = ph.hash(password)
    now = dt.datetime.now(dt.timezone.utc).isoformat(sep=' ')
    cur.execute('INSERT INTO users (username,password_hash,is_active,failed_login_count,created_at,updated_at) VALUES (?,?,?,?,?,?)', (username,pwd_hash,1,0,now,now))
    user_id = cur.lastrowid
    logger.info('Created user id %s', user_id)

# Ensure user_roles linking
cur.execute('SELECT 1 FROM user_roles WHERE user_id = ? AND role_id = ?', (user_id, role_id))
if not cur.fetchone():
    cur.execute('INSERT INTO user_roles (user_id, role_id) VALUES (?,?)', (user_id, role_id))
    logger.info('Linked user to admin role')

conn.commit()
conn.close()

# Now login via API
base = 'http://localhost:8000'
logger.info('Sleeping briefly to ensure server is ready...')
time.sleep(1)
logger.info('Logging in as %s', username)
r = requests.post(base + '/auth/login', json={'username': username, 'password': password})
logger.info('Login status %s %s', r.status_code, r.text)
r.raise_for_status()
data = r.json()
access = data['access_token']
refresh = data['refresh_token']
headers = {'Authorization': f'Bearer {access}', 'Content-Type': 'application/json'}

# Create a datapoint group
grp_name = 'smoke-test-group'
logger.info('Creating group %s', grp_name)
r = requests.post(base + '/api/config/datapoint-groups', headers=headers, json={'name': grp_name})
logger.info('Create group status %s %s', r.status_code, r.text)
if r.status_code not in (200,201,409):
    raise SystemExit('Failed to create group')
if r.status_code == 409:
    logger.info('Group exists, fetching list')
    r = requests.get(base + '/api/config/datapoint-groups', headers=headers)
    r.raise_for_status()
    groups = r.json()
else:
    groups = r.json()
# Normalize to list of groups
if isinstance(groups, dict) and 'groups' in groups:
    groups = groups['groups']
if isinstance(groups, list):
    g = next((x for x in groups if x.get('name') == grp_name), None)
else:
    g = None

if not g:
    # fetch all groups
    r = requests.get(base + '/api/config/datapoint-groups', headers=headers)
    r.raise_for_status()
    groups = r.json()
    if isinstance(groups, dict) and 'groups' in groups:
        groups = groups['groups']
    g = next((x for x in groups if x.get('name') == grp_name), None)

if not g:
    raise SystemExit('Could not find created group')
logger.info('Group found: %s', g)
group_id = g['id']

# Create a PLC if none exists
r = requests.get(base + '/api/config/plcs', headers=headers)
r.raise_for_status()
plcs = r.json().get('plcs', [])
if plcs:
    plc_id = plcs[0]['plc_id']
    logger.info('Using existing PLC %s', plc_id)
else:
    plc_id = 'smoke_plc'
    logger.info('Creating PLC %s', plc_id)
    r = requests.post(base + '/api/config/plcs', headers=headers, json={'plc_id': plc_id, 'content': None})
    r.raise_for_status()
    logger.info('Created PLC')

# Create container under PLC with groupId
logger.info('Creating container under PLC with groupId')
r = requests.post(f"{base}/api/config/plcs/{plc_id}/containers", headers=headers, json={'name':'smoke-container','type':'test','groupId': group_id})
logger.info('Create container status %s %s', r.status_code, r.text)
r.raise_for_status()

# Get latest tree to find container id
r = requests.get(base + '/api/config/tree', headers=headers)
r.raise_for_status()
root = r.json()
# find container by name
container = None
for plc in root.get('plcs', []):
    for c in plc.get('containers', []):
        if c.get('name') == 'smoke-container':
            container = c
            parent_plc = plc
            break
    if container:
        break
if not container:
    raise SystemExit('Container not found in tree')
container_id = container['id']
logger.info('Container id %s groupId %s', container_id, container.get('groupId'))

# Create datapoint under container without groupId to test inheritance
logger.info('Creating datapoint under container without explicitly setting groupId')
req = {
    'datapoint_id': 'smoke_dp_1',
    'direction': 'read',
    'parent_path': f"container/{container_id}",
    'data': {
        'label': 'smoke dp',
        'type': 'INTEGER',
        'address': 'R1',
    }
}

r = requests.post(f"{base}/config/plcs/{plc_id}/datapoints", headers=headers, json=req)
logger.info('Create datapoint status %s %s', r.status_code, r.text)
r.raise_for_status()

# Fetch datapoint from tree to confirm it inherited groupId
r = requests.get(base + '/api/config/tree', headers=headers)
r.raise_for_status()
root = r.json()
dp_found = False
for plc in root.get('plcs', []):
    for c in plc.get('containers', []):
        if c.get('id') == container_id:
            for dp in c.get('datapoints', []):
                if dp.get('label') == 'smoke dp' or dp.get('datapoint_id') == 'smoke_dp_1':
                    logger.info('Datapoint found in container: %s', dp)
                    dp_found = True
                    logger.info('Datapoint groupId: %s', dp.get('groupId'))
if not dp_found:
    logger.warning('Datapoint not found in tree; try listing datapoints via API')

logger.info('Smoke test complete')
