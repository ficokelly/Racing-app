#!/usr/bin/env python3
"""Guarded Stage70 Racing Hot Pots front-end and data-audit repair.

Never changes the D(c) scoring weights, historic selections, My Bets, P&L,
or published official results. Does not edit GitHub workflow files.
"""
from __future__ import annotations
import pathlib,re
ROOT=pathlib.Path.cwd()
MARKER='RHP_STAGE70_USER_REQUESTS_V1'

def edit(path,fn):
    file=ROOT/path
    old=file.read_text(encoding='utf-8');new=fn(old)
    if new!=old:
        file.write_text(new,encoding='utf-8');print('Updated:',path)
    else:print('Already current:',path)

def replace_one(s,old,new,label):
    count=s.count(old)
    if count==1:return s.replace(old,new,1)
    if count==0 and new in s:return s
    raise RuntimeError(label+': expected one anchor; found '+str(count))

def patch_index(s):
    if MARKER in s:return s
    if 'RHP_STAGE68_D_C_ONLY' not in s:raise RuntimeError('Stage69 D(c) app must be installed before Stage70')
    # Remove all automatic Watchlist additions (manual star buttons stay).
    pattern=r'function autoWatchQualifiers\(\)\{[\s\S]*?\n\}'
    s,count=re.subn(pattern,'function autoWatchQualifiers(){ /* Stage70: manual watchlist only; no automatic additions. */ return; }',s,count=1)
    if count!=1:raise RuntimeError('Watchlist auto-add rules anchor moved; no unsafe patch')
    # Navigation and Hot Pots duplicate promotional panel for Form vs Price.
    s,count=re.subn(r'<a class="form-price-nav"[^>]*>[^<]*</a>','',s,count=1)
    if count!=1:raise RuntimeError('Form vs Price nav link not found')
    s,count=re.subn(r'<div class="panel stage41-link"[^>]*>[\s\S]*?</div>','',s)
    if count!=2:raise RuntimeError('Expected Form vs Price cards in Selections and Results; found '+str(count))
    # Legacy C-only field-change guard had been reused by D(c). Keep the
    # published ranks for runner ordering, even before a new assessment runs.
    old="const valid=race&&!rhpCFieldChanged(race,sample.course,today,clock(sample.race_time))&&!rhpNonrunnerPending('D',{date:today,course:sample.course,time:clock(sample.race_time)});"
    s=replace_one(s,old,'const valid=!!race; // Stage70: keep last D(c) ranks during non-runner reassessment','Racecards rank-sort non-runner guard')
    # Disable the old overview helper's special field-changed suppression only
    # for D; the new Stage70 renderer also uses the last published ranking.
    old="if(rhpCFieldChanged(r,course,date,time))return {label:'Field changed',detail:'Reassessment required; old rank withheld'};"
    s=replace_one(s,old,"if(model!=='D'&&rhpCFieldChanged(r,course,date,time))return {label:'Field changed',detail:'Reassessment required; old rank withheld'};",'old overview ranking guard')
    css='''
/* RHP_STAGE70_USER_REQUESTS_V1 */
.form-price-nav,.stage41-link{display:none!important}
#rhp70-view-selector{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin:12px 0}
#rhp70-view-selector button{padding:11px 7px;min-height:46px;border:1px solid var(--rhp-edge);border-radius:11px;background:var(--rhp-card);color:var(--rhp-ink);font-weight:750}
#rhp70-view-selector button.active{background:var(--rhp-tint);box-shadow:inset 0 -3px 0 var(--rhp-focus)}
.rhp70-race{margin:0 0 12px;padding:14px!important}
.rhp70-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}
.rhp70-head h3{font-size:16px;margin:0 0 4px}
.rhp70-head p{font-size:12px;margin:0 0 8px}
.rhp70-pill{font-size:11px;padding:5px 8px;border-radius:9px;background:var(--rhp-tint);white-space:nowrap}
.rhp70-pair{display:grid;gap:8px;margin:10px 0}
.rhp70-pair-row{display:flex;gap:11px;align-items:center;border:1px solid var(--rhp-edge);border-radius:12px;padding:10px}
.rhp70-pair-row>div{min-width:0;flex:1}.rhp70-pair-row strong{font-size:15px}
.rhp70-rank{font-weight:800;display:inline-flex;align-items:center;justify-content:center;width:40px;min-width:40px;min-height:40px;border-radius:10px;background:var(--rhp-tint)}
.rhp70-pair-row small{display:block;color:var(--rhp-muted);font-size:12px;margin-top:4px}
.rhp70-pair-row.is-nr{opacity:.75;border-style:dashed}
.rhp70-pick,.rhp70-nr{display:inline-block;margin-left:5px;font-size:11px;font-weight:750}
.rhp70-nr{color:#d98742}.rhp70-next{padding:7px;font-size:12px;color:var(--rhp-muted)}
.rhp70-changed{padding:10px;border-left:3px solid #d99e3a;border-radius:5px;background:var(--rhp-tint);font-size:13px;line-height:1.5}
.rhp70-explain{font-size:14px;line-height:1.55}
'''
    s=replace_one(s,'</style></head><body>',css+'</style></head><body>','Stage70 styles')
    code=(ROOT/'scripts/rhp_stage70_ui.js').read_text(encoding='utf-8')
    s=replace_one(s,'\nsetup();\n// Manual refresh uses the existing state-preserving reload path',
       '\n'+code+'\nsetup();\n// Manual refresh uses the existing state-preserving reload path','Stage70 app control injection')
    return s

def patch_stage38_js(s):
    old="if(rhpCFieldChanged(r,course,date,time))return {label:'Field changed',detail:'Reassessment required; old rank withheld'};"
    if old in s:
        s=replace_one(s,old,"if(model!=='D'&&rhpCFieldChanged(r,course,date,time))return {label:'Field changed',detail:'Reassessment required; old rank withheld'};",'Stage38 ranking fallback')
    return s

def main():
    if not (ROOT/'index.html').exists() or not (ROOT/'scripts/rhp_stage68_dc.py').exists():
        raise SystemExit('Run Stage70 from the Stage69-enabled Racing-app root')
    edit('index.html',patch_index)
    edit('scripts/stage38_app.js',patch_stage38_js)
    print('Stage70 app and data audit ready. Original D and D(c) scoring are unchanged.')
if __name__=='__main__':main()
