// Stage38: integrated, labelled A/B/C/D/Z independent race assessment views.
// Append into existing classic script only after all existing declarations, before setup().
let stage38B=null,stage38D=null,stage38Z=null,stage38DResults=null,stage38ZResults=null,stage38Coverage=null;
function s38esc(s){return safe(String(s??''));}
function s38key(c,d,t){return commentaryKey(c,d,t);}
function s38find(feed,course,date,time){
 if(!feed||feed.day!==date)return null;
 return (feed.races||[]).find(r=>s38key(r.course,r.date,r.time||clock(r.race_time))===s38key(course,date,time))||null;
}
function s38field(r,course,date,time){return !!r && !rhpCFieldChanged(r,course,date,time) && !rhpNonrunnerPending(rcSelectedModel,{course,date,time});}
async function s38load(){
 const ts='?v='+Date.now(),files=['strategy-b-all-races.json','strategy-d-probabilities.json','strategy-z-probabilities.json',
       'strategy-d-results.json','strategy-z-results.json','model-assessment-coverage.json'];
 try{
  const p=await Promise.all(files.map(f=>fetch('./data/'+f+ts,{cache:'no-store'}).then(r=>r.ok?r.json():null).catch(()=>null)));
  [stage38B,stage38D,stage38Z,stage38DResults,stage38ZResults,stage38Coverage]=p;
  if(activeTab==='racecards')renderRacecards();
  if(activeTab==='power')renderStrategySelections();
  if(activeTab==='powerresults')renderStrategyResults();
 }catch(e){console.warn('Model coverage feed not available',e)}
}
function s38formSignal(row){
 const s=String(row?.evidence_summary||'');
 const match=s.match(/(\d+) wins? and (\d+) top-three finishes? in (\d+) linked prior starts?/i);
 const recent=s.match(/(\d+) wins? and (\d+) top-three finishes? in the last (\d+) recorded runs?/i);
 const co=s.match(/at this course, (\d+)\/(\d+) wins?/i);
 const dist=s.match(/at similar distances, (\d+)\/(\d+) wins?/i);
 const parts=[];
 if(match)parts.push(`Has ${match[1]} win${match[1]==='1'?'':'s'} from ${match[3]} verified previous start${match[3]==='1'?'':'s'}, with ${match[2]} top-three finishes.`);
 if(recent)parts.push(`Recent evidence: ${recent[1]} win${recent[1]==='1'?'':'s'} and ${recent[2]} places from the last ${recent[3]} recorded runs.`);
 if(co)parts.push(`Course record: ${co[1]} win${co[1]==='1'?'':'s'} from ${co[2]} run${co[2]==='1'?'':'s'}.`);
 if(dist)parts.push(`At comparable trips: ${dist[1]} win${dist[1]==='1'?'':'s'} from ${dist[2]} recorded starts.`);
 return parts;
}
function s38paragraph(...items){
 return items.filter(Boolean).map((s,i)=>`<span class="s38-para${i?' s38-after':''}">${s}</span>`).join('');
}
function s38raceSummary(race,model,sample,date,time){
 const label=`Model ${model}`;
 const key=s38key(sample.course,date,time);
 const cardName=s38esc(sample.course);
 const changes=!!race&&rhpCFieldChanged(race,sample.course,date,time);
 if(changes||rhpNonrunnerPending(model,{date,course:sample.course,time}))return `<div class="rhp-verdict s38-verdict"><strong>${label} · revised field needed</strong>${s38paragraph('A withdrawal or declaration changed the runners. The previous ranking is retained as an old snapshot, not a current prediction.','The independent model must reassess the remaining field before a selection is shown.')}</div>`;
 if(model==='Z'){
  if(!race)return `<div class="rhp-verdict s38-verdict"><strong>Model Z · no specialist qualification</strong>${s38paragraph('Model Z has checked this race for its registered specialist courses. None currently applies, so no Z horse is nominated.','The Newmarket and Dundalk strategies remain frozen research candidates. They are not approved live betting systems.')}</div>`;
  const a=[...(race.runners||[])].sort((x,y)=>Number(x.rank)-Number(y.rank));
  const top=a[0],second=a[1];
  return `<div class="rhp-verdict s38-verdict"><strong>Model Z · ${s38esc(race.strategy_name||'Frozen specialist')}</strong>`+
   s38paragraph(top?`<strong>${s38esc(top.horse)}</strong> heads the frozen course-specific ranking${second?`, ahead of <strong>${s38esc(second.horse)}</strong>`:''}.`:'No verified leader has been published.',
   race.selection_status==='PAPER_ONLY'?'The fixed score-gap condition is met: this is a timestamped <strong>paper-only</strong> qualifier, not a real bet.':'This specialist did not reach its frozen threshold, so there is no paper selection.',
   'These are relative research scores, not calibrated win probabilities. No strategy was automatically promoted.')+'</div>';
 }
 if(model==='D'){
  if(!race)return `<div class="rhp-verdict s38-verdict"><strong>Model D · forecast pending</strong>${s38paragraph('The original eight-factor scoring engine has not yet published a valid full-field assessment for this race.','Its research weights remain unchanged; no probability or leader is invented from another model.')}</div>`;
  const a=[...(race.runners||[])].sort((x,y)=>Number(x.rank)-Number(y.rank));
  const top=a[0],next=a[1];
  return `<div class="rhp-verdict s38-verdict"><strong>Model D · eight-factor assessment</strong>`+
    s38paragraph(top?`<strong>${s38esc(top.horse)}</strong> comes out highest on the independent historical factors${next?`, with <strong>${s38esc(next.horse)}</strong> next in the order`:''}.`:'Full-field assessment was incomplete.',
      top?`The score gap is ${Number(race.decision?.gap||0).toFixed(3)}, against the frozen research cut-off of ${Number(race.decision?.minimum_gap||0).toFixed(2)}. ${race.selection_status==='RESEARCH_ONLY'?'The research shortlist threshold is met.':'No betting selection clears the threshold.'}`:'',
      'Model D scores are not winning percentages. Its historical selected-bet ROI has not shown a reliably profitable edge.')+'</div>';
 }
 if(model==='B'){
  if(!race)return `<div class="rhp-verdict s38-verdict"><strong>Model B · historical evidence pending</strong>${s38paragraph('The whole field is included in the assessment queue, but verified form and comparable-race evidence are not available for a responsible order.','No ranking or betting selection is fabricated.')}</div>`;
  const list=[...(race.runners||[])].filter(x=>Number.isFinite(Number(x.research_score??x.score)) && (x.research_score??x.score)!=null).sort((a,b)=>Number(b.research_score??b.score)-Number(a.research_score??a.score));
  const a=list[0],b=list[1];
  const provisional=race.state==='PROVISIONAL_FORM_RANKING'||race.partial_research;
  return `<div class="rhp-verdict s38-verdict"><strong>Model B · ${provisional?'provisional form ranking':'independent race ranking'}</strong>`+
    s38paragraph(a?`<strong>${s38esc(a.horse)}</strong> leads the available verified form${b?`, with <strong>${s38esc(b.horse)}</strong> the next closest on the research ordering`:''}.`:'No runner has sufficient verified evidence for a defensible ranking.',
       a?`The assessment includes ${list.length} of ${race.field_size||race.runners?.length||'the'} runners with supported scores. ${provisional?'The full named-race comparison is incomplete, so this is a form-led view rather than a calibrated Model B forecast.':'All reported scores come from the independent B research feed.'}`:'',
       'A research ranking is not a betting recommendation. No official B selection is inferred solely from incomplete evidence.')+'</div>';
 }
 if(model==='C'){
  if(!race)return `<div class="rhp-verdict s38-verdict"><strong>Model C · complete-field assessment pending</strong>${s38paragraph('Model C has not published a valid independent ranking for this race. No A, B or market prices have been used as substitutes.','The normal C scoring workflow will need to publish a fresh field assessment before an estimated win chance can be shown.')}</div>`;
  const a=[...(race.runners||[])].filter(x=>x.win_probability!=null).sort((x,y)=>Number(y.win_probability)-Number(x.win_probability));
  return `<div class="rhp-verdict s38-verdict"><strong>Model C · independent win-probability assessment</strong>`+
    s38paragraph(a.length?`<strong>${s38esc(a[0].horse)}</strong> leads at ${(100*Number(a[0].win_probability)).toFixed(1)}%, ${a[1]?`ahead of <strong>${s38esc(a[1].horse)}</strong> at ${(100*Number(a[1].win_probability)).toFixed(1)}%`:''}.`:'No calibrated probability available.',
     race.selection_status==='SELECTED'?`A separate, experimental C betting gate has also qualified <strong>${s38esc(race.selection?.horse||'the leader')}</strong>.`:'No C selection met the separate betting conditions.',
     'These are experimental model estimates, not guaranteed outcomes.')+'</div>';
 }
 // A existing validated summary is authoritative; standardise typography only.
 const old=s38OldVerdict(sample,date,[]);
 return old.replace('class="rhp-verdict"','class="rhp-verdict s38-verdict"');
}
const s38OldModelRace=rcModelRace;
rcModelRace=function(course,date,time){
 if(rcSelectedModel==='D')return s38find(stage38D,course,date,time);
 if(rcSelectedModel==='Z')return s38find(stage38Z,course,date,time);
 if(rcSelectedModel==='B')return s38find(stage38B,course,date,time)||s38OldModelRace(course,date,time);
 return s38OldModelRace(course,date,time);
};
const s38OldVerdict=rhpSelectedModelVerdict;
rhpSelectedModelVerdict=function(sample,date,field){
 if(rcSelectedModel==='A'){
  const html=s38OldVerdict(sample,date,field);
  return html.replace('class="rhp-verdict"','class="rhp-verdict s38-verdict"');
 }
 return s38raceSummary(rcModelRace(sample.course,date,clock(sample.race_time)),rcSelectedModel,sample,date,clock(sample.race_time));
};
const s38OldComment=rhpSelectedHorseComment;
rhpSelectedHorseComment=function(r,x,date){
 const model=rcSelectedModel,tm=clock(r.race_time),n=`<strong>${s38esc(r.horse)}</strong>`;
 const mr=rcModelRace(r.course,date,tm);
 if(model!=='A'&&(!mr||rhpCFieldChanged(mr,r.course,date,tm)||rhpNonrunnerPending(model,{date,course:r.course,time:tm}))){
  if(model==='Z'&&!mr)return s38paragraph(`${n} does not currently fall under an eligible Model Z specialist.`, 'No Z form opinion or betting selection is inferred for this runner.');
  return s38paragraph(`${n} is awaiting a current Model ${model} assessment.`, 'Earlier results or prices will not be substituted for a current prediction.');
 }
 if(model==='B'){
  const row=rcModelHorse(r.horse,r.course,date,tm);if(!row)return s38paragraph(`${n} has no securely linked Model B form on this field.`);
  const facts=s38formSignal(row);
  const rank=row.rank!=null?`ranks ${row.rank} on the available form comparison`:'has insufficient verified form for a reliable position';
  return s38paragraph(`${n} ${rank}.`,facts.length?s38esc(facts.slice(0,2).join(' ')):'There is no securely linked recent form to quantify.',
    facts.length>2?s38esc(facts.slice(2).join(' ')):'',
    'This is a research assessment only; a positive form comment does not automatically create a betting selection.');
 }
 if(model==='C'){
  const row=rcModelHorse(r.horse,r.course,date,tm);const p=row?.win_probability;
  const signal=row?.positive_count,negative=row?.negative_count;
  return s38paragraph(`${n} is given ${p!=null?(100*Number(p)).toFixed(1)+'%':'no verified'} estimated chance of winning by Model C.`,
   signal!=null&&negative!=null?`The historical assessment records ${Number(signal)} positive and ${Number(negative)} negative signals, based on ${s38esc(row.form_runs??'an unconfirmed number of')} earlier linked runs.`:'Historical supporting evidence is incomplete.',
   'This is a model probability, not a guarantee or proof of bookmaker value.');
 }
 if(model==='D'){
  const row=rcModelHorse(r.horse,r.course,date,tm);const signals=Object.entries(row?.category_signals||{}).filter(([k,v])=>Number.isFinite(Number(v?.score))&&Number(v.sample_evidence_count||0)>0).sort((a,b)=>Math.abs(Number(b[1].score))-Math.abs(Number(a[1].score))).slice(0,2);
  return s38paragraph(`${n} is ranked ${row?.rank??'—'} on Model D's independent eight-factor scorecard.`,
   signals.length?`The most influential documented factors here are ${signals.map(([k,v])=>`${s38esc(k)} (${Number(v.score)>=0?'+':''}${Number(v.score).toFixed(2)})`).join(' and ')}.`:'There is not enough factor-level evidence to give a detailed view.',
   'The relative score is not a calibrated win probability; a separate threshold determines research shortlisting.');
 }
 if(model==='Z'){
  const row=rcModelHorse(r.horse,r.course,date,tm);
  if(!row)return s38paragraph(`${n} has no verified specialist research score.`);
  return s38paragraph(`${n} ranks ${row.rank} under the frozen ${s38esc(mr.strategy_name||'course')} strategy.`,
    mr.selection?.horse===r.horse?'The original Stage 34 gap rule is met; this is a paper selection recorded before the off.':'The specialist ranks the horse but has not chosen it as a paper selection.',
    'Model Z is in candidate testing. No real bet is placed and the strategy weights are not updated.');
 }
 if(model==='A'){
  const prob=strategyWinChance(r.horse,r.course,date,tm);
  const cr=commentaryHorse(r.horse,r.course,date,tm);
  const published=String(cr?.summary||'').trim();
  return s38paragraph(`${n} ${prob!=null?'has a Model A estimated win chance of '+(100*prob).toFixed(1)+'%':'is awaiting a complete validated Model A probability for this field'}.`,
   published?s38esc(published):'No independent horse-specific assessment has been published beyond the model form features.',
   'This estimate describes winning chance, not whether the offered odds represent value.');
 }
 const old=s38OldComment(r,x,date);
 return s38paragraph(`${n} ${s38esc(String(old||'No current model assessment.'))}`);
};
const s38OldBadge=rhpSelectedModelBadge;
rhpSelectedModelBadge=function(horse,course,date,time){
 if(!['D','Z'].includes(rcSelectedModel)){
  if(rcSelectedModel==='B'){
   const row=rcModelHorse(horse,course,date,time);
   return `<span class="rhp-win-chance">${row?.rank!=null?'B #'+s38esc(row.rank)+' · provisional':'B evidence pending'}</span>`;
  }
  return s38OldBadge(horse,course,date,time);
 }
 const row=rcModelHorse(horse,course,date,time);
 return `<span class="rhp-win-chance" title="Research rank, not a win percentage">Model ${rcSelectedModel}${row?.rank!=null?' #'+s38esc(row.rank):' · no rank'}</span>`;
};
function s38allRaces(day,type,model){
 const filtered=new Map();
 for(const r of racecardRunners||[]){
  const date=r.date||dateKey(r.race_time);if(date!==day||r.non_runner||r.is_non_runner||String(r.number||'').toUpperCase()==='NR')continue;
  const tm=clock(r.race_time),k=s38key(r.course,date,tm);
  if(!filtered.has(k))filtered.set(k,{course:r.course,date,time:tm,race_time:r.race_time,race_type:r.race_type||r.type||'',count:0});
  filtered.get(k).count++;
 }
 return [...filtered.values()].filter(x=>x.count>=2&&abTypeMatches({race_type:s38type(x)},type)).sort((a,b)=>a.time.localeCompare(b.time)||a.course.localeCompare(b.course));
}
function s38type(x){
 const raw=String(x.race_type||'').toLowerCase(),c=String(x.course||'').toLowerCase();
 if(/hurdle|chase|jump|national hunt|steeple|bumper/.test(raw))return 'jumps';
 if(c.includes('(aw)')||/all.?weather|aw|tapeta|polytrack/.test(raw))return 'aw';
 return 'flat';
}
function s38ranking(model,x){
 const {course,date,time}=x;
 if(model==='Z'){
  const z=s38find(stage38Z,course,date,time);
  if(!z)return {label:'No specialist match',detail:'Eligibility checked; no frozen candidate applies'};
  if(rhpCFieldChanged(z,course,date,time))return {label:'Reassessment pending',detail:'Runner list changed'};
  return {label:z.selection?.horse||z.runners?.[0]?.horse||'No rank',detail:z.strategy_name+' · '+(z.selection_status==='PAPER_ONLY'?'paper qualifier':'no selection')};
 }
 const f={A:strategyProbabilityFeed,B:stage38B||strategyBProbFeed,C:strategyCFeed,D:stage38D}[model];
 const r=s38find(f,course,date,time);
 if(!r)return {label:'Independent forecast pending',detail:'No current complete-field assessment'};
 if(rhpCFieldChanged(r,course,date,time))return {label:'Field changed',detail:'Reassessment required; old rank withheld'};
 const rows=[...(r.runners||[])];
 if(model==='A')rows.sort((a,b)=>Number(b.probability)-Number(a.probability));
 if(model==='C')rows.sort((a,b)=>Number(b.win_probability)-Number(a.win_probability));
 if(model==='B')rows.sort((a,b)=>Number(a.rank??999)-Number(b.rank??999));
 if(model==='D')rows.sort((a,b)=>Number(a.rank??999)-Number(b.rank??999));
 const h=rows[0];if(!h||h.rank==null&&model==='B'&&h.research_score==null&&h.score==null)return {label:'Verified evidence insufficient',detail:'No unsupported winner inferred'};
 const v=model==='A'?h.probability:model==='C'?h.win_probability:null;
 return {label:h.horse||'No supported leader',detail:(v!=null?(100*Number(v)).toFixed(1)+'% experimental win chance':model==='B'?'Relative verified-form ranking; no calibrated chance':'Relative research score; no calibrated chance')};
}
function s38everyRaceHTML(model,target,type){
 const races=s38allRaces(target,type,model);
 return `<details class="s38-overview"><summary>All race assessments · ${races.length} races</summary><div class="s38-overview-items">${races.map(r=>{
   const q=s38ranking(model,r);
   return `<div class="s38-overview-race"><strong>${s38esc(r.time)} ${s38esc(r.course)}</strong><span><b>${s38esc(q.label)}</b><small>${s38esc(q.detail)}</small></span></div>`;
  }).join('')||'<p>No complete race fields are available for this date.</p>'}</div></details>`;
}
const s38OldSelections=renderStrategySelections;
renderStrategySelections=function(){
 const model=abViewState.selections.model;
 if(!['D','Z'].includes(model)){
  s38OldSelections();
  const host=$('strategy-selection-list'),target=strategyDate(strategyDay);
  if(host && host.querySelector('.s38-overview')===null)host.insertAdjacentHTML('beforeend',s38everyRaceHTML(model,target,abViewState.selections.type));
  return;
 }
 const host=$('strategy-selection-list');if(!host)return;
 abSwitchState();
 document.querySelectorAll('[data-strategy-day]').forEach(b=>b.classList.toggle('active',b.dataset.strategyDay===strategyDay));
 const target=strategyDate(strategyDay),feed=model==='D'?stage38D:stage38Z;
 const raceRows=(feed?.day===target?feed.races||[]:[]).filter(r=>abTypeMatches(r,abViewState.selections.type));
 const now=new Date();
 const published=raceRows.filter(r=>r.selection&&['RESEARCH_ONLY','PAPER_ONLY'].includes(r.selection_status)&&!rhpCFieldChanged(r,r.course,r.date,r.time)&&new Date(r.race_time)>now);
 const message=model==='Z'?'Frozen course-candidate research. Paper picks are timestamped before the race; no active or approved Z strategies.':
    'Independent eight-factor rankings for all available races; research shortlist only, never a validated betting edge.';
 host.innerHTML=`<div class="panel"><strong>Model ${model} · ${published.length} research/paper selections</strong><p class="hint">${s38esc(message)} Every eligible race is assessed separately; betting selections are not forced.</p></div>`+
  (published.map(r=>`<article class="horse s38-paper-card"><div class="horse-top"><div><div class="name">${s38esc(r.selection.horse)}</div><div class="meta">${s38esc(r.time)} · ${s38esc(r.course)} · ${s38esc(r.strategy_name||'Model D independent research')}</div></div><span class="pill">${model==='Z'?'Paper only':'Research only'}</span></div><p>${s38esc(r.verdict)}</p><p class="hint">Recorded ${s38esc(r.published_at||feed.generated_at)} · frozen score gap ${Number(r.decision?.gap||0).toFixed(3)}. No real bet placed.</p><div class="actions" style="margin-top:10px"><button type="button" class="action small outline" data-rhp-hp-race="1" data-rhp-hp-day="${s38esc(r.date)}" data-rhp-hp-course="${s38esc(r.course)}" data-rhp-hp-time="${s38esc(r.time)}">View race</button></div></article>`).join('')||'<div class="rhp-ab-empty">No qualifying research or paper selections have been published for this period.</div>')+
 s38everyRaceHTML(model,target,abViewState.selections.type);
};
// Stage68: only the provider's verified final finishing order; top three are
// descriptive and are not bookmaker-specific each-way place terms.
function s38VerifiedFinishers(record){
 const id=courseMatchKey(record.course)+'|'+record.date+'|'+String(record.time||'').slice(0,5);
 const race=finishedRaceIndex.get(id);
 if(!race)return '';
 const all=[...race.values()];
 const podium=all.filter(x=>Number.isInteger(x.position)&&x.position<=3)
                 .sort((a,b)=>a.position-b.position);
 const ord=n=>n===1?'1st':n===2?'2nd':n===3?'3rd':n+'th';
 const raceStatus=x=>({pu:'Pulled up',f:'Fell',ur:'Unseated rider',bd:'Brought down',ro:'Ran out',r:'Refused'}[String(x.status||'').toLowerCase()]||x.status||'Unplaced');
 const resultText=x=>s38esc((x.position?ord(x.position):raceStatus(x))+' · '+x.horse+' · SP '+(x.sp||'unavailable'));
 const selected=race.get(horseMatchKey(record.horse));
 const below=selected&&!(Number.isInteger(selected.position)&&selected.position<=3)
              ?'<p class="hint">Hot Pot selection: '+resultText(selected)+'</p>':'';
 return '<div class="hint" style="margin-top:9px"><strong>Verified finishers (top three)</strong>'+
    (podium.length?podium.map(x=>'<div>'+resultText(x)+'</div>').join(''):'<div>Full finishing order not supplied.</div>')+
    below+'</div>';
}
const s38OldResults=renderStrategyResults;
renderStrategyResults=function(){
 const model=abViewState.results.model;
 if(!['D','Z'].includes(model)){s38OldResults();return;}
 abSwitchState();
 const host=$('strategy-results-list'),sum=$('strategy-results-summary');if(!host||!sum)return;
 const feed=model==='D'?stage38DResults:stage38ZResults;
 const all=(feed?.selections||[]),period=$('strategy-results-period').value,cutoff=new Date();
 cutoff.setDate(cutoff.getDate()-(period==='all'?99999:Number(period)-1));
 const list=all.filter(r=>(period==='all'||r.date>=dateKey(cutoff.toISOString()))&&abTypeMatches(r,abViewState.results.type))
   .sort((a,b)=>String(b.published_at||'').localeCompare(String(a.published_at||'')));
 const settled=list.filter(r=>['Won','Lost'].includes(r.status)),wins=settled.filter(r=>r.status==='Won').length;
 const priced=settled.filter(r=>r.odds>1&&(model==='D'||r.preoff_price_verified===true));
 const stake=Number($('strategy-results-stake').value)||1;
 const profit=priced.reduce((v,r)=>v+stake*(r.status==='Won'?Number(r.odds)-1:-1),0);
 const pct=priced.length?100*profit/(stake*priced.length):null;
 $('strategy-results-sr').textContent=settled.length?(100*wins/settled.length).toFixed(1)+'%':'—';
 $('strategy-results-roi').textContent=pct==null?'—':pct.toFixed(1)+'%';
 $('strategy-results-pnl').textContent=pct==null?'—':(profit<0?'−':'+')+money(Math.abs(profit));
 sum.textContent=`Model ${model} · ${list.length} paper/research logs · ${settled.length} settled · ${wins} winners. `+
  (model==='Z'?'ROI includes only verified pre-off bookmaker prices; no SP substitution.':'D results are settled at official SP where available, NOT attainable pre-off bookmaker quotes.')+
  ' Real My Bets and P&L are unchanged.';
 host.innerHTML=list.length?list.map(r=>`<article class="horse"><div class="horse-top"><div><div class="name">${s38esc(r.horse)}</div><div class="meta">${s38esc(r.date)} · ${s38esc(r.time)} ${s38esc(r.course)} · ${s38esc(r.strategy_name||'Independent Model D')}</div></div><span class="pill">${s38esc(r.status||'Pending')}</span></div><p class="hint">Recorded before off: ${s38esc(r.published_at||'time not recorded')} · ${model==='Z'?'Paper only':'Research only'} · ${r.odds>1?s38esc((model==='Z'?'Pre-off odds':'Official SP')+' '+r.odds):'No verified bookmaker quote'}${r.position?' · Finish '+s38esc(r.position):r.finishing_status?' · '+s38esc(r.finishing_status):''}</p>${model==='D'?s38VerifiedFinishers(r):''}</article>`).join(''):
   '<div class="rhp-ab-empty">No recorded results for this model and period yet. Paper entries appear only after a genuine pre-off prediction.</div>';
 const hist=$('strategy-historical-roi');if(hist)hist.innerHTML=`<p class="hint">${model==='D'?'Model D historical evidence is recorded separately in Stage 30–37 reports.':'Model Z candidates remain frozen. Promotion requires independent forward paper results and explicit approval.'}</p>`;
};
const s38OldLoad=loadStrategyData;
loadStrategyData=async function(){await s38OldLoad();await s38load();};
// Extend refresh detection to independent research feeds without changing existing feed behaviours.
for(const f of ['strategy-b-all-races.json','strategy-d-probabilities.json','strategy-z-probabilities.json','strategy-d-results.json','strategy-z-results.json','model-assessment-coverage.json']){
 if(!AUTO_REFRESH_FILES.includes(f))AUTO_REFRESH_FILES.push(f);
}

// Rebind the existing app's legacy tab aliases to the unified renderers.
renderPower=renderStrategySelections;
renderPowerResults=renderStrategyResults;
loadPowerResults=loadStrategyData;
// Stage68: View Race works even when the selection has no bookmaker odds.
document.addEventListener('click',function(event){
 const button=event.target.closest('[data-rhp-hp-race]');
 if(!button)return;
 event.preventDefault();
 const day=button.dataset.rhpHpDay,course=button.dataset.rhpHpCourse,time=button.dataset.rhpHpTime;
 const popup={day,course,time};
 $('race-info').textContent=course+' · '+time+' · '+day;
 renderRacecards(popup);
 if(!$('race-runner-list').querySelector('.panel'))
    $('race-runner-list').innerHTML='<div class="empty">The complete racecard has not yet been published.</div>';
 $('race-overlay').classList.remove('hidden');
 document.body.style.overflow='hidden';
},true);
// Also load directly for cached app views which skip the usual tab setup.
s38load();
