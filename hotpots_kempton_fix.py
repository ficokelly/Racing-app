#!/usr/bin/env python3
"""Minimal, idempotent display-only Hot Pots repair for Kempton/Kempton Park.
Run from the Racing-app repository root: python3 hotpots_kempton_fix.py
Does not alter the model's historical ranking or invent a selection.
"""
from pathlib import Path
import sys

path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('index.html')
if not path.is_file():
    raise SystemExit(f'Could not find {path}; run in your Racing-app repository root.')
s = path.read_text(encoding='utf-8')
replacements = [
    (
        "function courseMatchKey(c){return personKey(String(c||'').replace(/\\s*\\((?:AW|all[ -]?weather)\\)\\s*$/i,'').trim())}",
        "function courseMatchKey(c){const key=personKey(String(c||'').replace(/\\s*\\((?:AW|all[ -]?weather)\\)\\s*$/i,'').trim());return key==='kemptonpark'?'kempton':key}",
        'Canonicalize Kempton Park as Kempton across price/race matching',
    ),
    (
        "if(!race)return 'This race is on the card, but Hot Pots has not published its rankings yet.';",
        "if(!race||!ordered(race).length)return race?.reason||'This race is on the card, but Hot Pots has not published its rankings yet.';",
        'Display the real unranked reason rather than suggesting a ranking exists',
    ),
    (
        "function display(race){\n  const selected=race?.selection_status==='RESEARCH_ONLY'&&!!race.selection;",
        "function display(race){\n  const selected=race?.selection_status==='RESEARCH_ONLY'&&!!race.selection;\n  const hasRanking=ordered(race).length>0;",
        'Track whether a race has any published rankings',
    ),
    (
        "${selected?'Model selection':'Ranked only'}",
        "${selected?'Model selection':hasRanking?'Ranked only':'Awaiting ranking'}",
        'Distinguish pending assessment from ranked-only races',
    ),
    (
        "const picks=all.filter(r=>r.selection_status==='RESEARCH_ONLY').length,missing=all.filter(r=>r.selection_status==='ASSESSMENT_PENDING').length;",
        "const picks=all.filter(r=>r.selection_status==='RESEARCH_ONLY').length,missing=all.filter(r=>r.selection_status==='ASSESSMENT_PENDING').length;\n  const publishedRankingCount=all.filter(r=>ordered(r).length>0).length;",
        'Calculate the actual number of ranked races',
    ),
    (
        '${scored.length} races ranked',
        '${publishedRankingCount} races ranked',
        'Correct the ranked count in the Hot Pots summary',
    ),
]

new_s = s
changed = []
for old, new, description in replacements:
    if new in new_s:
        print('Already installed:', description)
    elif old in new_s:
        if new_s.count(old) != 1:
            raise SystemExit(f'Unsafe source: {description}: expected exactly one match, got {new_s.count(old)}; no changes written')
        new_s = new_s.replace(old, new, 1)
        changed.append(description)
    else:
        raise SystemExit(f'Source has changed: {description}; no changes written')

if new_s == s:
    print('Already fully installed; no change.')
else:
    if 'kemptonpark' not in new_s or 'Awaiting ranking' not in new_s:
        raise SystemExit('Verification failed; no changes written')
    path.write_text(new_s, encoding='utf-8')
    for item in changed:
        print('Fixed:', item)
    print(f'Saved {path}. Review with git diff, then commit/deploy.')
