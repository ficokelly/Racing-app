// RHP_STAGE70_USER_REQUESTS_V1
// Race withdrawals never erase a published ranking. These are research-only
// rankings. Non-runners remain identified as NR until a later complete assessment.
(function(){
 'use strict';
 let view='all'; // all races, or model selections; race-type filters apply to both
 const getRace=(course,day,time)=>s38find(stage38D,course,day,time);
 const ordered=race=>[...(race?.runners||[])].filter(x=>Number.isInteger(Number(x.rank))&&Number(x.rank)>0).sort((a,b)=>Number(a.rank)-Number(b.rank));
 const norm=x=>horseMatchKey(x||'');
 const isNR=x=>!!x&&(x.non_runner===true||x.is_non_runner===true||x.is_withdrawn===true||String(x.number||'').toUpperCase()==='NR'||['nr','nonrunner','withdrawn','scratched','dns'].includes(String(x.runner_status||x.status||'').toLowerCase().replace(/[^a-z]/g,'')));
 function cardState(race,horse){
  if(!race||!horse)return 'unavailable';
  const cards=(racecardRunners||[]).filter(x=>
   courseMatchKey(x.course)===courseMatchKey(race.course)&&
   (x.date||dateKey(x.race_time))===race.date&&clock(x.race_time)===race.time);
  const exact=cards.find(x=>norm(x.horse)===norm(horse));
  if(!exact)return 'unknown'; // absent is NOT proof of a withdrawal
  return isNR(exact)?'nr':'active';
 }
 function raceGroup(r){
  const kind=String(r.race_type||'').toLowerCase(),title=String(r.race_name||'').toLowerCase(),place=String(r.course||'').toLowerCase();
  const info=[kind,title,place].join(' ');
  if(place.includes('(aw)')||/all.?weather|polytrack|tapeta/.test(info))return 'aw';
  if(/hurdle|chase|steeple|national hunt|nh flat|bumper|jumps/.test(info))return 'jumps';
  if(/flat|maiden stakes|novice stakes|nursery|handicap stakes/.test(info)||kind==='flat')return 'flat';
  return 'unknown'; // don't quietly call an unknown race Flat
 }
 const typeLabel={flat:'Flat turf',aw:'All-weather',jumps:'Jumps',unknown:'Race type not confirmed'};
 const kindText=race=>typeLabel[raceGroup(race)]||typeLabel.unknown;
 function modelDecision(race){
  if(!race)return 'This race is on the card, but Hot Pots has not published its rankings yet.';
  const d=race.decision||{};
  if(race.selection_status==='RESEARCH_ONLY')return 'The top-rated horse also passed the extra checks for a model selection. This is a research pick, not a guaranteed winner.';
  if(d.frozen_d_agreement===false)return 'The two parts of the model disagree on the strongest horse. Hot Pots ranks the field but makes no selection.';
  if(Number.isFinite(Number(d.original_d_gap))&&Number.isFinite(Number(d.minimum_gap))&&Number(d.original_d_gap)<Number(d.minimum_gap))return 'The historical ratings are too close for a formal selection. That does not erase the rankings.';
  return 'Hot Pots can rank the field without making a model selection. Its extra qualifying conditions were not all met.';
 }
 function priorRankingNote(race){
  if(!race)return '';
  const changed=rhpCFieldChanged(race,race.course,race.date,race.time);
  return changed?'<p class="rhp70-changed"><strong>Runner list changed:</strong> these are the last published rankings. A withdrawal does not remove them. The next completed assessment can update the ranks and the selection.</p>':'';
 }
 function pairHTML(race){
  const ranks=ordered(race),top=ranks.slice(0,2);
  if(!top.length)return '<p class="hint">Waiting for a published rank #1 and #2. No ranking is being guessed from odds.</p>';
  const rows=top.map(x=>{
   const status=cardState(race,x.horse),nr=status==='nr';
   const selected=race.selection_status==='RESEARCH_ONLY'&&norm(race.selection?.horse)===norm(x.horse);
   return `<div class="rhp70-pair-row${nr?' is-nr':''}"><span class="rhp70-rank">#${safe(x.rank)}</span><div><strong>${safe(x.horse)}</strong>${nr?' <span class="rhp70-nr">NR · withdrawn</span>':''}${selected?' <span class="rhp70-pick">Model selection</span>':''}<small>${safe(x.rank===1?'Highest in this published ranking':'Second in this published ranking')}${nr?' · pending reassessment':''}</small></div></div>`;
  });
  const withdrawnInTwo=top.some(x=>cardState(race,x.horse)==='nr');
  if(withdrawnInTwo){
   const remaining=ranks.filter(x=>cardState(race,x.horse)!=='nr'&&!top.includes(x));
   if(remaining.length)rows.push(`<div class="rhp70-next"><strong>Next active horse:</strong> #${safe(remaining[0].rank)} ${safe(remaining[0].horse)} (previous published rank; not renumbered until reassessment)</div>`);
  }
  return '<div class="rhp70-pair">'+rows.join('')+'</div>';
 }
 function openRaceButton(race){
  return `<button type="button" class="action small outline" data-rhp-open-race="${encodeURIComponent(JSON.stringify({course:race.course,day:race.date,time:race.time}))}">View Race</button>`;
 }
 function display(race){
  const selected=race?.selection_status==='RESEARCH_ONLY'&&!!race.selection;
  const raceTitle=String(race.race_name||'').trim()||kindText(race)+' race';
  return `<article class="panel rhp70-race"><div class="rhp70-head"><div><h3>${safe(race.time)} · ${safe(race.course)}</h3><p class="hint">${safe(raceTitle)} · ${safe(kindText(race))}</p></div><span class="rhp70-pill">${selected?'Model selection':'Ranked only'}</span></div>`+
   pairHTML(race)+priorRankingNote(race)+`<p class="rhp70-explain">${safe(modelDecision(race))}</p>`+openRaceButton(race)+'</article>';
 }
 function ensureControls(){
  const panel=$('strategy-day-switch')?.closest('.panel');
  if(panel&&!$('rhp70-view-selector')){
   const container=document.createElement('div');container.id='rhp70-view-selector';container.className='rhp70-view-selector';container.setAttribute('role','group');container.setAttribute('aria-label','Hot Pots view');
   container.innerHTML='<button type="button" data-rhp70-view="all">All races · #1 & #2</button><button type="button" data-rhp70-view="selections">Model selections</button>';
   panel.insertBefore(container,$('strategy-day-switch'));
  }
  const old=$('power-type-toggle')?.closest('.panel');if(old)old.hidden=true;
  document.querySelectorAll('.form-price-nav,.stage41-link').forEach(x=>x.remove());
  const intro=$('powerview')?.querySelector('.rhp-strategy-panel>p.hint');
  if(intro)intro.textContent='All races shows the top two D(c) rankings for each race. Model selections shows only races that passed the additional research checks. The race-type filters work in both views. Withdrawals never blank the previous rankings.';
  const flat=$('powerview')?.querySelector('[data-ab-type="flat"][data-ab-view="selections"]');if(flat)flat.textContent='Flat turf';
  document.querySelectorAll('#rhp70-view-selector [data-rhp70-view]').forEach(b=>{const active=b.dataset.rhp70View===view;b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active));});
 }
 // Don't erase rankings if the current declared field changes. The score engine
 // itself will still recompute and replace them on its next eligible run.
 renderStrategySelections=function(){
  abViewState.selections.model='D';ensureControls();
  const host=$('strategy-selection-list');if(!host)return;
  document.querySelectorAll('[data-strategy-day]').forEach(b=>b.classList.toggle('active',b.dataset.strategyDay===strategyDay));
  document.querySelectorAll('#powerview [data-ab-type][data-ab-view="selections"]').forEach(b=>b.classList.toggle('active',b.dataset.abType===abViewState.selections.type));
  const day=strategyDate(strategyDay),feed=stage38D;
  if(!feed||!Array.isArray(feed.races)){
   host.innerHTML='<div class="panel">Waiting for the first D(c) ranking feed. Older models are not substituted.</div>';return;
  }
  // Include newly added races even if the engine has not yet scored them.
  const scored=feed.races.filter(x=>x.date===day);
  const known=new Map(scored.map(r=>[commentaryKey(r.course,day,r.time),r]));
  for(const c of racecardRunners||[]){
   if((c.date||dateKey(c.race_time))!==day||!c.course||!c.race_time)continue;
   const time=clock(c.race_time),key=commentaryKey(c.course,day,time);
   if(!known.has(key))known.set(key,{course:c.course,date:day,time,race_time:c.race_time,race_name:c.race_name||'',race_type:c.race_type||c.type||'',runners:[],selection:null,selection_status:'ASSESSMENT_PENDING'});
  }
  const all=[...known.values()],type=abViewState.selections.type||'all';
  const shown=all.filter(r=>(type==='all'||raceGroup(r)===type)&&(view==='all'||r.selection_status==='RESEARCH_ONLY'&&!!r.selection))
   .sort((a,b)=>String(a.race_time||a.time).localeCompare(String(b.race_time||b.time))||String(a.course).localeCompare(String(b.course)));
  const picks=all.filter(r=>r.selection_status==='RESEARCH_ONLY').length,missing=all.filter(r=>r.selection_status==='ASSESSMENT_PENDING').length;
  const msg=$('strategy-model-message');
  if(msg)msg.textContent=`${day} · ${scored.length} races ranked · ${picks} research selections · ${missing?missing+' race(s) awaiting ranking · ':''}${shown.length} shown.`;
  host.innerHTML=shown.length?shown.map(display).join(''):`<div class="panel">${all.length?'No races match this view.':'No racing data for this date yet.'} Choose another filter or date.</div>`;
 };
 renderPower=renderStrategySelections;
 document.addEventListener('click',e=>{
  const btn=e.target.closest('[data-rhp70-view]');if(!btn||!['all','selections'].includes(btn.dataset.rhp70View))return;
  e.preventDefault();e.stopImmediatePropagation();view=btn.dataset.rhp70View;renderStrategySelections();
 },true);
 // On the bottom dock, Racecards always returns to the meetings/races menu.
 const dock=$('rhp-bottom-nav');
 dock?.addEventListener('click',e=>{
  const btn=e.target.closest('[data-dock-tab="racecards"]');if(!btn)return;
  e.preventDefault();e.stopImmediatePropagation();
  rhpShowMenu(false);rhpRaceDetailActive=false;rhpRaceAllMode=false;
  if($('rc-meeting'))$('rc-meeting').value='all';
  rcSetupFilters();if($('rc-time'))$('rc-time').value='all';
  showTab('racecards');
  window.scrollTo({top:0,behavior:'auto'});
 },true);
 // Explain relative rankings without pretending the scores are calibrated win odds.
 rhpSelectedModelVerdict=function(sample,day){
  const time=clock(sample.race_time),race=getRace(sample.course,day,time);
  if(!race)return '<div class="rhp-verdict"><strong>How Hot Pots sees this race</strong><p>There is no published assessment for this field yet. The app will not guess a ranking.</p></div>';
  const rows=ordered(race),first=rows[0],second=rows[1];
  if(!first)return '<div class="rhp-verdict"><strong>How Hot Pots sees this race</strong><p>The field is awaiting its first published ranking.</p></div>';
  const gap=Number(race.decision?.gap),close=Number.isFinite(gap)&&gap<.5;
  return `<div class="rhp-verdict"><strong>How Hot Pots sees this race</strong>${s38paragraph(
   `<strong>${safe(first.horse)}</strong> comes out highest in the model’s assessment${second?`, ahead of <strong>${safe(second.horse)}</strong>`:''}.`,
   'Hot Pots weighs established historical racing patterns alongside evidence relevant to the type of race being run. It compares the horses within this field rather than simply choosing the shortest-priced favourite.',
   second?(close?'There is not much between the two leaders on the combined research score.':'There is a clearer difference between the leading two on the combined research score. This does not guarantee either will win.'):'',
   modelDecision(race),
   rhpCFieldChanged(race,race.course,race.date,race.time)?'A runner has been added or removed since this ranking was produced. The published order remains on screen until the model reassesses it.':''
  )}</div>`;
 };
 rhpSelectedHorseComment=function(r,x,day){
  const race=getRace(r.course,day,clock(r.race_time)),runner=ordered(race).find(z=>norm(z.horse)===norm(r.horse));
  if(!race)return `There is no D(c) ranking for ${safe(r.horse)} in this race yet. No earlier model or bookmaker odds have been used to invent one.`;
  if(!runner)return `${safe(r.horse)} is not in the last published Hot Pots ranking for this race. If the declared runners have changed, a fresh assessment is needed.`;
  const rank=Number(runner.rank),order=ordered(race),leader=order[0],second=order[1];
  const intro=rank===1?`Hot Pots has ${safe(r.horse)} at the top of its list${second?`, ahead of ${safe(second.horse)}`:''}.`:
   `${safe(r.horse)} is ranked #${safe(rank)} out of ${order.length} horses. ${rank===2&&leader?`${safe(leader.horse)} is the only runner rated higher.`:`The model currently favours ${safe(leader?.horse||'another runner')} over this horse.`}`;
  const selection=rank===1&&race.selection_status==='RESEARCH_ONLY'?'It also passes the extra checks for a Hot Pots research selection.':rank===1?'It leads the rankings but does not meet every extra check for a formal model selection.':'Rank position shows the model’s ordering, not where a horse is predicted to finish.';
  const stale=rhpCFieldChanged(race,race.course,race.date,race.time)?'The field has changed since this assessment, so the displayed rank is from the last published calculation.':'';
  return s38paragraph(intro,'The ranking combines historical race evidence with how the available form fits this kind of race. Those signals are weighed against the other runners, not treated as bookmaker probabilities.',selection,stale);
 };
 rhpSelectedModelBadge=function(horse,course,day,time){
  const race=getRace(course,day,time);
  const row=ordered(race).find(x=>norm(x.horse)===norm(horse));
  const nr=row&&cardState(race,horse)==='nr';
  return `<span class="rhp-rank-chip" title="${nr?'Last published rank; runner withdrawn':'Hot Pots D(c) research ranking'}">${row?'#'+safe(row.rank)+(nr?' · NR':''):'—'}</span>`;
 };
 ensureControls();
})();
