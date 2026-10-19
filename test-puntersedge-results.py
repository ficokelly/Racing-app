"""Safe one-shot UK/Irish racing results test. Does not modify app data."""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

key = os.getenv('PUNTERSEDGE_API_KEY', '').strip()
if not key:
    sys.exit('PUNTERSEDGE_API_KEY secret is not set. Add it in GitHub Settings > Secrets and variables > Actions.')

params = urllib.parse.urlencode({
    'hours_back': 36,
    'categories': 'horse',
    'country': 'GB,IE',
    'limit': 200,
})
url = 'https://api.puntersedge.online/v1/racing/results?' + params
request = urllib.request.Request(url, headers={'X-API-Key': key, 'Accept': 'application/json'})
try:
    with urllib.request.urlopen(request, timeout=35) as response:
        data = json.load(response)
        remaining = response.headers.get('X-Credits-Remaining', 'unknown')
except urllib.error.HTTPError as err:
    print(f'PuntersEdge returned HTTP {err.code}. Check the key, account verification and quota.')
    sys.exit(1)
except Exception as err:
    sys.exit(f'Results request failed: {type(err).__name__}: {err}')

races = data if isinstance(data, list) else data.get('results', data.get('races', []))
if not isinstance(races, list):
    sys.exit('Unexpected API response structure; results is not a list.')
print(f'API request successful. GB/IE thoroughbred result records: {len(races)}. Credits remaining: {remaining}.')
print('This was a read-only test. No Racing Hot Pots files changed.')
for race in races[:5]:
    runners = race.get('runners') or []
    positioned = [r for r in runners if r.get('position') is not None]
    print(json.dumps({
        'venue': race.get('venue'),
        'country': race.get('country'),
        'race_number': race.get('race_number'),
        'start_time': race.get('start_time'),
        'result_status': race.get('status'),
        'field_status': race.get('field_status'),
        'runner_count': len(runners),
        'runners_with_position': len(positioned),
        'sample': [{'horse': r.get('name'), 'position': r.get('position'), 'sp_decimal': r.get('sp')}
                   for r in sorted(positioned, key=lambda r: r['position'])[:3]],
    }, ensure_ascii=False))
