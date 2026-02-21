"""
Test script to fetch /plc_data endpoint with mock PLC data.
This verifies the endpoint works correctly when storage is populated.
"""
import requests
import json
import time
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

time.sleep(2)

try:
    # Login
    login_response = requests.post(
        "http://localhost:8000/api/auth/login",
        json={"username": "admin", "password": "admin"}
    )
    
    if login_response.status_code != 200:
        logger.error("Login failed: %s", login_response.status_code)
        logger.error("%s", login_response.json())
        exit(1)
    
    token = login_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    
    # First, let's check what's currently in storage
    logger.info("%s", "=" * 80)
    logger.info("Current /plc_data endpoint response:")
    logger.info("%s", "=" * 80)
    response = requests.get("http://localhost:8000/api/plc_data", headers=headers)
    data = response.json()
    logger.info("%s", json.dumps(data, indent=2))
    
    # Count null values
    def count_nulls(obj, path=""):
        count = 0
        if obj is None:
            return 1
        if isinstance(obj, dict):
            for k, v in obj.items():
                count += count_nulls(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                count += count_nulls(item, f"{path}[{i}]")
        return count
    
    null_count = count_nulls(data)
    logger.warning("Found %s null values in response", null_count)
    logger.warning("Possible causes:")
    logger.warning("1. PLC is not reachable (check network, IP, port)")
    logger.warning("2. Modbus connection failed")
    logger.warning("3. PollingService hasn't run yet (wait a few seconds)")
    logger.warning("4. Register addresses in YAML don't match actual PLC registers")
    
except Exception as e:
    import traceback
    logger.exception("Error: %s", e)
    traceback.print_exc()
