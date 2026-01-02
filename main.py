"""
Entry point for Uvicorn.

Run:
  uvicorn main:app --host 0.0.0.0 --port 8000
"""
from sunny_scada.api.app import create_app

app = create_app()
