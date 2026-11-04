#!/usr/bin/env python3
"""Stage41 form-versus-price overlay audit, prospective paper observations only.

Independent Model D ranks the field BEFORE price. Bookmaker ranks are determined
from one complete, same-bookmaker pre-off book (Paddy Power preferred).
Ranking disagreements are NOT calibrated win probabilities or predicted ROI.
Records top form leader for every assessable field, and one experimental overlay
where a plausible top-half form horse is at least 2 bookmaker ranks overlooked.
Records are immutable and only settled against completed official results.
Never alters Model A/B/C/D/Z, bet ledger, P&L or Stage40 price thresholds.
"""
from __future__ import annotations
import argparse, collections as C, datetime as dt, html, json, math, re, sys
from pathlib import Path
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parent))
import stage40_price_value as base
UTC=dt.timezone.utc
IRE=ZoneInfo('Europe/Dublin')
VERSION='stage41-independent-form-price-rank-overlays-v1'
FORM_TYPES=('D_CURRENT','D_STAGE39_FROZEN_FALLBACK')
MIN_TO_OFF_MIN=2
MAX_BOOK_AGE_MIN=120
MAX_SNAPSHOT_AGE_MIN=60
TOP_FORM_MAX=5
MAX_ODDS=21.0
MIN_RANK_ADVANTAGE=2


def read(p,default=None):return base.read(p,default)
def write(p,obj):base.write(p,obj)
def norm(s):return base.normal(s)
def key(r):return base.race_key(r)
def time(s):return base.parse_time(s)
def now_utc():return dt.datetime.now(UTC)


def quote_source(field,quotes,now,snapshot_status):
    """Pick one *complete* bookmaker book; do not mix bookmakers for rank."""
    if snapshot_status!='FRESH':return None,'PRICE_SNAPSHOT_NOT_FRESH'
    books=base.market_info(field,quotes)
    preference=['Paddy Power','William Hill','Bet365','Sky Bet','BoyleSports','Ladbrokes','Coral','Unibet','Betfair']
    preferred=sorted(books,key=lambda b:(preference.index(b) if b in preference else 99,b))
    for bookmaker in preferred:
        entries={}
        valid=True
        for r in field:
            v=quotes.get(norm(r['horse']),{}).get(bookmaker)
            if not v:valid=False;break
            ts=time(v.get('quote_observed_at'))
            if not ts or ts>now+dt.timedelta(minutes=2) or (now-ts).total_seconds()/60>MAX_BOOK_AGE_MIN:
                valid=False;break
            entries[norm(r['horse'])]=v
        if valid:return {'bookmaker':bookmaker,'quotes':entries,'overround':books[bookmaker]['overround']},'COMPLETE_SAME_BOOKMAKER_BOOK'
    return None,'NO_COMPLETE_CURRENT_SINGLE_BOOKMAKER_BOOK'


def model_D_ranking(field,model_race,model_feed,day,off):
    if not model_race or model_feed.get('day')!=day:return None,'NO_SAME_DAY_D_RANKING'
    ts=time(model_race.get('published_at') or model_feed.get('generated_at'))
    if not ts or ts>=off:return None,'D_RANKING_LATE_OR_UNDATED'
    if model_race.get('complete_field') is False:return None,'D_INCOMPLETE_FIELD'
    arr=model_race.get('runners') or []
    ids=[norm(v.get('horse')) for v in arr]
    live=[norm(v['horse']) for v in field]
    if len(ids)!=len(live) or len(set(ids))!=len(ids) or set(ids)!=set(live):return None,'D_FIELD_CHANGED_REASSESSMENT_PENDING'
    if any(not isinstance(v.get('rank'),int) or v['rank']<1 for v in arr):return None,'D_RANKING_MISSING'
    ranks=sorted(arr,key=lambda v:v['rank'])
    if sorted(v['rank'] for v in ranks)!=list(range(1,len(ranks)+1)):return None,'D_RANKING_NOT_PERMUTATION'
    return [{'horse':r['horse'],'rank':r['rank'],'research_score':r.get('research_score'),
             'horse_id':r.get('horse_id'),'form':None,'trainer':None} for r in ranks],'D_CURRENT'


def stage39_ranking(field,stage39,day,course,clock,off):
    if stage39.get('day')!=day:return None,'STAGE39_WRONG_DATE'
    ts=time(stage39.get('created_at_utc'))
    if not ts or ts>=off:return None,'STAGE39_NOT_PREOFF'
    matched=[r for r in stage39.get('races',[]) if r.get('date')==day and
             base.course(r.get('course'))==course and base.clock(r.get('race_time'))==clock]
    if len(matched)!=1:return None,'STAGE39_NO_UNIQUE_RACE'
    race=matched[0]
    if race.get('status')!='FULL_FIELD_D_RESEARCH_RANKED':return None,'STAGE39_UNVERIFIED_RANKING'
    ranks=race.get('rankings') or []
    present=[norm(v['horse']) for v in field]
    past=[norm(v.get('horse')) for v in ranks]
    if len(past)!=len(present) or len(set(past))!=len(past) or set(past)!=set(present):return None,'STAGE39_FIELD_CHANGED'
    if sorted(v.get('rank') for v in ranks)!=list(range(1,len(ranks)+1)):return None,'STAGE39_RANKS_INVALID'
    return [{'horse':v['horse'],'rank':v['rank'],'research_score':v.get('research_score'),
             'horse_id':next((r.get('horse_id') for r in field if norm(r['horse'])==norm(v['horse'])),None),
             'form':v.get('recent_form'),'trainer':v.get('trainer')} for v in ranks],'D_STAGE39_FROZEN_FALLBACK'


def market_ranks(ranked,book):
    """Competition rankings with price ties share a rank."""
    prices={norm(r['horse']):book['quotes'][norm(r['horse'])]['decimal'] for r in ranked}
    sorted_unique=sorted(set(prices.values()))
    ranks={p:1+sum(v< p for v in prices.values()) for p in sorted_unique}
    return {h:ranks[p] for h,p in prices.items()}


def shortlist(ranked,book):
    """Always show best comparison; only label actual rank overlay if gap>=2.

    Longshots cannot dominate solely by huge market-rank difference: only the
    top five or top half by independent form are eligible for experimental picks.
    """
    mr=market_ranks(ranked,book)
    n=len(ranked)
    sample=[]
    for r in ranked:
        h=norm(r['horse']);q=book['quotes'][h]
        diff=mr[h]-r['rank']
        sample.append({**r,'market_rank':mr[h],'price_decimal':q['decimal'],
            'bookmaker':book['bookmaker'],'quote_observed_at':q.get('quote_observed_at'),
            'quote_timestamp_quality':q.get('quote_timestamp_quality'),
            'opening_decimal':q.get('opening_decimal'),
            'market_share_pct':round(100/(q['decimal']*book['overround']),2),
            'break_even_pct':round(100/q['decimal'],2),
            'rank_advantage':diff,
            'research_only':True,'probability_calibrated':False})
    topcut=max(2,min(TOP_FORM_MAX,math.ceil(n/2)))
    eligible=[r for r in sample if r['rank']<=topcut and 1.8<=r['price_decimal']<=MAX_ODDS]
    ordered=sorted(eligible,key=lambda r:(-r['rank_advantage'],r['rank'],-r['price_decimal'],norm(r['horse'])))
    best=ordered[0] if ordered else None
    overlay=best if best and best['rank_advantage']>=MIN_RANK_ADVANTAGE else None
    best_any=max(sample,key=lambda r:(r['rank_advantage'],-r['rank']))
    return sample,overlay,best or best_any


def settle(records,results,now):
    return base.settle(records,results,now)


def evaluate(rows):
    priced=[r for r in rows if r.get('status') in ('Won','Lost') and r.get('decimal_odds')]
    profit=sum(float(r.get('profit_units') or 0) for r in priced)
    wins=sum(r['status']=='Won' for r in priced)
    return {'priced_settled':len(priced),'wins':wins,
            'strike_rate_pct':round(100*wins/len(priced),2) if priced else None,
            'profit_units':round(profit,3),
            'roi_pct':round(100*profit/len(priced),2) if priced else None}


def build(root,date=None,now=None):
    root=Path(root);data=root/'data';now=now or now_utc()
    date=date or now.astimezone(IRE).date().isoformat();dt.date.fromisoformat(date)
    cards=read(data/'racecards.json',{});odds=read(data/'latest-odds.json',{})
    d=read(data/'strategy-d-probabilities.json',{})
    historical=read(root/'reports/stage39/stage39-tomorrow-full-assessments.json',{})
    results=read(data/'race-results.json',{})
    oddsidx,price_status,price_audit=base.odds_index(odds,now,date)
    fields=C.defaultdict(list)
    for r in cards.get('runners',[]):
        if base.day(r.get('date') or r.get('race_time'))==date and r.get('course') and r.get('race_time') and r.get('horse'):
            fields[key(r)].append(r)
    d_idx={key(r):r for r in d.get('races',[]) if r.get('date')==date}
    history_path=data/'form-price-paper-history.json'
    history=read(history_path,{'version':1,'mode':'FORM_PRICE_PAPER_DIAGNOSTIC','selections':[]})
    recorded=history.setdefault('selections',[])
    locked={(v.get('date'),base.course(v.get('course')),v.get('time'),v.get('track_type')):v
            for v in recorded if v.get('status') in ('Pending','Won','Lost','Non-runner')}
    races=[];stats=C.Counter();added=0
    for k,rs in sorted(fields.items(),key=lambda kv:(kv[0][2],kv[0][1])):
        active=[r for r in rs if not base.is_nr(r)]
        if len(active)<2 or len({norm(x['horse']) for x in active})!=len(active):stats['invalid_fields']+=1;continue
        off=time(active[0]['race_time']);
        if not off:stats['missing_off_time']+=1;continue
        preoff=off>now+dt.timedelta(minutes=MIN_TO_OFF_MIN)
        # Immediately invalidate earlier paper snapshots if the declared field
        # changed before the off. An unchanged horse must not make a stale race
        # appear unchanged after a separate non-runner is declared.
        current_fingerprint=','.join(sorted(norm(v['horse']) for v in active))
        if preoff:
            for old in recorded:
                if old.get('date')==date and base.course(old.get('course'))==k[1] and \
                   old.get('time')==k[2] and old.get('status')=='Pending' and \
                   old.get('frozen_field_fingerprint')!=current_fingerprint:
                    old['status']='Superseded';old['superseded_at']=now.isoformat()
                    locked.pop((date,k[1],k[2],old.get('track_type')),None)
        item={'date':date,'course':active[0]['course'],'time':k[2],
              'race_time':active[0]['race_time'],'race_id':active[0].get('race_id'),
              'race_name':active[0].get('race_name'),'field_size':len(active),
              'current_field_fingerprint':current_fingerprint,
              'nonrunners_excluded':len(rs)-len(active),'preoff_now':preoff,
              'status':'PENDING','form_leader':None,'market_favourite':None,
              'best_form_price':None,'overlay':None,'form_runner_comparisons':[],
              'matched_bookmaker':None,'paper_tracking':'NOT_RECORDED'}
        ranks,basis=model_D_ranking(active,d_idx.get(k),d,date,off)
        if ranks is None:
            ranks,basis2=stage39_ranking(active,historical,date,k[1],k[2],off)
            if ranks is not None:basis=basis2
        if ranks is None:
            item.update(status='NO_VERIFIED_COMPLETE_FORM_RANKING',form_status=basis)
            stats['without_verified_form']+=1;races.append(item);continue
        item['form_status']=basis
        item['form_leader']=ranks[0]['horse']
        raw=oddsidx.get(k,{}) if price_status=='FRESH' else {}
        quotes=C.defaultdict(dict)
        field_names={norm(r['horse']) for r in active}
        for (h,b),q in raw.items():
            if h in field_names:quotes[h][b]=q
        book,reason=quote_source(active,quotes,now,price_status)
        if book is None:
            item.update(status=reason,model_top_three=[r['horse'] for r in ranks[:3]])
            stats['without_complete_current_book']+=1;races.append(item);continue
        scores,overlay,best=shortlist(ranks,book)
        favourite=min(scores,key=lambda x:(x['price_decimal'],x['rank']))
        item.update(status='FORM_PRICE_COMPARED' if preoff else 'POST_OFF_RESEARCH_ONLY',
                    matched_bookmaker=book['bookmaker'],
                    overround_pct=round(100*(book['overround']-1),2),
                    form_runner_comparisons=scores,
                    market_favourite=favourite['horse'],
                    best_form_price=best,
                    overlay=overlay,
                    potential_overlay=overlay is not None,
                    value_is_proven=False,
                    score_is_win_probability=False,
                    commentary=(f"{best['horse']} is rated {best['rank']} on the independent form model, "
                       f"but is only {best['market_rank']} in the {book['bookmaker']} betting order at "
                       f"{best['price_decimal']:.2f} decimal. "
                       +(f"The {best['rank_advantage']}-place gap is worth monitoring in paper testing. " if overlay and best is overlay else
                         'This comparison does not meet the minimum two-place ranking difference. ')
                       +"It is not a calibrated expected return or a verified profitable bet."))
        stats['with_full_market_and_form']+=1
        if overlay:stats['positive_overlays']+=1
        if not preoff:stats['post_off_comparison_only']+=1
        # Freeze ONE pre-off diagnostic per eligible race even if no significant
        # overlay; outcomes let us compare the overlays with the baseline.
        chosen=overlay or best
        track_type='RANK_OVERLAY' if overlay else 'ALL_RACE_FORM_PRICE_BENCHMARK'
        record_key=(date,k[1],k[2],track_type)
        if preoff and chosen and record_key not in locked:
            recorded.append({'date':date,'course':active[0]['course'],'time':k[2],
                 'race_time':active[0]['race_time'],'race_id':active[0].get('race_id'),
                 'horse':chosen['horse'],'horse_id':chosen.get('horse_id'),
                 'track_type':track_type,'model':'D','model_rank_source':basis,
                 'model_version':d.get('model_version') if basis=='D_CURRENT' else VERSION+'-stage39',
                 'form_rank':chosen['rank'],'bookmaker_rank':chosen['market_rank'],
                 'rank_advantage':chosen['rank_advantage'],
                 'decimal_odds':chosen['price_decimal'],'bookmaker':chosen['bookmaker'],
                 'quote_observed_at':chosen['quote_observed_at'],
                 'quote_timestamp_quality':chosen['quote_timestamp_quality'],
                 'published_at':now.isoformat(),
                 'frozen_field_fingerprint':','.join(sorted(field_names)),
                 'status':'Pending','paper_only':True,'no_bet_placed':True,
                 'no_validated_ev':True,'calibration_approved':False,
                 'policy':VERSION})
            locked[record_key]=recorded[-1];item['paper_tracking']='NEW_PREOFF_DIAGNOSTIC';added+=1
        elif preoff and record_key in locked:
            item['paper_tracking']='ALREADY_LOCKED'
        races.append(item)
    settled=settle(recorded,results,now)
    summary={'races_with_fields':len(races),'form_and_price_compared':stats['with_full_market_and_form'],
             'positive_rank_overlays':stats['positive_overlays'],
             'no_complete_verified_form':stats['without_verified_form'],
             'no_complete_current_book':stats['without_complete_current_book'],
             'post_off_comparisons':stats['post_off_comparison_only'],
             'new_prospective_paper_diagnostics':added,'paper_settlements_updated':settled,
             'overlays_paper_roi':evaluate([r for r in recorded if r.get('track_type')=='RANK_OVERLAY']),
             'baseline_paper_roi':evaluate([r for r in recorded if r.get('track_type')=='ALL_RACE_FORM_PRICE_BENCHMARK'])}
    result={'version':1,'policy':VERSION,'day':date,'generated_at':now.isoformat(),
        'state':'ALL_RACES_SCREENED_FORM_VERSUS_BOOKMAKER_NO_APPROVED_VALUE_PROBABILITY',
        'price_feed_state':price_status,'price_audit':price_audit,
        'uses_frozen_model_d':True,'models_changed':False,'z_frozen_unchanged':True,
        'no_betting_recommendations':True,'no_real_bets':True,
        'explanation':('Compares the independent Model D ranking against ONE current complete bookmaker market. '
            'Shows the best discrepancy for every race when covered. A positive rank gap is not a real expected-value estimate. '
            'Records one paper benchmark or overlay before the off for independent ROI testing.'),
        'summary':summary,'races':races}
    write(data/'form-price-opportunities.json',result)
    history['last_updated']=now.isoformat();history['policy']=VERSION
    write(history_path,history)
    print('Stage41',date,'races',len(races),'form-market',summary['form_and_price_compared'],
          'overlays',summary['positive_rank_overlays'],'new paper',added,'settled',settled)
    return result


def self_test():
    t=dt.datetime(2026,11,4,12,tzinfo=UTC)
    field=[{'horse':x} for x in ('Alpha','Beta','Gamma','Delta')]
    odds={norm(x):{'Paddy Power':{'horse':x,'decimal':v,'bookmaker':'Paddy Power',
        'quote_observed_at':'2026-11-04T11:50:00+00:00',
        'quote_timestamp_quality':'COLLECTOR_ONLY'}}
          for x,v in zip(('Alpha','Beta','Gamma','Delta'),(2.,3.,5.,10.))}
    book,reason=quote_source(field,odds,t,'FRESH')
    assert reason=='COMPLETE_SAME_BOOKMAKER_BOOK' and book['bookmaker']=='Paddy Power'
    form=[{'horse':h,'rank':j+1,'research_score':1-j/10} for j,h in enumerate(('Gamma','Beta','Alpha','Delta'))]
    scoring,overlay,best=shortlist(form,book)
    assert overlay['horse']=='Gamma' and overlay['rank_advantage']==2
    assert best['horse']=='Gamma'
    assert next(r for r in scoring if r['horse']=='Alpha')['rank_advantage']==-2
    formshort=[{'horse':h,'rank':j+1} for j,h in enumerate(('Alpha','Beta','Gamma'))]
    p,s=model_D_ranking(field[:3],{'runners':formshort,'complete_field':True},
       {'day':'2026-11-04','generated_at':'2026-11-04T11:00:00Z'},'2026-11-04',
       dt.datetime(2026,11,4,14,tzinfo=UTC))
    assert p and s=='D_CURRENT'
    p,s=model_D_ranking(field,{'runners':formshort,'complete_field':True},
       {'day':'2026-11-04','generated_at':'2026-11-04T11:00:00Z'},'2026-11-04',
       dt.datetime(2026,11,4,14,tzinfo=UTC))
    assert p is None and s=='D_FIELD_CHANGED_REASSESSMENT_PENDING'
    assert quote_source(field,odds,t,'NO_FRESH_PRICE_SNAPSHOT')[0] is None
    assert evaluate([])['roi_pct'] is None
    a=[{'date':'2026-11-04','course':'Dundalk (AW)','time':'12:00','horse':'Alpha',
        'status':'Pending','decimal_odds':4.,'track_type':'RANK_OVERLAY'}]
    results={'races':[{'date':'2026-11-04','course':'Dundalk','off_dt':'2026-11-04T12:00:00Z',
      'status':'final','runners':[{'horse':'Alpha','position':1,'status':'ran'}]}]}
    assert settle(a,results,t)==1 and a[0]['profit_units']==3.
    assert evaluate(a)['roi_pct']==300
    print('Stage41 self-test passed: one complete book, ordinal gaps, stale guards, ROI settlement')


def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=('run','self-test'))
    p.add_argument('--repo',default='.');p.add_argument('--day')
    a=p.parse_args();self_test() if a.command=='self-test' else build(a.repo,a.day)

if __name__=='__main__':main()
