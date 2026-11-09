#!/usr/bin/env python3
"""Guarded Stage69 installer. Changes current production UI to D(c) display and
reconciles eight externally verified missing finishes; leaves original D research
and real bet history unmodified. Does not edit .github/workflows.
"""
from __future__ import annotations
import json,pathlib,re
ROOT=pathlib.Path.cwd()

def edit(path,fn):
    p=ROOT/path;s=p.read_text(encoding='utf-8');new=fn(s)
    if new!=s:
        p.write_text(new,encoding='utf-8');print('updated',path)
    else:print('already current',path)

def one(s,old,new,reason,allow_done=True):
    count=s.count(old)
    if count==1:return s.replace(old,new,1)
    if count==0 and allow_done and new in s:return s
    raise RuntimeError('Stage69 cannot safely patch '+reason+' (count='+str(count)+')')

def patch_ui(s,html):
    if 'RHP_STAGE68_D_C_ONLY' in s:
        print('Existing D(c) UI detected; leave unchanged');return s
    # Stage68 already removed the confusing form coverage suffix; do not touch it.
    s=one(s," if(!feed||feed.day!==date)return null;", " if(!feed||!Array.isArray(feed.races))return null;",'multi-day D(c) race lookup')
    s=one(s, " const ts='?v='+Date.now(),files=['strategy-b-all-races.json','strategy-d-probabilities.json','strategy-z-probabilities.json',\n       'strategy-d-results.json','strategy-z-results.json','model-assessment-coverage.json'];",
          " const ts='?v='+Date.now(),files=['strategy-dc-probabilities.json','strategy-dc-results.json'];",'load only D(c) model')
    s=one(s,'  [stage38B,stage38D,stage38Z,stage38DResults,stage38ZResults,stage38Coverage]=p;',
       '  stage38B=null;stage38D=p[0];stage38Z=null;stage38DResults=p[1];stage38ZResults=null;stage38Coverage=null;','do not substitute older model D')
    s=one(s,' const raceRows=(feed?.day===target?feed.races||[]:[]).filter(r=>abTypeMatches(r,abViewState.selections.type));',
        ' const raceRows=(feed?.races||[]).filter(r=>r.date===target&&abTypeMatches(r,abViewState.selections.type));','day-scoped D(c) selections')
    if html:
        # Only the inlined Stage38 contains the full D summary and selection card.
        start=s.find(' if(model===\'D\'){\n  if(!race)return `<div class="rhp-verdict s38-verdict">')
        end=s.find(' if(model===\'B\'){',start)
        if start<0 or end<start:raise RuntimeError('Missing original frozen-D summary anchor')
        s=s[:start]+''' if(model==='D'){
  if(!race)return `<div class="rhp-verdict s38-verdict"><strong>Hot Pots · assessment pending</strong>${s38paragraph('The race-type-aware D(c) workflow has not yet published a pre-off assessment for this field. No old model is substituted.')}</div>`;
  const ranks=[...(race.runners||[])].sort((a,b)=>Number(a.rank)-Number(b.rank));
  const top=ranks[0],second=ranks[1];
  return `<div class="rhp-verdict s38-verdict"><strong>Hot Pots · race-type-aware assessment</strong>`+
   s38paragraph(top?`<strong>${s38esc(top.horse)}</strong> ranks first${second?`, ahead of <strong>${s38esc(second.horse)}</strong>`:''}, combining frozen historical D evidence with the separately trained race-type C form signals.`:'No supported ranking available.',
     top?`The relative research-score gap is ${Number(race.decision?.gap||0).toFixed(3)}. ${race.selection_status==='RESEARCH_ONLY'?'Research shortlist condition met.':'No shortlist qualified.'}`:'',
     'Scores are not calibrated winning chances and do not establish profitable ROI.')+'</div>';
 }
''' +s[end:]
        old='<p class="hint">Recorded ${s38esc(r.published_at||feed.generated_at)} · frozen score gap ${Number(r.decision?.gap||0).toFixed(3)}. No real bet placed.</p></article>'
        new='<p class="hint">Recorded ${s38esc(r.published_at||feed.generated_at)} · research score gap ${Number(r.decision?.gap||0).toFixed(3)}. No real bet placed.</p><div class="actions"><button type="button" class="action small outline" data-rhp-open-race="${encodeURIComponent(JSON.stringify({course:r.course,day:r.date,time:r.time}))}">View Race</button></div></article>'
        s=one(s,old,new,'View Race for every selected Hot Pot')
        js=(ROOT/'scripts/rhp_stage68_frontend.js').read_text()
        marker='\nsetup();\n// Manual refresh uses the existing state-preserving reload path'
        s=one(s,marker,'\n'+js+'\n'+marker,'D(c) results and full-race UI')
        css='''
/* RHP_STAGE68_D_C_ONLY: preserve research archives but display only D(c). */
#modelsview, #section-buttons [data-section="models"], [data-section="models"],
#section-buttons .model-z-nav, #powerview .panel:has(#power-type-toggle),
#powerview [data-stage40-paper], #powerview .stage41-link {display:none!important}
.rhp-result-finishers{display:grid;gap:7px;margin:10px 0}
.rhp-result-runner{display:flex;justify-content:space-between;align-items:baseline;gap:10px;padding:8px 10px;border:1px solid var(--rhp-edge);border-radius:8px}
.rhp-result-runner.is-picked{font-weight:750;border-width:2px}
.rhp-stale-feed{color:#e4a225!important}
'''
        s=one(s,'</style></head><body>',css+'</style></head><body>','hide archived model tabs')
        marker='// Preserve tab, selected filters, collapsed panels and scroll while published data refreshes.'
        s=one(s,marker,"for(const f of ['strategy-dc-probabilities.json','strategy-dc-results.json'])if(!AUTO_REFRESH_FILES.includes(f))AUTO_REFRESH_FILES.push(f);\n"+marker,'refresh D(c) feeds automatically')
        odds="$('status').textContent='● ODDS SYNCED';$('asof').textContent=`Last collected: ${when(latest.collected_at)} (Irish time) · ${latest.races_returned} races · ${latest.prices_collected} bookmaker quotes · ${hist.length} snapshots`;"
        newodds="const rhpOddsAge=(Date.now()-Date.parse(latest.collected_at))/60000;const rhpOddsStale=!Number.isFinite(rhpOddsAge)||rhpOddsAge>15;$('status').textContent=rhpOddsStale?'⚠ ODDS STALE':'● ODDS SYNCED';$('status').classList.toggle('rhp-stale-feed',rhpOddsStale);$('asof').textContent=`Last successful odds update: ${when(latest.collected_at)} (Irish time) · ${Number.isFinite(rhpOddsAge)?Math.floor(Math.max(0,rhpOddsAge))+' minutes ago':'time unavailable'} · ${latest.races_returned} races · ${latest.prices_collected} bookmaker quotes`;"
        if odds in s:s=s.replace(odds,newodds,1)
        elif newodds not in s:print('NOTICE: existing odds label changed, preserving new label')
        # Prevent stale historical model-tab selection from reopening hidden models.
        # Wrapper is installed before setup, after the existing showTab overrides.
        s=s.replace(marker,marker,1)
    return s

def patch_collector(s):
    if 'rhp_stage69_verified_results' in s:return s
    # Seven-day rolling retention erased official results needed for old selections.
    s=s.replace('timedelta(days=7)).date()','timedelta(days=30)).date()')
    point="kept.sort(key=lambda r: r.get('off_dt', ''), reverse=True)"
    add="""# Stage69: preserve externally verified PU/F/UR statuses after each provider refresh.
from scripts.rhp_stage69_verified_results import patch as apply_verified_race_finishes
corrected,not_found=apply_verified_race_finishes(kept,strict=False)
if corrected:print('Retained externally verified non-finisher statuses:',len(corrected))
"""
    return one(s,point,add+point,'preserve externally verified terminal results')

def patch_archive_reconciler(s):
    if 'STAGE69_TERMINAL_STATUS_RECOVERY' in s:return s
    needle="""                    for field in ('position', 'status', 'sp'):
                        if prior.get(field) in (None, '') and runner.get(field) not in (None, ''):
                            prior[field] = runner[field]
                            update = True"""
    replacement="""                    for field in ('position', 'status', 'sp'):
                        if prior.get(field) in (None, '') and runner.get(field) not in (None, ''):
                            prior[field] = runner[field]
                            update = True
                    # STAGE69_TERMINAL_STATUS_RECOVERY: A generic 'ran' must not
                    # obscure a separately verified non-completion event.
                    terminal={'pu','pulled up','f','fell','ur','unseated rider','bd','brought down','r','refused','ro','ran out'}
                    new_status=str(runner.get('status') or '').strip().lower()
                    old_status=str(prior.get('status') or '').strip().lower()
                    if (prior.get('position') is None and old_status in ('','ran','finished')
                            and new_status in terminal and old_status != new_status):
                        prior['status']=new_status
                        update=True"""
    return one(s,needle,replacement,'copy verified non-finisher codes into detailed form archive')

def main():
    if not (ROOT/'index.html').exists():raise SystemExit('Run from Racing-app repo root')
    for name in ['rhp_stage68_dc.py','rhp_stage68_settle.py','rhp_stage68_schedule.py','rhp_stage68_push.py','rhp_stage68_frontend.js','rhp_stage69_verified_results.py']:
        if not (ROOT/'scripts'/name).exists():raise RuntimeError('Bundle file not unpacked: '+name)
    edit('index.html',lambda s:patch_ui(s,True))
    edit('scripts/stage38_app.js',lambda s:patch_ui(s,False))
    edit('collect-puntersedge-results.py',patch_collector)
    edit('scripts/stage68_result_reconciler.py',patch_archive_reconciler)
    print('Stage69 guarded code edits installed; old D weights and My Bets unchanged.')
if __name__=='__main__':main()
