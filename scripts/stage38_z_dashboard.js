// Automatic frozen Model Z selections are published separately from manual observations.
(async function(){
 const e=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const host=document.getElementById('s38-automatic-z');if(!host)return;
 try{
  const [p,r]=await Promise.all(['./data/strategy-z-probabilities.json','./data/strategy-z-results.json'].map(u=>fetch(u+'?v='+Date.now(),{cache:'no-store'}).then(x=>x.ok?x.json():null)));
  const all=p?.races||[],latest=all.filter(x=>x.selection_status==='PAPER_ONLY');
  const logs=r?.selections||[],settled=logs.filter(x=>['Won','Lost'].includes(x.status)),wins=settled.filter(x=>x.status==='Won').length;
  const priced=settled.filter(x=>x.preoff_price_verified===true&&Number(x.odds)>1);
  const profit=priced.reduce((s,x)=>s+(x.status==='Won'?Number(x.odds)-1:-1),0),roi=priced.length?100*profit/priced.length:null;
  host.innerHTML=`<div class="row"><span class="tag research">${e(p?.mode||'Paper feed pending')}</span><span class="small">${e(p?.day||'Date unavailable')}</span></div>`+
   `<div class="cards"><div class="metric"><small>Latest automatic qualifiers</small><b>${latest.length}</b></div><div class="metric"><small>Verified settled strike rate</small><b>${settled.length?(100*wins/settled.length).toFixed(1)+'%':'—'}</b></div><div class="metric"><small>Verified pre-off bookmaker ROI</small><b>${roi!=null?roi.toFixed(1)+'%':'—'}</b></div></div>`+
   `<div class="note">Candidate research only. Automated paper entries are frozen before the off and are separate from manual observations. ROI is not calculated without a verified timestamped bookmaker price.</div>`+
   (latest.length?`<div class="table-wrap"><table><thead><tr><th>Race</th><th>Horse</th><th>Strategy</th><th>Paper status</th></tr></thead><tbody>${latest.map(x=>`<tr><td>${e(x.time)} ${e(x.course)}</td><td><strong>${e(x.selection?.horse)}</strong></td><td>${e(x.strategy_name)}</td><td>Paper only</td></tr>`).join('')}</tbody></table></div>`:'<div class="empty">No specialist paper qualifiers in the latest published racecards. Every race is still assessed for eligibility.</div>');
 }catch(err){host.innerHTML='<div class="empty">The automatic Model Z paper feed is not published yet. This is not a losing selection.</div>';}
})();
