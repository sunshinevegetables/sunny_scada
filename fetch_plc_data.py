import requests
import json
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

time.sleep(2)  # Wait for server to start

try:
    # Login
    login_response = requests.post(
        "http://localhost:8000/api/auth/login",
        json={"username": "admin", "password": "admin"}
    )
    logger.info("Login status: %s", login_response.status_code)
    login_data = login_response.json()
    logger.info("Login response keys: %s", list(login_data.keys()))
    
    token = login_data.get("access_token")
    if not token:
        logger.error("Full login response: %s", json.dumps(login_data, indent=2))
        raise ValueError("No access_token in login response")
    
    # Fetch PLC data
    headers = {"Authorization": f"Bearer {token}"}
    plc_response = requests.get("http://localhost:8000/api/plc_data", headers=headers)
    
    logger.info("PLC Data status: %s", plc_response.status_code)
    # Pretty print the response
    data = plc_response.json()
    logger.info("%s", json.dumps(data, indent=2))
    
except Exception as e:
    import traceback
    logger.exception("Error: %s", e)
    traceback.print_exc()
