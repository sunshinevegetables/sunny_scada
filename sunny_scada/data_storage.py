from datetime import datetime
from threading import Lock

class DataStorage:
    def __init__(self):
        self.data = {}
        self.lock = Lock()

    def update_data(self, plc_name, plc_data):
        with self.lock:
            self.data[plc_name] = {
                "timestamp": datetime.now().isoformat(),
                "data": plc_data
            }

    def get_data(self):
        with self.lock:
            return self.data.copy()
