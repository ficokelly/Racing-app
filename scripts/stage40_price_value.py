#!/usr/bin/env python3
"""Stage 40: strictly pre-off, price-aware research without modifying A/B/C/D/Z.

A & C: compare only independent full-field probabilities from their own current
forecast with actual timed bookmaker win prices. These comparisons are
UNVALIDATED, PAPER-ONLY, until independent forward calibration proves them.
B & D & Z: research rankings only; no numerical probability/EV is invented.

Published original quote, model version, event field and timestamp are immutable.
Settled outcomes update result fields only. No actual bets are placed.
"""
from __future__ import annotations
import argparse, collections, datetime as dt, html, json, math, re
from pathlib import Path
from zoneinfo import ZoneInfo

UTC=dt.timezone.utc
IRE=ZoneInfo('Europe/Dublin')
VERSION='stage40-price-aware-research-v1'
PAPER_POLICY='stage40-paper-only-edge20pct-and-4pp-v1'
MAX_SNAPSHOT_AGE_MIN=60
MAX_QUOTE_AGE_MIN=120
MIN_TO_OFF_MIN=2
MODEL_PROBS={'A':'probability','C':'win_probability'}


def read(path, default=None):
    try:return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):return default if default is not None else {}


def write(path,obj):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_suffix(p.suffix+'.tmp')
    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    tmp.replace(p)


def parse_time(value):
    if not value:return None
    try:
        t=dt.datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return t.replace(tzinfo=UTC) if not t.tzinfo else t.astimezone(UTC)
    except (TypeError,ValueError):return None


def normal(value):
    x=str(value or '').lower().strip()
    x=re.sub(r'\((?:ire|gb|fr|usa|ger|aw|ir|i)\)\s*$','',x,flags=re.I)
    return re.sub(r'[^a-z0-9]','',x)


def course(value):
    x=normal(value)
    aliases={'kemptonpark':'kempton','lingfieldpark':'lingfield','chelmsfordcity':'chelmsford',
             'newcastleaw':'newcastle','wolverhamptonaw':'wolverhampton','dundalkaw':'dundalk',
             'southwellaw':'southwell','sandownpark':'sandown','thecurragh':'curragh'}
    return aliases.get(x,x)


def clock(value):
    t=parse_time(value)
    if t:return t.astimezone(IRE).strftime('%H:%M')
    s=str(value or '')
    return s[:5] if re.fullmatch(r'\d\d:\d\d',s[:5]) else ''


def day(value):return str(value or '')[:10]


def race_key(r):
    return (day(r.get('date') or r.get('race_time') or r.get('off_dt')),
            course(r.get('course') or r.get('venue')),
            clock(r.get('race_time') or r.get('off_dt') or r.get('time')))


def is_nr(r):
    # Preserve every explicit Stage26 withdrawal flag, including upstream
    # camelCase names, rather than interpreting a missing flag as a starter.
    flag_names=('non_runner','is_non_runner','withdrawn','is_withdrawn',
                'scratched','isNonRunner','nonRunner','isWithdrawn','isScratched')
    flagged=any(r.get(k) in (True,1,'true','True','1') for k in flag_names)
    statuses=('runner_status','status','horse_status','result_status',
              'runnerStatus','horseStatus')
    marked=any(normal(r.get(k)) in {'nr','nonrunner','withdrawn','scratched','isnonrunner'}
               for k in statuses)
    return str(r.get('number') or '').upper()=='NR' or flagged or marked


def number(v):
    try:
        if isinstance(v,str):
            v=v.strip().lower()
            if '/' in v and re.fullmatch(r'\d+/\d+',v):
                a,b=map(float,v.split('/'));return 1+a/b if b else None
            if v in {'evs','evens','even'}:return 2.
        n=float(v)
        return n if math.isfinite(n) else None
    except (TypeError,ValueError,ZeroDivisionError):return None


def quote_book(v):
    a=normal(v)
    lookup={
     'paddypower':'Paddy Power','pp':'Paddy Power','williamhill':'William Hill',
     'bet365':'Bet365','skybet':'Sky Bet','ladbrokes':'Ladbrokes',
     'coral':'Coral','betfair':'Betfair','boylesports':'BoyleSports',
     'unibet':'Unibet','betvictor':'BetVictor','betfred':'Betfred',
     '10bet':'10bet','betway':'Betway','sportingbet':'Sportingbet'}
    return lookup.get(a,str(v or '').strip())


def odds_index(payload,when,target_date):
    collected=parse_time(payload.get('collected_at'))
    age=((when-collected).total_seconds()/60) if collected else None
    status='FRESH' if age is not None and -2<=age<=MAX_SNAPSHOT_AGE_MIN else 'NO_FRESH_PRICE_SNAPSHOT'
    index=collections.defaultdict(dict);ignored=collections.Counter()
    for r in payload.get('prices',[]):
        if not isinstance(r,dict):continue
        key=race_key(r)
        if key[0]!=target_date or not all(key):continue
        h=normal(r.get('horse'));b=quote_book(r.get('bookmaker'))
        price=number(r.get('decimal_odds'))
        if not h or not b or price is None or not 1.01<price<=501:
            ignored['invalid_price']+=1;continue
        if r.get('stale') is True or str(r.get('stale')).lower()=='true':
            ignored['source_marked_stale']+=1;continue
        updated=parse_time(r.get('bookmaker_updated_at'))
        observed=updated or collected
        if not observed or observed>when+dt.timedelta(minutes=2):
            ignored['missing_or_future_timestamp']+=1;continue
        quote_age=(when-observed).total_seconds()/60
        if quote_age>MAX_QUOTE_AGE_MIN:
            ignored['old_bookmaker_quote']+=1;continue
        entry={'horse':r.get('horse'),'bookmaker':b,'decimal':price,
               'price_updated_at':updated.isoformat() if updated else None,
               'quote_observed_at':observed.isoformat(),
               'collector_observed_at':collected.isoformat() if collected else None,
               'quote_timestamp_quality':'BOOKMAKER_UPDATED_AT' if updated else 'COLLECTOR_ONLY',
               'opening_decimal':number(r.get('opening_odds')),
               'race_id':r.get('race_id')}
        k=(h,b)
        old=index[key].get(k)
        if old is None or entry['quote_observed_at']>old['quote_observed_at']:
            index[key][k]=entry
    return index,status,{'age_minutes':round(age,1) if age is not None else None,
                         'collected_at':payload.get('collected_at'),
                         'ignored_rows':dict(ignored),'indexed_races':len(index)}


def validated_prediction(field,model_race,model,model_feed,target_date,off):
    if not model_race or model_feed.get('day')!=target_date:
        return None,'NO_SAME_DAY_MODEL_FORECAST'
    if model_race.get('complete_field') is False:
        return None,'INCOMPLETE_MODEL_FIELD'
    model_ts=parse_time(model_race.get('published_at') or model_feed.get('generated_at'))
    if not model_ts or model_ts>=off:
        return None,'MODEL_PUBLISHED_AFTER_OFF_OR_UNDATED'
    items=model_race.get('runners') or []
    field_ids=[normal(r.get('horse')) for r in field]
    model_ids=[normal(r.get('horse')) for r in items]
    if len(field_ids)!=len(items) or set(field_ids)!=set(model_ids) or len(set(model_ids))!=len(model_ids):
        return None,'MODEL_FIELD_CHANGED_OR_MISSING_NONRUNNER_REASSESSMENT'
    col=MODEL_PROBS[model]
    probs={}
    for r in items:
        n=number(r.get(col))
        if n is None or n<0 or n>1:return None,'INVALID_MODEL_PROBABILITY'
        probs[normal(r['horse'])]=n
    if abs(sum(probs.values())-1)>.009:return None,'MODEL_PROBABILITIES_DO_NOT_SUM_TO_ONE'
    return probs,'VALID_CURRENT_FULL_FIELD_FORECAST'


def preference(quotes_by_horse):
    """Default Paddy Power, otherwise a named alternative (never mixed overround)."""
    if not quotes_by_horse:return None
    if 'Paddy Power' in quotes_by_horse:return quotes_by_horse['Paddy Power']
    for b in ('William Hill','Bet365','Sky Bet','Ladbrokes','Coral','BoyleSports','Unibet','Betfair'):
        if b in quotes_by_horse:return quotes_by_horse[b]
    return sorted(quotes_by_horse.values(),key=lambda r:r['bookmaker'])[0]


def market_info(active,quotes):
    available=set.intersection(*(set(quotes.get(normal(r['horse']),{})) for r in active)) if active else set()
    over={}
    for b in sorted(available):
        ps=[quotes[normal(r['horse'])][b]['decimal'] for r in active]
        margin=sum(1/x for x in ps)
        if 0.80<margin<2.0:over[b]={'overround':round(margin,6),'priced_runners':len(active)}
    return over


def best_quote_for_runner(h,bookmaker_quotes,market):
    if not bookmaker_quotes:return None
    q=preference(bookmaker_quotes)
    if not q:return None
    result=dict(q)
    b=q['bookmaker']
    m=market.get(b)
    result['break_even_pct']=round(100/q['decimal'],3)
    result['market_fair_pct']=round(100/(q['decimal']*m['overround']),3) if m else None
    result['bookmaker_overround_pct']=round(100*(m['overround']-1),2) if m else None
    result['full_bookmaker_field']=bool(m)
    result['movement_pct']=round(100*((q['opening_decimal']/q['decimal'])-1),2) if q.get('opening_decimal') and q['opening_decimal']>1 else None
    return result


def value_for_model(p,quote):
    if p is None or not quote:return None
    imp=1/quote['decimal'];margin=p-imp;ev=quote['decimal']*p-1
    mkt=quote.get('market_fair_pct')
    return {'model_win_pct':round(100*p,2),'break_even_pct':round(100*imp,2),
            'market_fair_pct':mkt,'model_minus_break_even_pp':round(100*margin,2),
            'model_minus_market_fair_pp':round(100*p-mkt,2) if mkt is not None else None,
            'theoretical_ev_pct':round(100*ev,2),
            'ev_is_unvalidated':True,
            'readme':'Theoretical EV relies on unapproved model calibration. Not a proven edge.'}


def candidate(v,quote,now,off,snapshot_status):
    if not v or not quote:return False,'NO_CURRENT_MATCHED_PRICE_OR_PROBABILITY'
    if snapshot_status!='FRESH':return False,'ODDS_SNAPSHOT_NOT_FRESH'
    if not quote['full_bookmaker_field']:return False,'FULL_FIELD_BOOKMAKER_PRICES_UNAVAILABLE'
    if quote['quote_timestamp_quality']!='BOOKMAKER_UPDATED_AT':
        return False,'BOOKMAKER_QUOTE_TIME_NOT_VERIFIED'
    if off<=now+dt.timedelta(minutes=MIN_TO_OFF_MIN):return False,'RACE_TOO_CLOSE_OR_STARTED'
    price=quote['decimal']
    if not 1.25<=price<=21:return False,'PRICE_OUTSIDE_RESEARCH_SCREEN'
    if v['theoretical_ev_pct']<20 or v['model_minus_break_even_pp']<4:
        return False,'MODEL_PRICE_EDGE_BELOW_PAPER_SCREEN'
    if v['model_minus_market_fair_pp'] is None or v['model_minus_market_fair_pp']<3:
        return False,'MODEL_MARKET_SHARE_EDGE_TOO_SMALL'
    if v['model_win_pct']/max(0.1,v['market_fair_pct'])>1.65:
        return False,'EXTREME_UNVALIDATED_MODEL_MARKET_DISAGREEMENT'
    return True,'PAPER_RESEARCH_CANDIDATE_NOT_A_BET'


def settle(history,results,when):
    grouped=collections.defaultdict(list)
    for r in results.get('races',[]):
        if r.get('status')=='final':grouped[race_key(r)].append(r)
    changed=0
    for row in history:
        if row.get('status')!='Pending':continue
        matches=grouped.get((row['date'],course(row['course']),row['time']),[])
        if len(matches)!=1:continue
        people=matches[0].get('runners') or []
        found=[r for r in people if normal(r.get('horse'))==normal(row['horse'])]
        if len(found)!=1:continue
        horse=found[0]
        if is_nr(horse):status='Non-runner';pos=None
        else:
            pos=number(horse.get('position'))
            if pos is None:continue
            status='Won' if int(pos)==1 else 'Lost'
        row['status']=status;row['position']=int(pos) if pos is not None else None
        row['settled_at']=when.isoformat();row['settlement_source']='VERIFIED_FINAL_RACING_RESULT'
        if row.get('decimal_odds') and status in ('Won','Lost'):
            row['profit_units']=round((row['decimal_odds']-1) if status=='Won' else -1,4)
        changed+=1
    return changed


def build(root,target_date=None,now=None):
    root=Path(root);data=root/'data'
    now=now or dt.datetime.now(UTC)
    today=now.astimezone(IRE).date().isoformat()
    target_date=target_date or today
    dt.date.fromisoformat(target_date)
    racecards=read(data/'racecards.json')
    odds=read(data/'latest-odds.json')
    feeds={m:read(data/f) for m,f in {
       'A':'strategy-probabilities.json','B':'strategy-b-all-races.json',
       'C':'strategy-c-probabilities.json','D':'strategy-d-probabilities.json',
       'Z':'strategy-z-probabilities.json'}.items()}
    group=collections.defaultdict(list)
    for r in racecards.get('runners',[]):
        if day(r.get('date') or r.get('race_time'))==target_date and r.get('horse') and r.get('course') and r.get('race_time'):
            group[race_key(r)].append(r)
    odds_idx,price_status,odds_audit=odds_index(odds,now,target_date)
    models={}
    for model,f in feeds.items():
        idx={}
        for r in f.get('races',[]):
            if day(r.get('date') or r.get('race_time'))==target_date:
                k=race_key(r)
                if k not in idx:idx[k]=r
        models[model]=idx
    history_path=data/'value-paper-history.json'
    forecast_path=data/'value-forecast-history.json'
    hist=read(history_path,{'version':1,'mode':'PAPER_ONLY','selections':[]})
    past=hist.setdefault('selections',[])
    snapshots=read(forecast_path,{'version':1,'observations':[]})
    forecast_observations=snapshots.setdefault('observations',[])
    past_keys={(r.get('date'),course(r.get('course')),r.get('time'),r.get('model')):r
               for r in past if r.get('status') in ('Pending','Won','Lost','Non-runner')}
    observed_keys={(r.get('date'),course(r.get('course')),r.get('time'),r.get('model'))
                   for r in forecast_observations if r.get('status') in ('Pending','Won','Lost','Non-runner')}
    coverage=collections.Counter();races=[];new_papers=0;new_forecasts=0
    historical_stage39=read(root/'reports/stage39/stage39-summary.json')
    historical_course=collections.defaultdict(list)
    for r in historical_stage39.get('per_course_report_rows',[]):
        if r.get('model')=='global_frozen_D' and r.get('scope')=='ORIGINAL_FROZEN_GATE' \
           and r.get('period') in ('2025_ALREADY_REVIEWED','2026_RECORDED_RESULTS_TO_SOURCE_CUTOFF'):
            historical_course[r['course']].append({
             'period':r['period'],'roi_pct':r.get('roi_pct'),
             'races':r.get('races'),'priced_bets':r.get('priced_bets')})
    calibration={'A':read(data/'strategy-online-calibration.json'),
                 'B':read(data/'strategy-b-calibration.json'),
                 'C':{'state':'NO_APPROVED_FORWARD_CALIBRATION'},
                 'D':{'state':'UNCALIBRATED_SCORE_ONLY'},
                 'Z':{'state':'FROZEN_UNCALIBRATED_SPECIALIST_SCORE'}}
    for k,field_all in sorted(group.items(),key=lambda kv:(kv[0][2],kv[0][1])):
        active=[r for r in field_all if not is_nr(r)]
        if len(active)<2 or len(set(normal(r['horse']) for r in active))!=len(active):
            coverage['invalid_race_field']+=1;continue
        off=parse_time(active[0].get('race_time'))
        if not off:coverage['race_time_unavailable']+=1;continue
        race_id=active[0].get('race_id')
        raw_quotes=odds_idx.get(k,{}) if price_status=='FRESH' else {}
        quoted={}
        for (h,b),q in raw_quotes.items():
            if h not in {normal(r['horse']) for r in active}:continue
            # PuntersEdge and The Racing API issue different race IDs.
            # Match uniquely on date + normalized venue + exact off time + horse.
            # Record cross-provider IDs as a diagnostic rather than rejecting the quote.
            if race_id and q.get('race_id') and q['race_id']!=race_id:
                coverage['different_provider_race_ids_matched_by_time_and_horse']+=1
            quoted.setdefault(h,{})[b]=q
        margin=market_info(active,quoted)
        field_prices={normal(r['horse']):best_quote_for_runner(normal(r['horse']),quoted.get(normal(r['horse'])),margin)
                      for r in active}
        n_priced=sum(bool(v) for v in field_prices.values())
        if n_priced:coverage['races_with_any_prices']+=1
        if margin:coverage['races_with_complete_bookmaker_book']+=1
        meta={'date':target_date,'course':active[0]['course'],'time':k[2],
              'race_time':active[0]['race_time'],'race_id':race_id,
              'race_name':active[0].get('race_name'),'race_type':active[0].get('type') or active[0].get('race_type'),
              'field_size':len(active),'non_runners_excluded':len(field_all)-len(active),
              'priced_runners':n_priced,'full_bookmakers':list(margin),
              'preoff_at_generation':off>now,'models':{},'paper_candidates':[]}
        dkind=(models['D'].get(k) or {}).get('race_type')
        if dkind not in ('aw','flat','jumps'):
            lower=str(active[0].get('race_name') or '').lower()
            dkind=('jumps' if any(x in lower for x in ('hurdle','chase','steeple','bumper'))
                   else 'aw' if k[1] in ('dundalk','wolverhampton','chelmsford') or '(aw)' in str(active[0]['course']).lower()
                   else 'flat')
        meta['stage39_context']=historical_course.get(dkind+'|'+k[1],[])
        live_fingerprint=','.join(sorted(normal(r['horse']) for r in active))
        if off>now:
            for prev in past:
                if prev.get('date')==target_date and course(prev.get('course'))==k[1] and prev.get('time')==k[2] \
                   and prev.get('status')=='Pending' and prev.get('frozen_field_fingerprint')!=live_fingerprint:
                    prev['status']='Superseded';prev['superseded_at']=now.isoformat()
                    past_keys.pop((target_date,k[1],k[2],prev['model']),None)
            for prev in forecast_observations:
                if prev.get('date')==target_date and course(prev.get('course'))==k[1] and prev.get('time')==k[2] \
                   and prev.get('status')=='Pending' and prev.get('frozen_field_fingerprint')!=live_fingerprint:
                    prev['status']='Superseded';prev['superseded_at']=now.isoformat()
                    observed_keys.discard((target_date,k[1],k[2],prev['model']))
        for model,feed in feeds.items():
            source=models[model].get(k)
            if model in MODEL_PROBS:
                probabilities,state=validated_prediction(active,source,model,feed,target_date,off)
                if not probabilities:
                    meta['models'][model]={'status':state,'leader':None,'model_probability_available':False}
                    coverage[f'{model}_pending_or_stale']+=1
                    continue
                ordered=sorted(active,key=lambda r:-probabilities[normal(r['horse'])])
                lead=ordered[0]
                m={'status':'PRICE_COMPARED_EXPERIMENTAL','model_version':source.get('model_version') or feed.get('model_version'),
                   'leader':lead['horse'],'model_probability_available':True,
                   'model_win_pct':round(100*probabilities[normal(lead['horse'])],2),
                   'calibration_status':calibration[model].get('state','UNVERIFIED'),
                   'runners':[]}
                for runner in ordered:
                    h=normal(runner['horse']);q=field_prices.get(h)
                    p=probabilities[h];v=value_for_model(p,q)
                    eligible,reason=candidate(v,q,now,off,price_status)
                    # Price research must not override Model C's independent
                    # sparse-form / extreme market disagreement safety veto.
                    if model=='C' and (source.get('selection_status')=='RISK_FILTERED' or
                         source.get('decision',{}).get('safety_review',{}).get('pass') is False):
                        eligible,reason=False,'MODEL_C_INDEPENDENT_SAFETY_FILTER_BLOCKED'
                    item={'horse':runner['horse'],'horse_id':runner.get('horse_id'),
                          'probability':round(p,6),'price':q,'value':v,
                          'paper_screen_passed':eligible,'screen_reason':reason}
                    m['runners'].append(item)
                    if eligible:meta['paper_candidates'].append({'model':model,'horse':runner['horse'],
                                    'price':q,'value':v,'model_version':m['model_version'],
                                    'screen_reason':reason})
                meta['models'][model]=m
                coverage[f'{model}_valid_fullfield']+=1
            else:
                if not source:
                    meta['models'][model]={'status':'NO_CURRENT_RANKING','model_probability_available':False}
                    continue
                ordered=source.get('runners') or []
                if {normal(r['horse']) for r in ordered}!={normal(r['horse']) for r in active}:
                    meta['models'][model]={'status':'STALE_OR_INCOMPLETE_RANKING','model_probability_available':False}
                    continue
                if source.get('complete_field') is False or any(r.get('rank') is None for r in ordered):
                    meta['models'][model]={'status':'INCOMPLETE_PROVISIONAL_RANKING',
                      'model_probability_available':False,
                      'explanation':'Some runners have no supported model rank; a whole-field winner cannot be claimed.'}
                    continue
                ordered=sorted(ordered,key=lambda r:(r.get('rank') is None,number(r.get('rank')) or 999))
                leader=ordered[0]
                name=leader.get('horse')
                m={'status':'RANK_ONLY_NO_VALUE_PROBABILITY','model_version':source.get('model_version') or feed.get('model_version'),
                   'leader':name,'model_probability_available':False,
                   'leader_price':field_prices.get(normal(name)),
                   'top_three':[{'horse':r.get('horse'),'rank':r.get('rank'),
                                 'score':r.get('research_score',r.get('score'))} for r in ordered[:3]],
                   'explanation':'No calibrated win chance; cannot compute Model '+model+' value or expected ROI.'}
                if model=='Z':m['specialist_status']=source.get('selection_status','NO_SELECTION')
                meta['models'][model]=m
                coverage[f'{model}_current_rankings']+=1
            # Immutable, top-ranked source forecasts capture before racing;
            # these are OBSERVATIONS for future calibration, never real bets.
            key_obs=(target_date,k[1],k[2],model)
            obj=meta['models'][model]
            if key_obs not in observed_keys and off>now+dt.timedelta(minutes=MIN_TO_OFF_MIN) and obj.get('leader'):
                leader=obj['leader'];q=field_prices.get(normal(leader))
                forecast_observations.append({**{x:meta[x] for x in ('date','course','time','race_time','race_id','field_size')},
                  'model':model,'model_version':obj.get('model_version'),
                  'horse':leader,'published_at':now.isoformat(),
                  'odds':q['decimal'] if q else None,
                  'bookmaker':q['bookmaker'] if q else None,
                  'quote_observed_at':q['quote_observed_at'] if q else None,
                  'probability':round(probabilities[normal(leader)],6) if model in MODEL_PROBS and probabilities else None,
                  'status':'Pending','no_bet_placed':True,'record_type':'FORECAST_NOT_A_BET',
                  'frozen_field_fingerprint':live_fingerprint})
                observed_keys.add(key_obs);new_forecasts+=1
        # Never generate multiple bets from the same model/race. A paper
        # candidate is chosen by theoretical EV ONLY among eligible runners.
        for model in MODEL_PROBS:
            choices=[r for r in meta['paper_candidates'] if r['model']==model]
            if not choices:continue
            best=max(choices,key=lambda r:(r['value']['theoretical_ev_pct'],r['value']['model_minus_break_even_pp']))
            pk=(target_date,k[1],k[2],model)
            if pk in past_keys:continue
            q=best['price']
            past.append({**{x:meta[x] for x in ('date','course','time','race_time','race_id','field_size')},
             'horse':best['horse'],'model':model,'model_version':best['model_version'],
             'published_at':now.isoformat(),'decimal_odds':q['decimal'],'bookmaker':q['bookmaker'],
             'quote_observed_at':q['quote_observed_at'],'quote_timestamp_quality':q['quote_timestamp_quality'],
             'model_probability':best['value']['model_win_pct']/100,
             'theoretical_ev_pct_unvalidated':best['value']['theoretical_ev_pct'],
             'break_even_pct':best['value']['break_even_pct'],
             'market_fair_pct':best['value']['market_fair_pct'],
             'frozen_field_fingerprint':','.join(sorted(normal(r['horse']) for r in active)),
             'status':'Pending','paper_only':True,'no_bet_placed':True,
             'selection_policy':PAPER_POLICY,'calibration_approved':False})
            past_keys[pk]=past[-1];new_papers+=1
        # Only display one strongest candidate per model, not all that happened
        # to have model-price disagreements in the race.
        meta['paper_candidates']=[max([x for x in meta['paper_candidates'] if x['model']==m],
                                      key=lambda x:x['value']['theoretical_ev_pct'])
                                   for m in MODEL_PROBS if any(x['model']==m for x in meta['paper_candidates'])]
        races.append(meta)
        coverage['races_assessed']+=1
    results=read(data/'race-results.json')
    settled=settle(past,results,now)
    settled_forecasts=settle(forecast_observations,results,now)
    hist['last_updated']=now.isoformat();hist['policy']=PAPER_POLICY
    snapshots['last_updated']=now.isoformat()
    metrics={}
    for model in ('A','C'):
        rows=[x for x in past if x['model']==model and x.get('status') in ('Won','Lost') and x.get('decimal_odds')]
        wins=sum(x['status']=='Won' for x in rows)
        profit=sum(x.get('profit_units',0) for x in rows)
        metrics[model]={'settled_priced':len(rows),'wins':wins,
                        'win_rate_pct':round(wins/len(rows)*100,2) if rows else None,
                        'profit_units':round(profit,2),
                        'real_price_paper_roi_pct':round(profit/len(rows)*100,2) if rows else None}
    paper={'version':1,'day':target_date,'generated_at':now.isoformat(),'policy':PAPER_POLICY,
           'state':'RESEARCH_ONLY_CALIBRATION_UNVALIDATED','calibration':{m:v.get('state') for m,v in calibration.items()},
           'readme':'Forecasts independent of odds; bookmaker break-even and vig-free market are separate. Uncalibrated model EV is not a proven betting edge.',
           'price_feed_status':price_status,'price_feed_audit':odds_audit,
           'races':races,'coverage':dict(coverage),'new_paper_candidates':new_papers,
           'new_forward_forecasts':new_forecasts,'settlements_updated':settled,
           'forecast_settlements_updated':settled_forecasts,'paper_performance':metrics,
           'real_bets_placed':0,'my_bets_and_pnl_modified':False,'stage34_z_weights_modified':False}
    write(data/'value-opportunities.json',paper)
    write(history_path,hist);write(forecast_path,snapshots)
    print('Stage40',target_date,'races',len(races),'price feed',price_status,
          'with_any_prices',coverage['races_with_any_prices'],
          'A/C complete',coverage['A_valid_fullfield'],coverage['C_valid_fullfield'],
          'new paper',new_papers,'new forecasts',new_forecasts,'settled',settled)
    return paper


def self_test():
    fixed=dt.datetime(2026,11,4,8,0,tzinfo=UTC)
    assert course('Wolverhampton (AW)')=='wolverhampton'
    assert course('Kempton Park')=='kempton'
    assert race_key({'date':'2026-11-04','course':'Dundalk (AW)','race_time':'2026-11-04T18:45:00Z'})==('2026-11-04','dundalk','18:45')
    assert is_nr({'number':'NR'}) and not is_nr({'status':'declared'})
    assert number('5/1')==6. and number('Evs')==2.
    active=[{'horse':'One'},{'horse':'Two'},{'horse':'Three'}]
    model={'complete_field':True,'runners':[{'horse':'Two','probability':.3},
           {'horse':'One','probability':.5},{'horse':'Three','probability':.2}]}
    f={'day':'2026-11-04','generated_at':'2026-11-04T07:45:00Z'}
    off=dt.datetime(2026,11,4,12,0,tzinfo=UTC)
    p,status=validated_prediction(active,model,'A',f,'2026-11-04',off)
    assert status=='VALID_CURRENT_FULL_FIELD_FORECAST' and p['one']==.5
    p,status=validated_prediction(active[:2],model,'A',f,'2026-11-04',off)
    assert p is None and 'FIELD_CHANGED' in status
    quotes={normal(r['horse']):{'Paddy Power':{'decimal':q,'bookmaker':'Paddy Power'}} for r,q in zip(active,[2.,3.,6.])}
    m=market_info(active,quotes)
    assert round(m['Paddy Power']['overround'],6)==1.
    q=best_quote_for_runner('one',quotes['one'],m)
    assert q['break_even_pct']==50 and q['market_fair_pct']==50.
    q.update(quote_timestamp_quality='BOOKMAKER_UPDATED_AT')
    val=value_for_model(.6,q)
    assert round(val['theoretical_ev_pct'])==20
    elig,_=candidate(val,q,fixed,off,'FRESH')
    assert elig is True
    p,status=validated_prediction(active,model,'A',{'day':'2026-11-03',
                                  'generated_at':'2026-11-03T09:00:00Z'},'2026-11-04',off)
    assert p is None and status=='NO_SAME_DAY_MODEL_FORECAST'
    audit=[{'date':'2026-11-04','course':'Dundalk (AW)','time':'12:00',
            'horse':'One','status':'Pending','decimal_odds':3.25}]
    results={'races':[{'date':'2026-11-04','course':'Dundalk','off_dt':'2026-11-04T12:00:00Z',
                      'status':'final','runners':[{'horse':'One','position':1,'status':'ran'}]}]}
    assert settle(audit,results,fixed)==1 and audit[0]['profit_units']==2.25
    assert settle(audit,results,fixed)==0
    print('Stage40 self-test OK: full-field, stale guards, PP margin, theoretical EV, settlement')


def main():
    p=argparse.ArgumentParser()
    p.add_argument('command',choices=('run','self-test'))
    p.add_argument('--repo',default='.')
    p.add_argument('--day')
    a=p.parse_args()
    if a.command=='self-test':self_test()
    else:build(a.repo,a.day)

if __name__=='__main__':main()
