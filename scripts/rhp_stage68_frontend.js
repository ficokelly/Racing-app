// RHP_STAGE68_D_C_ONLY — authoritative D(c) display and verified result detail.
// Research A/B/C/D/Z continue independently in data archives, not on app tabs.
(function(){
 function statusName(value){
  const x=String(value||'').toLowerCase().replace(/[^a-z]/g,'');
  if(x==='fell'||x==='f')return 'Fell';
  if(x==='pu'||x==='pulledup')return 'Pulled Up';
  if(x==='ur'||x==='unseatedrider')return 'Unseated Rider';
  if(x==='bd'||x==='broughtdown')return 'Brought Down';
  if(['nr','nonrunner','withdrawn','scratched'].includes(x))return 'Non-runner';
  return value||'Pending';
 }
 function spLabel(value){
  const v=String(value??'').trim();
  return v&&v!=='null'&&v!=='None'?'SP '+safe(v):'SP unavailable';
 }
 function ordinal(n){
  n=Number(n);if(!Number.isInteger(n)||n<1)return '—';
  return n+(n%100>=11&&n%100<=13?'th':n%10===1?'st':n%10===2?'nd':n%10===3?'rd':'th');
 }
 function spDecimal(v){
  const s=String(v??'').trim();
  if(/^evens?$/i.test(s))return 2;
  const m=s.match(/^(\d+)\s*\/\s*(\d+)$/);
  if(m&&Number(m[2])>0)return 1+Number(m[1])/Number(m[2]);
  return null;
 }
 function raceResultRows(entry){
  if(Array.isArray(entry.finishers)&&entry.finishers.length)return entry.finishers;
  const index=finishedRaceIndex.get(finishedRaceKey(entry.course,entry.race_time,entry.date));
  if(index)return [...index.values()].map(x=>({horse:x.horse,position:x.position,sp:x.sp,status:x.status}));
  return [];
 }
 function resultCard(entry){
  const runners=raceResultRows(entry);
  const top=runners.filter(x=>Number.isInteger(Number(x.position))&&Number(x.position)>0&&Number(x.position)<=3)
    .sort((a,b)=>Number(a.position)-Number(b.position));
  const selection=runners.find(x=>horseMatchKey(x.horse)===horseMatchKey(entry.horse));
  const picked=selection||{horse:entry.horse,position:entry.position,status:entry.status,sp:entry.sp};
  const status=statusName(entry.status);
  const header=`<div class="horse-top"><div><div class="name">${safe(entry.horse)}</div><div class="meta">${safe(entry.date)} · ${safe(entry.time)} · ${safe(entry.course)}</div></div><span class="pill ${status==='Won'?'yes':status==='Pending'?'':'no'}">${safe(status)}</span></div>`;
  const finisher=(x,selected=false)=>`<div class="rhp-result-runner${selected?' is-picked':''}"><span>${Number(x.position)>0?safe(ordinal(x.position))+' · ':''}${safe(x.horse)}${selected?' ★ Hot Pot':''}${!x.position?' · '+safe(statusName(x.status)):''}</span><span>${spLabel(x.sp)}</span></div>`;
  const seen=new Set();
  const placed=top.map(x=>{seen.add(horseMatchKey(x.horse));return finisher(x,horseMatchKey(x.horse)===horseMatchKey(entry.horse))}).join('');
  const pickedOutside=!seen.has(horseMatchKey(entry.horse))&&entry.status!=='Pending';
  const additional=pickedOutside?finisher(picked,true):'';
  const reason=entry.status==='Pending'?'<p class="hint">Awaiting verified final results. No finishing position or SP has been guessed.</p>':'';
  const note=runners.length?'<p class="hint">Verified first three finishers, followed by the Hot Pot when outside the first three. Second/third does not always qualify for an each-way payout.</p>':'';
  const off={course:entry.course,day:entry.date,time:entry.time};
  return `<article class="horse">${header}<div class="rhp-result-finishers">${placed}${additional}</div>${note}${reason}<div class="hint">Research pick: ${safe(status)}${entry.position?' · '+safe(ordinal(entry.position)):''} · ${spLabel(entry.sp)} · ${safe(entry.result_source||'Awaiting official results')}</div><div class="actions"><button class="action small outline" type="button" data-rhp-open-race="${encodeURIComponent(JSON.stringify(off))}">View Race</button></div></article>`;
 }
 function renderDResults(){
  const host=$('strategy-results-list'),summary=$('strategy-results-summary');
  if(!host||!summary)return;
  const all=(stage38DResults?.selections||[]);
  const period=$('strategy-results-period')?.value||'all';
  const cutoff=new Date();cutoff.setDate(cutoff.getDate()-(period==='all'?99999:Number(period)-1));
  const shown=all.filter(r=>(period==='all'||r.date>=dateKey(cutoff.toISOString()))&&abTypeMatches(r,abViewState.results.type))
     .sort((a,b)=>String(b.date).localeCompare(String(a.date))||String(b.time).localeCompare(String(a.time)));
  const settled=shown.filter(r=>['Won','Placed','Unplaced','Fell','Pulled Up','Unseated Rider','Brought Down','Refused'].includes(r.status));
  const wins=settled.filter(r=>r.status==='Won').length;
  const priceable=settled.filter(r=>spDecimal(r.sp)!==null);
  const profit=priceable.reduce((x,r)=>x+(r.status==='Won'?spDecimal(r.sp)-1:-1),0);
  $('strategy-results-sr').textContent=settled.length?(100*wins/settled.length).toFixed(1)+'%':'—';
  $('strategy-results-roi').textContent=priceable.length?(100*profit/priceable.length).toFixed(1)+'%':'—';
  $('strategy-results-pnl').textContent=priceable.length?(profit<0?'−':'+')+money(Math.abs(profit)):'—';
  summary.textContent=`Hot Pots D(c) · ${shown.length} timestamped research selections · ${settled.length} settled · ${wins} winners · ${shown.filter(r=>r.status==='Pending').length} awaiting verified results. SP-based ROI uses only selections with confirmed starting prices; no real bets are counted.`;
  host.innerHTML=shown.length?shown.map(resultCard).join(''):'<div class="rhp-ab-empty">No D(c) research selections recorded yet for this period. Rankings are never inferred from the older D feed.</div>';
  const history=$('strategy-historical-roi');if(history)history.textContent='D(c) is a separate research model. Its live win strike rate and SP ROI are not proof of future profitability.';
 }
 renderStrategyResults=function(){abViewState.results.model='D';renderDResults();};
 renderPowerResults=renderStrategyResults;
 document.addEventListener('click',event=>{
  const btn=event.target.closest('[data-rhp-open-race]');if(!btn)return;
  event.preventDefault();event.stopPropagation();
  let target;try{target=JSON.parse(decodeURIComponent(btn.dataset.rhpOpenRace))}catch(e){return}
  if(!target?.course||!target?.time||!target?.day)return;
  const card=racecardRunners.find(r=>r.course===target.course&&(r.date||dateKey(r.race_time))===target.day&&clock(r.race_time)===target.time);
  $('race-info').textContent=`${target.course} · ${target.day} · ${target.time} · full racecard`;
  renderRacecards(target);
  if(!$('race-runner-list').querySelector('.panel'))$('race-runner-list').innerHTML='<div class="empty">Racecard details are no longer available in the rolling archive.</div>';
  $('race-overlay').classList.remove('hidden');document.body.style.overflow='hidden';
 },true);
})();
