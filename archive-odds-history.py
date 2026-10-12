"""Bound live odds history and preserve older snapshots in small dated archives.

Run after collect-odds.py and before git add/push. Uses no API credits.
"""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

DATA = Path('data')
LIVE = DATA / 'odds-history.json'
ARCHIVE = DATA / 'odds-archive'
MAX_LIVE_BYTES = 22_000_000
MAX_ARCHIVE_BYTES = 15_000_000


def timestamp(s):
    try:
        d = datetime.fromisoformat(str(s).replace('Z', '+00:00'))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


def identity(s):
    return str(s.get('collected_at', ''))


def json_bytes(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_bytes(json_bytes(obj))
    temp.replace(path)


def main():
    if not LIVE.exists():
        raise SystemExit('No odds history to archive')
    history = json.loads(LIVE.read_text(encoding='utf-8'))
    if not isinstance(history, list):
        raise ValueError('Odds history must be a list')
    history = sorted((s for s in history if isinstance(s, dict)), key=lambda s: timestamp(s.get('collected_at')))
    # Keep the latest 24 hours when possible, but always enforce the size cap.
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    removed = []
    while len(history) > 1 and (timestamp(history[0].get('collected_at')) < cutoff or len(json_bytes(history)) > MAX_LIVE_BYTES):
        removed.append(history.pop(0))
    if len(json_bytes(history)) > MAX_LIVE_BYTES:
        raise RuntimeError('A single snapshot exceeds the live-history size limit; refusing to discard it')

    # Archive snapshots into date-specific, size-limited numbered parts. Preserve
    # existing archived snapshots and deduplicate by collected_at on every run.
    by_day = {}
    for s in removed:
        day = timestamp(s.get('collected_at')).strftime('%Y-%m-%d')
        by_day.setdefault(day, []).append(s)
    for day, additions in by_day.items():
        existing_paths = sorted(ARCHIVE.glob(f'{day}-part-*.json'))
        combined = {}
        for p in existing_paths:
            for snap in json.loads(p.read_text(encoding='utf-8')):
                combined[identity(snap)] = snap
        for snap in additions:
            combined[identity(snap)] = snap
        snapshots = sorted(combined.values(), key=lambda s: timestamp(s.get('collected_at')))
        parts, part = [], []
        for snap in snapshots:
            if len(json_bytes([snap])) > MAX_ARCHIVE_BYTES:
                raise RuntimeError('Single snapshot exceeds archive part size limit')
            if part and len(json_bytes(part + [snap])) > MAX_ARCHIVE_BYTES:
                parts.append(part)
                part = []
            part.append(snap)
        if part:
            parts.append(part)
        for i, content in enumerate(parts, 1):
            write_json(ARCHIVE / f'{day}-part-{i:03d}.json', content)
        for p in existing_paths:
            if p.name not in {f'{day}-part-{i:03d}.json' for i in range(1, len(parts)+1)}:
                p.unlink()
    write_json(LIVE, history)
    print(f'Archived {len(removed)} snapshots; {len(history)} remain in live history ({LIVE.stat().st_size:,} bytes)')
    print(f'Archive directory: {ARCHIVE}')


if __name__ == '__main__':
    main()
