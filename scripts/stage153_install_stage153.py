#!/usr/bin/env python3
"""Installer invoked only by the user's explicit GitHub workflow_dispatch.

Fail closed before any commit if expected frozen-source patterns have changed.
Never modify model weights or training outputs, old D(c), historical results or odds.
"""
from pathlib import Path
import re

def replace(path, before, after, expected=1):
 p=Path(path);s=p.read_text(encoding='utf-8');n=s.count(before)
 if n!=expected:raise RuntimeError(f'{path}: expected {expected} copies, found {n} of {before[:95]!r}')
 p.write_text(s.replace(before,after),encoding='utf-8')
 print('PATCHED',path,before[:85])

def more_dates_expr(datevar):
 return (f"now_dublin=dt.datetime.now(__import__('zoneinfo').ZoneInfo('Europe/Dublin')).date()\n"
         f"    if {datevar} not in (now_dublin.isoformat(),(now_dublin+dt.timedelta(days=1)).isoformat()):"
         f"raise RuntimeError('Only today/tomorrow Dublin pre-off predictions are allowed')")

# Allow the previously frozen research functions to score NEXT DAY unresulted cards,
# using the same immutable prior-quarter research cutoff and no target-day outcomes.
p='scripts/stage141_today_or_weight_paper.py'
replace(p,"if date!=utcnow().date().isoformat():raise RuntimeError('No backdated paper selections')",
       more_dates_expr('date'))
for p in ('scripts/stage151_d142_core_form_or_weight_today.py',
          'scripts/stage152_best_roi_specialists_today.py'):
 s=Path(p).read_text()
 # Keep existing exact-function behaviour; only widen today's pre-off guard.
 if 'stage152' in p:
  replace(p,"if date!=today():\n        raise RuntimeError('Only current Dublin calendar day is permitted; cannot backdate predictions')",
      "now_dublin=dt.date.fromisoformat(today())\n    if date not in (now_dublin.isoformat(),(now_dublin+dt.timedelta(days=1)).isoformat()):\n        raise RuntimeError('Only today or tomorrow Dublin pre-off predictions are allowed')")
 else:
  replace(p,"if date != today():\n        raise RuntimeError('Only current Dublin calendar date can be selected: no backdated results')",
      "now_dublin=dt.date.fromisoformat(today())\n    if date not in (now_dublin.isoformat(),(now_dublin+dt.timedelta(days=1)).isoformat()):\n        raise RuntimeError('Only today or tomorrow Dublin pre-off predictions are allowed')")

p='scripts/stage147_daily_143_146.py'
replace(p,"if date!=TODAY or dt.datetime.now(DUBLIN).date().isoformat()!=TODAY:\n        raise RuntimeError('Live paper scorer refuses non-today target (Dublin local date)')",
       "now_local=dt.datetime.now(DUBLIN).date()\n    if date not in (now_local.isoformat(),(now_local+dt.timedelta(days=1)).isoformat()):\n        raise RuntimeError('Live paper scorer permits today or tomorrow only (Dublin local date)')")
# The fixed 2026Q4 source is untouched; quarter cutoff selection uses the actual requested date.
replace(p,"    date=a.date or now().date().isoformat()\n    a.out.mkdir(parents=True,exist_ok=True)",
       "    global TRAIN_THROUGH\n    date=a.date or now().astimezone(DUBLIN).date().isoformat()\n    TRAIN_THROUGH=previous_quarter_end(date)\n    a.out.mkdir(parents=True,exist_ok=True)")

# Stage152 already scores each full field; export those exact score arrays in
# rank order as well as Rank1, so withdrawals never silently switch models.
p='scripts/stage152_best_roi_specialists_today.py'
replace(p,"    rank1=[];skipped=collections.Counter()",
       "    rank1=[];all_runner_rows=[];skipped=collections.Counter()")
replace(p,"            top=int(np.argmax(scores))\n            rank1.append({",
       """            top=int(np.argmax(scores))
            for rank,idx in enumerate(np.argsort(-scores,kind='stable'),1):
                all_runner_rows.append({'date':date,'time_utc':c['off'].strftime('%H:%M'),
                    'meeting':r['course'],'race_id':rid,'race_name':r['race_name'],
                    'race_type':r['subtype'],'discipline':r['kind'],'model':name,
                    'rank':rank,'horse':r['horse'][idx],
                    'model_score_not_calibrated_probability':round(float(scores[idx]),6),
                    'field_size':len(r['horse']),'prediction_asof':L.utcnow().isoformat(),
                    'odds_used_for_current_prediction':False})
            rank1.append({""")
replace(p,"        top=int(np.argmax(scores))\n        rank1.append({",
       """        top=int(np.argmax(scores))
        for rank,idx in enumerate(np.argsort(-scores,kind='stable'),1):
            all_runner_rows.append({'date':date,'time_utc':c['off'].strftime('%H:%M'),
                'meeting':r['course'],'race_id':rid,'race_name':r['race_name'],
                'race_type':r['subtype'],'discipline':r['kind'],'model':name,
                'rank':rank,'horse':r['horse'][idx],
                'model_score_not_calibrated_probability':round(float(scores[idx]),6),
                'field_size':len(r['horse']),'prediction_asof':L.utcnow().isoformat(),
                'odds_used_for_current_prediction':False})
        rank1.append({""")
replace(p,"    L.csvwrite(args.out/'stage152-strongest-roi-specialists-rank1.csv',rank1)",
       "    L.csvwrite(args.out/'stage152-strongest-roi-specialists-rank1.csv',rank1)\n    L.csvwrite(args.out/'stage152-strongest-roi-specialists-all-runners.csv',all_runner_rows)")

# Individual workflows remain independently auditable, and the controller
# requests a date that may be tomorrow but never yesterday.
checks={
 '.github/workflows/stage141-today-or-weight-paper.yml':(
    'DAY="$(date -u +%F)"',
    'DAY="$(TZ=Europe/Dublin date +%F)"',
    "if [ \"$DAY\" != \"$TARGET\" ]; then",
    "NEXT=\"$(TZ=Europe/Dublin date -d tomorrow +%F)\"\n          if [ \"$DAY\" != \"$TARGET\" ] && [ \"$NEXT\" != \"$TARGET\" ]; then"),
 '.github/workflows/stage151-d142-core-form-or-weight-today.yml':(
    'CURRENT_DAY="$(TZ=Europe/Dublin date +%F)"',
    'CURRENT_DAY="$(TZ=Europe/Dublin date +%F)"',
    'if [ "$TARGET" != "$CURRENT_DAY" ]; then',
    'NEXT_DAY="$(TZ=Europe/Dublin date -d tomorrow +%F)"\n          if [ "$TARGET" != "$CURRENT_DAY" ] && [ "$TARGET" != "$NEXT_DAY" ]; then'),
 '.github/workflows/stage152-best-roi-specialists-today.yml':(
    'CURRENT="$(TZ=Europe/Dublin date +%F)"',
    'CURRENT="$(TZ=Europe/Dublin date +%F)"',
    'if [ "$TARGET" != "$CURRENT" ]; then',
    'NEXT="$(TZ=Europe/Dublin date -d tomorrow +%F)"\n          if [ "$TARGET" != "$CURRENT" ] && [ "$TARGET" != "$NEXT" ]; then'),
 '.github/workflows/stage147-daily-143-146.yml':(
    'export CURRENT_DAY="$(TZ=Europe/Dublin date +%F)"',
    'export CURRENT_DAY="$(TZ=Europe/Dublin date +%F)"',
    'if [ "$TARGET" != "$CURRENT_DAY" ]; then',
    'NEXT_DAY="$(TZ=Europe/Dublin date -d tomorrow +%F)"\n          if [ "$TARGET" != "$CURRENT_DAY" ] && [ "$TARGET" != "$NEXT_DAY" ]; then'),
}
for path,(keep,kept,before,after) in checks.items():
 if keep!=kept:replace(path,keep,kept)
 replace(path,before,after)

p='.github/workflows/stage152-best-roi-specialists-today.yml'
replace(p,"          import datetime as dt,json", "          import datetime as dt,json,os")
replace(p,"          today=dt.datetime.now(ZoneInfo('Europe/Dublin')).date().isoformat()",
       "          today=os.environ['TARGET_DAY']")
replace(p,"          test -s \"$OUT/stage152-strongest-roi-specialists-rank1.csv\"",
       "          test -s \"$OUT/stage152-strongest-roi-specialists-rank1.csv\"\n          test -s \"$OUT/stage152-strongest-roi-specialists-all-runners.csv\"")
replace(p,"            reports/stage152-specialists/paper/stage152-strongest-roi-specialists-rank1.csv",
       "            reports/stage152-specialists/paper/stage152-strongest-roi-specialists-rank1.csv\n            reports/stage152-specialists/paper/stage152-strongest-roi-specialists-all-runners.csv")

p='.github/workflows/stage147-daily-143-146.yml'
replace(p,"          import json,datetime as dt", "          import json,datetime as dt,os")
replace(p,"          date=dt.datetime.now(ZoneInfo('Europe/Dublin')).date().isoformat()",
       "          date=os.environ['TARGET_DAY']")
# Avoid extracting ~150 MB of scratch reference data in every scheduled hub run.
replace(p,"            reports/stage147-143-146/paper/*.csv",
       "            reports/stage147-143-146/paper/stage146-today-rank1-rank2.csv\n            reports/stage147-143-146/paper/stage146-today-all-runners.csv")

# Old D(c) paper runs remain archived, but must not send misleading
# push alerts for a model that is no longer displayed as Hot Pots.
p='.github/workflows/rhp-stage69-dc-live.yml'
replace(p,"      - name: Notify only for new or changed Hot Pots selections\n        if: steps.guard.outputs.run == 'true'",
       "      - name: Notify only for new or changed Hot Pots selections\n        if: ${{ false }}  # Stage153 owns the visible Hot Pots strategy")

# Show *only* the combined model in the current existing Hot Pots UI.
p='index.html'
replace(p,"files=['strategy-dc-probabilities.json','strategy-dc-results.json']",
       "files=['strategy-roi-combined.json','strategy-roi-combined-results.json']")
replace(p,"['strategy-dc-probabilities.json','strategy-dc-results.json']",
       "['strategy-roi-combined.json','strategy-roi-combined-results.json']")
s=Path(p).read_text()
assert 'D(c)' in s,'D(c) original Hot Pots label not found'
s=s.replace('D(c)','ROI Specialists')
s=s.replace('No first price','Opening price unavailable')
needle=" ensureControls();\n})();\n\nsetup();"
assert s.count(needle)==1,'Stage70 UI hook changed; cannot safely install combined commentary'
js="""
 // Stage153: only validated ROI specialists appear in the Hot Pots ranking.
 // Naturally worded, factual race commentary; never copy Racing Post text.
 rhpSelectedModelVerdict=function(sample,day){
  const race=getRace(sample.course,day,clock(sample.race_time));
  if(!race)return '<div class="rhp-verdict"><strong>ROI Specialists</strong><p>This race is awaiting its specialist assessment. No other model is substituted.</p></div>';
  if(!race.selection)return `<div class="rhp-verdict"><strong>ROI Specialists · pending</strong><p>${safe(race.reason||'A complete, current specialist ranking is not yet available.')}</p></div>`;
  const tip=race.nap_nb?`<p><strong>${safe(race.nap_nb)}</strong> · ${safe(race.source_model||'Specialist')} confidence pick</p>`:'';
  const prose=race.commentary||`${race.selection.horse} is the specialist's first choice.`;
  return `<div class="rhp-verdict"><strong>How Hot Pots sees this race</strong>${tip}<p>${safe(prose)}</p><p class="hint">Race-type specialist: ${safe((race.source_model||'').replaceAll('_',' '))} · Scores are rankings, not guaranteed probabilities.</p></div>`;
 };
 rhpSelectedHorseComment=function(r,x,day){
  const race=getRace(r.course,day,clock(r.race_time));
  if(!race||!race.selection)return 'The race-type specialist has not published a complete pre-off assessment of this field.';
  const h=ordered(race).find(v=>norm(v.horse)===norm(r.horse));
  if(!h)return `${safe(r.horse)} is not part of the currently verified model field. A runner change requires reassessment.`;
  const tip=race.nap_nb&&Number(h.rank)===1?`<strong>${safe(race.nap_nb)}.</strong> `:'';
  return tip+safe(h.commentary||'No individual form reasons have been verified for this runner.')+
    ` <small>Rank ${safe(h.rank)} of ${safe(race.field_size)} · ${safe((race.source_model||'').replaceAll('_',' '))}</small>`;
 };
 rhpSelectedModelBadge=function(horse,course,day,time){
  const race=getRace(course,day,time);
  const h=ordered(race).find(x=>norm(x.horse)===norm(horse));
  const isnr=h&&cardState(race,horse)==='nr';
  const tip=h&&Number(h.rank)===1&&race.nap_nb?' · '+race.nap_nb:'';
  return `<span class="rhp-rank-chip" title="${safe(race?.source_model||'ROI specialist not yet available')}">${h?'#'+safe(h.rank)+tip+(isnr?' · NR':''):'—'}</span>`;
 };
"""
s=s.replace(needle,js+needle)
Path(p).write_text(s)
print('PATCHED index.html ROI-only feed and original natural commentary')

# Settle the combined result feed when the primary 5-minute collector publishes
# a verified race; this never recomputes historic model decisions.
p='.github/workflows/racing-hot-pots-five-minute.yml'
s=Path(p).read_text()
if 'stage153_roi_combined.py settle' not in s:
 mark="      - name: Update and archive complete racecards once an hour"
 assert s.count(mark)==1
 block="""      - name: Update settled ROI specialists using final verified results
        shell: bash
        run: |
          if test -s data/strategy-roi-combined.json; then
            python scripts/stage153_roi_combined.py settle \\
              --output data/strategy-roi-combined.json \\
              --results data/race-results.json \\
              --settled data/strategy-roi-combined-results.json
          fi

"""
 s=s.replace(mark,block+mark)
 key="          if [ -f data/nonrunner-decision-history.json ]; then"
 assert s.count(key)==1
 s=s.replace(key,"          if [ -f data/strategy-roi-combined-results.json ]; then git add data/strategy-roi-combined-results.json; fi\n"+key)
 Path(p).write_text(s)
 print('PATCHED',p,'verified combined result settlement')

for path in ('scripts/stage141_today_or_weight_paper.py',
             'scripts/stage151_d142_core_form_or_weight_today.py',
             'scripts/stage152_best_roi_specialists_today.py',
             'scripts/stage147_daily_143_146.py'):
 import py_compile;py_compile.compile(path,doraise=True)
print('INSTALL_STAGE153_ALL_SOURCE_PATCHES_COMPILE_OK')
