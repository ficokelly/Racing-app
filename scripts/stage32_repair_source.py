from pathlib import Path
import re

path = Path("index.html")
source = path.read_text(encoding="utf-8")
original = source
MARKER = '<!-- RHP_STAGE32_FORM_AND_MODEL_C_UI -->'
if MARKER in source:
    print('Stage 32 racecard UI already repaired; no changes needed')
    raise SystemExit(0)

def between(start, end, replacement, name):
    global source
    if source.count(start) != 1 or source.count(end) != 1:
        raise SystemExit(f"{name}: expected one matching anchor, got {source.count(start)} / {source.count(end)}")
    i = source.index(start)
    j = source.index(end, i + len(start))
    source = source[:i] + replacement.rstrip() + "\n" + source[j:]

def exact(old, new, name):
    global source
    c = source.count(old)
    if c == 0 and name in (
        "C ranked race verdict",
        "C individual horse commentary stale guard",
        "C horse win chance stale guard",
    ):
        corrected = re.sub(r"(?m)^ {2}", " ", old)
        if source.count(corrected) == 1:
            old = corrected
            c = 1
    if c != 1:
        raise SystemExit(f"{name}: expected exactly one target, found {c}")
    source = source.replace(old, new, 1)

between(
    "function formStats(row,type){",
    "function indexFormNames(raw){",
    """function formStats(row,type){
  const person=row[type],selectedDays=type==='trainer'?trainerDays:jockeyDays;
  if(!person||!formData||!formData.periods)return null;
  const asOf=formData.as_of,raceDay=row.day;
  if(!/^\\d{4}-\\d{2}-\\d{2}$/.test(asOf||'')||!/^\\d{4}-\\d{2}-\\d{2}$/.test(raceDay||''))return null;
  const prior=new Date(raceDay+'T12:00:00Z');prior.setUTCDate(prior.getUTCDate()-1);
  if(asOf>prior.toISOString().slice(0,10))return null;
  // Archived days without published results do not erase verified runs from other days.
  // Use the longest published requested window; disclose how many calendar dates have
  // verified race results rather than inventing a zero-run day for a gap.
  let availableDays=0,period=null,calendarDays=0;
  for(let n=Math.min(14,selectedDays);n>=1;n--){
    const candidate=formData.periods[String(n)];
    if(!candidate||!Array.isArray(candidate.dates_available))continue;
    const minDate=new Date(asOf+'T12:00:00Z');minDate.setUTCDate(minDate.getUTCDate()-(n-1));
    const lower=minDate.toISOString().slice(0,10);
    const verified=new Set(candidate.dates_available.filter(d=>typeof d==='string'&&d>=lower&&d<=asOf));
    if(!verified.size)continue;
    period=candidate;availableDays=verified.size;calendarDays=n;break;
  }
  if(!period)return null;
  const group=type==='trainer'?'trainers':'jockeys',directId=row[type+'_id'];
  let stat=directId?period[group]?.[directId]:null;
  if(!stat){
    const matches=formIndex[type][formName(person)]||[];
    if(matches.length!==1)return null;
    stat=period[group]?.[matches[0]]||null;
  }
  if(!stat)return null;
  const runs=Number(stat.runs??stat.rides),wins=Number(stat.wins);
  if(!Number.isFinite(runs)||runs<=0||!Number.isFinite(wins)||wins<0||wins>runs)return null;
  return {wins,runs,pct:100*wins/runs,availableDays,selectedDays,calendarDays,partialCoverage:availableDays<calendarDays||period.coverage_complete===false};
}
""",
    "form stats partial-window repair"
)

exact(
    "const value=stat?`${stat.wins}/${stat.runs} · ${stat.pct.toFixed(1)}% (${stat.availableDays}d)`:'Form unknown';",
    "const value=stat?`${stat.wins}/${stat.runs} · ${stat.pct.toFixed(1)}% (${stat.availableDays}/${stat.calendarDays}d recorded${stat.partialCoverage?' · partial coverage':''})`:'Form unavailable';",
    "racecard people display"
)
exact(
    "formData=raw;indexFormNames(raw);autoWatchQualifiers();renderPower();renderMarkets();renderHot();",
    "formData=raw;indexFormNames(raw);autoWatchQualifiers();renderPower();renderMarkets();renderHot();if(activeTab==='racecards')renderRacecards();",
    "rerender form after async fetch"
)
# Keep C's original published percentages; don't invent new probabilities after withdrawals.
c_field = """function rhpCFieldChanged(race,course,date,time){
  if(!race||!Array.isArray(race.runners)||!race.runners.length)return false;
  const current=racecardRunners.filter(r=>(r.date||dateKey(r.race_time))===date&&
    courseMatchKey(r.course)===courseMatchKey(course)&&clock(r.race_time)===time);
  if(!current.length)return false;
  const active=current.filter(r=>r.non_runner!==true&&r.is_non_runner!==true&&
    String(r.number||'').toUpperCase()!=='NR'&&
    !['nr','nonrunner','withdrawn','scratched'].includes(String(r.runner_status||r.status||'').toLowerCase().replace(/[^a-z]/g,'')));
  const before=new Set(race.runners.map(r=>horseMatchKey(r.horse)).filter(Boolean));
  const now=new Set(active.map(r=>horseMatchKey(r.horse)).filter(Boolean));
  return before.size!==now.size||[...now].some(x=>!before.has(x));
}
"""
exact("function rcModelRace(course,date,time){", c_field+"function rcModelRace(course,date,time){", "C field-change guard")
exact(
    "if(rcSelectedModel==='C')return strategyCFeed?.version===2&&strategyCFeed?.day===date?(strategyCFeed.races||[]).find(r=>commentaryKey(r.course,r.date,r.time)===target):null;",
    "if(rcSelectedModel==='C')return strategyCFeed?.version===2&&strategyCFeed?.day===date?(strategyCFeed.races||[]).find(r=>commentaryKey(r.course,r.date,r.time||clock(r.race_time))===target):null;",
    "C race-match normalisation"
)
# Show a real ranked summary and explicit selection/no-selection on Model C racecards.
exact(
    """  if(!race)return '<div class="rhp-verdict"><strong>Model C · independent research</strong><p>New Model C v2 assessment not yet published for this field. It requires a completed Stage 22 full-history run and a complete current racecard.</p></div>';
  const sourceLinks=(race.decision?.external_sources||[]).slice(0,2).map(x=>`<a href="${safe(x.source_url)}" target="_blank" rel="noopener noreferrer">External source</a>`).join(' · ');
  return `<div class="rhp-verdict"><strong>Model C v2 · full-history and independent external research</strong><p>${safe(race.verdict||'Assessment not available')}</p>${sourceLinks?`<p class="hint">Sources: ${sourceLinks}</p>`:''}<p class="hint">This version has a new independent 2025 holdout test. Historical SP profit is not proof of live profit.</p></div>`;""",
    """  if(!race)return '<div class="rhp-verdict"><strong>Model C · awaiting published race prediction</strong><p>No same-day Model C v2 assessment was found for this race. We do not fill it with Model A or market odds.</p></div>';
  if(rhpCFieldChanged(race,sample.course,date,time)){
    const prior=(race.runners||[]).filter(x=>x.win_probability!=null&&Number.isFinite(Number(x.win_probability))).sort((a,b)=>Number(b.win_probability)-Number(a.win_probability)).slice(0,3);
    const old=prior.map((x,i)=>`${i+1}. ${safe(x.horse)} ${(100*Number(x.win_probability)).toFixed(1)}%`).join(' · ');
    return `<div class="rhp-verdict"><strong>Model C · reassessment required</strong><p>The runner list has changed since this Model C calculation. An official selection cannot be inferred from the old field.</p><p class="hint"><strong>Earlier snapshot only (not current):</strong> ${old||'No recorded ranking'}. Re-run Stage 22 for updated chances.</p></div>`;
  }
  const ranked=(race.runners||[]).filter(x=>x.win_probability!=null&&Number.isFinite(Number(x.win_probability))).sort((a,b)=>Number(b.win_probability)-Number(a.win_probability));
  const leaders=ranked.slice(0,3).map((x,i)=>`${i+1}. ${safe(x.horse)} ${(100*Number(x.win_probability)).toFixed(1)}%`).join(' · ');
  const official=race.selection_status==='SELECTED'&&race.selection?`Official C selection: <strong>${safe(race.selection.horse)}</strong>.`:'No official Model C selection met the policy thresholds in this race.';
  const sourceLinks=(race.decision?.external_sources||[]).slice(0,2).map(x=>`<a href="${safe(x.source_url)}" target="_blank" rel="noopener noreferrer">External source</a>`).join(' · ');
  return `<div class="rhp-verdict"><strong>Model C v2 · independent win probabilities</strong>${leaders?`<p><strong>Highest rated:</strong> ${leaders}</p>`:''}<p>${safe(race.verdict||'Assessment not available')}</p><p class="hint">${official} These estimated chances are experimental and are not proof of betting value.</p>${sourceLinks?`<p class="hint">Sources: ${sourceLinks}</p>`:''}</div>`;""",
    "C ranked race verdict"
)
exact(
    """  if(rcSelectedModel==='C'){
    const prob=Number(horse.win_probability);""",
    """  if(rcSelectedModel==='C'){
    const modelRace=rcModelRace(r.course,date,clock(r.race_time));
    if(rhpCFieldChanged(modelRace,r.course,date,clock(r.race_time)))return 'Model C is re-assessing this race after the runner list changed. Prior probabilities are withheld.';
    const prob=Number(horse.win_probability);""",
    "C individual horse commentary stale guard"
)
exact(
    """  const v=Number(row?.win_probability);
  return `<span class="rhp-win-chance" title="Experimental estimate. Model C has not demonstrated positive ROI.">Model C win ${row&&Number.isFinite(v)?(100*v).toFixed(1)+'%':'—'}</span>`;""",
    """  const modelRace=rcModelRace(course,date,time);
  if(rhpCFieldChanged(modelRace,course,date,time))return '<span class="rhp-win-chance" title="Racecard has changed since the Model C run">Model C reassessment pending</span>';
  const v=Number(row?.win_probability);
  return `<span class="rhp-win-chance" title="Experimental estimate; not a bookmaker-implied probability or betting recommendation.">Model C win ${row&&row.win_probability!=null&&Number.isFinite(v)?(100*v).toFixed(1)+'%':'—'}</span>`;""",
    "C horse win chance stale guard"
)
exact(
    "if(model==='C')return strategyCFeed?.version===2?strategyCFeed.races.map(r=>({...r,status:'UPCOMING',model:'C',model_validation:'HISTORICALLY_TRAINED_V2',historical_editions:r.historical_examples,",
    "if(model==='C')return strategyCFeed?.version===2?strategyCFeed.races.map(r=>({...r,time:r.time||clock(r.race_time),status:'UPCOMING',model:'C',model_validation:'HISTORICALLY_TRAINED_V2',historical_editions:r.historical_examples,",
    "C selection card time fallback"
)
exact(
    "const selected=filtered.filter(r=>!rhpNonrunnerPending(state.model,r)&&r.selection&&r.selection_status==='SELECTED'&&r.status==='UPCOMING');",
    "const selected=filtered.filter(r=>!rhpNonrunnerPending(state.model,r)&&!(state.model==='C'&&rhpCFieldChanged(r,r.course,r.date,r.time||clock(r.race_time)))&&r.selection&&r.selection_status==='SELECTED'&&r.status==='UPCOMING');",
    "withhold changed-field official C selections"
)
# A non-runner indicated by racecard number must not appear as an eligible current runner.
exact(
    "r.non_runner===true||r.is_non_runner===true||['nr','nonrunner','withdrawn','scratched'].includes",
    "r.non_runner===true||r.is_non_runner===true||String(r.number||'').toUpperCase()==='NR'||['nr','nonrunner','withdrawn','scratched'].includes",
    "exclude numbered non-runners"
)
if source == original:
    raise SystemExit("No changes applied")
if source.count('</head>')!=1:
    raise SystemExit('Stage 32 UI cannot mark patch version safely')
source=source.replace('</head>',MARKER+'</head>',1)
path.write_text(source, encoding="utf-8")
print("Stage 32 patch applied:", len(original), "->", len(source), "characters")
print("Index remains separate from all model data and betting records.")
