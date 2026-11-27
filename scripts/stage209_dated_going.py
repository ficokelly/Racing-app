"""Date-specific published going fallback; never replace nonblank provider going."""
import re
from datetime import datetime, timezone
DAY = '2026-11-28'
SOURCES = {
    'bangorondee': 'https://www.racingpost.com/racecards/4/bangor-on-dee/2026-11-28/',
    'newbury': 'https://www.racingpost.com/racecards/36/newbury/2026-11-28/',
}
SCHEDULE = {
    'bangorondee': {'11:50':'hurdle','12:25':'hurdle','13:00':'chase','13:33':'hurdle','14:10':'hurdle','14:45':'chase','15:25':'chase'},
    'newbury': {'12:00':'hurdle','12:35':'hurdle','13:10':'chase','13:40':'hurdle','14:20':'hurdle','15:00':'chase','15:38':'chase'},
}
VALUES = {
    ('bangorondee','hurdle'): ('Good','Good, Good to Soft in places'),
    ('bangorondee','chase'): ('Good to Soft','Good to Soft'),
    ('newbury','hurdle'): ('Good to Soft','Good to Soft, Good in places'),
    ('newbury','chase'): ('Good','Good, Good to Soft in places'),
}
def blank(x):
    return x is None or not str(x).strip()
def target(row):
    if str(row.get('date') or '')[:10] != DAY:
        return None
    course = re.sub(r'[^a-z]', '', str(row.get('course') or '').lower())
    course = {'bangor':'bangorondee'}.get(course,course)
    if course not in SCHEDULE:
        return None
    try:
        off = datetime.fromisoformat(str(row.get('race_time') or '').replace('Z','+00:00'))
        if off.tzinfo is None:
            return None
        clock = off.astimezone(timezone.utc).strftime('%H:%M')
    except ValueError:
        return None
    code = SCHEDULE[course].get(clock)
    if not code:
        return None
    kind = str(row.get('type') or '').lower()
    if code not in kind and not (code=='chase' and 'steeple' in kind):
        return None
    return course, clock, code
def apply(row):
    key = target(row)
    if not key or not blank(row.get('going')) or not blank(row.get('going_detailed')):
        return False
    course, clock, code = key
    row['going'], row['going_detailed'] = VALUES[(course,code)]
    row['going_source'] = SOURCES[course]
    row['going_source_date'] = '2026-11-27'
    row['going_provisional'] = True
    return True
