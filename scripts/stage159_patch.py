"""Stage159 installer patch (idempotent, fail-closed, no change to frozen predictors)."""
from __future__ import annotations
import pathlib
import re

ROOT=pathlib.Path('.')
SCRIPT=ROOT/'scripts'
MARK='STAGE159_MODEL_INPUT_DRIVERS_V1'
FILES=('stage141_today_or_weight_paper.py','stage151_d142_core_form_or_weight_today.py',
       'stage152_best_roi_specialists_today.py','stage147_daily_143_146.py',
       'stage128_g5_today_paper.py','stage153_roi_combined.py')


def change(s,old,new,label,expect=1):
    got=s.count(old)
    if got!=expect:raise RuntimeError(f'{label}: expected {expect} anchors, got {got}. No changes are written.')
    return s.replace(old,new)


def add_import(s):
    return change(s,'import numpy as np','import numpy as np\nimport stage159_explainability as E', 'explainability import')


def stage141(s):
    s=add_import(s)
    s=change(s,"        prob,bank=S.predict_bank(banks,H.hpr_ability_137(M.copy_race(r,weights=True,official=True)))", """        stage159_inputs=H.hpr_ability_137(M.copy_race(r,weights=True,official=True))
        prob,bank=S.predict_bank(banks,stage159_inputs)
        stage159_drivers=E.safe_forest(stage159_inputs['xf'],prob,banks[bank],S.FEATURES)""",'stage141 original predictions')
    s=change(s,"               'bank':bank,'decision_utc':utcnow().isoformat(),'odds_used':False,'result_known':False}",
       "               'bank':bank,'driver_json':json.dumps(stage159_drivers[ix],separators=(',',':')),\n               'decision_utc':utcnow().isoformat(),'odds_used':False,'result_known':False}",'stage141 preoff provenance')
    return s


def stage151(s):
    s=add_import(s)
    s=change(s,"        scores, bank = S.predict_bank(banks, H.hpr_ability_137(model_race))", """        stage159_inputs=H.hpr_ability_137(model_race)
        scores, bank = S.predict_bank(banks, stage159_inputs)
        stage159_drivers=E.safe_forest(stage159_inputs['xf'],scores,banks[bank],S.FEATURES)""",'stage151 original predictions')
    s=change(s,"                'raw_score_not_calibrated_win_pct': round(float(scores[idx]), 6),",
          "                'raw_score_not_calibrated_win_pct': round(float(scores[idx]), 6),\n                'driver_json': json.dumps(stage159_drivers[idx],separators=(',',':')),",'stage151 driver field')
    return s


def stage152(s):
    s=add_import(s)
    s=change(s,"            scores,branch=S.predict_bank(bank,H.hpr_ability_137(inputs))", """            stage159_inputs=H.hpr_ability_137(inputs)
            scores,branch=S.predict_bank(bank,stage159_inputs)
            stage159_drivers=E.safe_forest(stage159_inputs['xf'],scores,bank[branch],S.FEATURES)""",'stage152 ML scoring')
    first=s.index('        for name,inputs,bank in variants:')
    second=s.index("        name=P.W_G+'__ROI'",first)
    before=s[first:second]
    needle="                    'field_size':len(r['horse']),'prediction_asof':L.utcnow().isoformat(),\n                    'odds_used_for_current_prediction':False})"
    after=change(before,needle,
      "                    'field_size':len(r['horse']),'prediction_asof':L.utcnow().isoformat(),\n                    'driver_json':json.dumps(stage159_drivers[int(idx)],separators=(',',':')),\n                    'odds_used_for_current_prediction':False})",'stage152 ML runner CSV')
    s=s[:first]+after+s[second:]
    s=change(s,"        scores=r['scores'][P.W_G]@weights", """        scores=r['scores'][P.W_G]@weights
        stage159_pillars=E.safe_pillars(r['scores'][P.W_G],weights)""",'stage152 exact additive scoring')
    first=s.index("        name=P.W_G+'__ROI'")
    second=s.index('    if not rank1:',first)
    before=s[first:second]
    needle2="                'field_size':len(r['horse']),'prediction_asof':L.utcnow().isoformat(),\n                'odds_used_for_current_prediction':False})"
    after=change(before,needle2,
      "                'field_size':len(r['horse']),'prediction_asof':L.utcnow().isoformat(),\n                'driver_json':json.dumps(stage159_pillars[int(idx)],separators=(',',':')),\n                'odds_used_for_current_prediction':False})",'stage152 additive runner CSV')
    s=s[:first]+after+s[second:]
    return s


def stage147(s):
    s=add_import(s)
    s=change(s,"        predictions=F.predict(live,routing,config,bank,nonlinear=nonlinear,with_trend=with_trend)","""        predictions=F.predict(live,routing,config,bank,nonlinear=nonlinear,with_trend=with_trend)
        # The family-specific logistic model is linear in its scaled input features.
        # These are exact within-race log-odds contributions, not guessed causes.
        stage159_X,_,stage159_meta,_,stage159_names=F.fused_matrix(live,routing,config,nonlinear=nonlinear,with_trend=with_trend)
        stage159_lookup={(str(rid),int(ix)):k for k,(rid,ix) in enumerate(stage159_meta)}
        stage159_drivers={}
        for stage159_race in live:
            stage159_rid=str(stage159_race['race_id'])
            stage159_indices=[stage159_lookup[(stage159_rid,k)] for k in range(len(stage159_race['horse']))]
            stage159_family=routing[stage159_rid]['family']
            stage159_scaler,stage159_clf=bank.get(stage159_family,bank['__GLOBAL__'])
            stage159_drivers[stage159_rid]=E.safe_logistic(stage159_X[stage159_indices],stage159_scaler,stage159_clf,stage159_names)""",'stage147 logistic predictions')
    s=change(s,"                    'trend_horse_score':round(float(race['trend_features'][k][0]),5) if with_trend else '',",
          "                    'trend_horse_score':round(float(race['trend_features'][k][0]),5) if with_trend else '',\n                    'driver_json':json.dumps(stage159_drivers[rid][k],separators=(',',':')),",'stage147 runner CSV')
    return s


def stage128(s):
    s=add_import(s)
    s=change(s,"        p['raw_probability']=model['estimator'].predict_proba(x)[:,1]", """        p['raw_probability']=model['estimator'].predict_proba(x)[:,1]
        # Record input sensitivity BEFORE normalized race scores/reordering.
        stage159_items=['']*len(p)
        for stage159_rid,stage159_positions in p.groupby('race_id',sort=False).indices.items():
            stage159_ix=np.asarray(stage159_positions,dtype=int)
            stage159_mat=x.iloc[stage159_ix].to_numpy(dtype=float)
            stage159_scores=p.iloc[stage159_ix]['raw_probability'].to_numpy(dtype=float)
            stage159_impacts=E.safe_forest(stage159_mat,stage159_scores,model['estimator'],cols)
            for stage159_pos,stage159_impact in zip(stage159_ix,stage159_impacts):
                stage159_items[int(stage159_pos)]=json.dumps(stage159_impact,separators=(',',':'))
        p['driver_json']=stage159_items""",'stage128 G5 ranking input')
    return s


def stage153(s):
    s=change(s,"      'source_model':model,'trainer':c.get('trainer'),'jockey':c.get('jockey'),",
      "      'source_model':model,'trainer':c.get('trainer'),'jockey':c.get('jockey'),\n      'model_drivers':json.loads(pred['driver_json']) if pred.get('driver_json') else None,",'stage153 preserve frozen preoff evidence')
    s=change(s," _enrich_individual(payload, cards, args.sources)\n # Stage155 only changes prose; source-model runners, ranks, scores and picks are preserved.",
       " _enrich_individual(payload, cards, args.sources)\n from stage159_narrative import upgrade as _explain_picks\n _explain_picks(payload, cards)\n # Stage155 + Stage159 only change prose and evidence metadata; all frozen rankings, scores and picks remain unchanged.", 'stage153 explain before publish')
    return s

PATCHES={'stage141_today_or_weight_paper.py':stage141,
         'stage151_d142_core_form_or_weight_today.py':stage151,
         'stage152_best_roi_specialists_today.py':stage152,
         'stage147_daily_143_146.py':stage147,
         'stage128_g5_today_paper.py':stage128,
         'stage153_roi_combined.py':stage153}


def main():
    cache={}
    for name in FILES:
        path=SCRIPT/name
        old=path.read_text(encoding='utf-8')
        if MARK in old:
            print('ALREADY_APPLIED',name)
            continue
        new=PATCHES[name](old)
        # Only after all patches validate will any file be overwritten.
        compile(new,str(path),'exec')
        cache[path]=new+'\n# '+MARK+'\n'
    for p,s in cache.items():
        p.write_text(s,encoding='utf-8')
        print('STAGE159_PATCHED',str(p),len(s))
    return len(cache)

if __name__=='__main__':
    print('STAGE159_FILES_CHANGED',main())
