
import os
import json
import urllib.request
import urllib.error

key = os.environ["PUNTERSEDGE_API_KEY"]

url = (
    "https://api.puntersedge.online/v1/racing/next-to-go"
    "?country=GB&category=horse"
)

request = urllib.request.Request(
    url,
    headers={"X-API-Key": key}
)

try:
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.load(response)

        print("HTTP status:", response.status)
        print(
            "Credits remaining:",
            response.headers.get("X-Credits-Remaining", "unknown")
        )

    if isinstance(data, dict):
        print("Response fields:", list(data.keys()))
        for field, value in data.items():
            if isinstance(value, list):
                print(field, "items:", len(value))
                print(
                    "Sample:",
                    json.dumps(value[:1], indent=2)[:4000]
                )
    elif isinstance(data, list):
        print("Items returned:", len(data))
        print("Sample:", json.dumps(data[:1], indent=2)[:4000])

except urllib.error.HTTPError as error:
    print("API request failed. HTTP status:", error.code)
    raise SystemExit(1)
