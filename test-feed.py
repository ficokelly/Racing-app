
import os

api_key = os.environ.get("PUNTERSEDGE_API_KEY", "")

if api_key:
    print("SUCCESS: PuntersEdge API key is available.")
else:
    print("ERROR: PuntersEdge API key is missing.")
    raise SystemExit(1)

