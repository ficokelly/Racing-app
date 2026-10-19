"""Optional results collector for Racing Hot Pots.
Requires The Racing API Basic plan (or above) for /v1/results/today.
Output consumed by Racecards: data/race-results.json.
Run in GitHub Actions with existing RACING_API_USERNAME and RACING_API_PASSWORD secrets.
"""
import base64
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

URL = 'https://api.theracingapi.com/v1/results/today'
credentials = base64.b64encode((os.environ['RACING_API_USERNAME'] + ':' + os.environ['RACING_API_PASSWORD']).encode()).decode()
headers = {'Authorization': 'Basic ' + credentials, 'Accept': 'application/json'}
results = []
for skip in range(0, 5000, 50):
    address = URL + '?' + urllib.parse.urlencode({'limit':50,'skip':skip})
    with urllib.request.urlopen(urllib.request.Request(address, headers=headers),timeout=45) as response:
        payload=json.load(response)
    chunk=payload.get('results', [])
    results.extend(chunk)
    if len(chunk)<50: break
    time.sleep(0.3)

path=Path('data/race-results.json')
try: existing=json.loads(path.read_text())
except (OSError,ValueError): existing={}
by_id={}
for r in existing.get('races', []):
    if r.get('race_id'): by_id[r['race_id']]=r
for r in results:
    if str(r.get('region','')).upper() not in ('GB','IRE','GBR','IRL'):
        continue
    runner_items=r.get('runners') or []
    rows=[]
    for horse in runner_items:
        rows.append({
            'horse':horse.get('horse') or horse.get('horse_name'),
            'position':horse.get('position') or horse.get('pos') or horse.get('finish_position'),
            'status':horse.get('status'),
            'sp':horse.get('sp') or horse.get('sp_frac') or horse.get('starting_price')
        })
    # Do not replace already complete data with an incomplete upstream response.
    if not any(x.get('position') for x in rows):continue
    item={'race_id':r.get('race_id'),'date':r.get('date'),
          'course':r.get('course'),'off_dt':r.get('off_dt'),'runners':rows}
    if item['race_id']:by_id[item['race_id']]=item
path.parent.mkdir(parents=True,exist_ok=True)
path.write_text(json.dumps({'collected_at':datetime.now(timezone.utc).isoformat(),
                            'races':list(by_id.values())},indent=2))
print(f'Saved {len(by_id)} verified races; {len(results)} race result records scanned')
