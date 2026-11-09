#!/usr/bin/env python3
"""Guarded surgical installation of Stage68 Racing Hot Pots feed/UI repairs.

Install will abort if expected code anchors are different; it never silently
replaces the frozen Model D scoring algorithm or promotes untested Model D(c).
"""
import pathlib

ROOT=pathlib.Path('.')


def replace_one(s, old, new, label):
    if new and new in s:
        print(label + ': already installed')
        return s
    n=s.count(old)
    if n != 1:
        raise RuntimeError(f'{label}: expected exactly one existing code anchor, found {n}; installation aborted')
    return s.replace(old,new,1)


def patch_index():
    path=ROOT/'index.html'
    s=path.read_text(encoding='utf-8')
    s=replace_one(s,
        "const value=stat?`${stat.wins}/${stat.runs} · ${stat.pct.toFixed(1)}% (${stat.availableDays}/${stat.calendarDays}d recorded${stat.partialCoverage?' · partial coverage':''})`:'Form unavailable';",
        "const value=stat?`${stat.wins}/${stat.runs} · ${stat.pct.toFixed(1)}%`:'Form unavailable';",
        'Clean trainer/jockey preview: retain wins/runs and 14-day strike rate')
    s=replace_one(s,
        "$('status').textContent='● ODDS SYNCED';$('asof').textContent=",
        "const rhpOddsAgeMinutes=(Date.now()-Date.parse(latest.collected_at))/60000;"
        "$('status').textContent=Number.isFinite(rhpOddsAgeMinutes)&&rhpOddsAgeMinutes>=20?"
        "'⚠ ODDS STALE · '+Math.floor(rhpOddsAgeMinutes)+'m':'● ODDS SYNCED';"
        "$('asof').textContent=",
        'Show genuine odds freshness instead of saying synced when stale')
    s=replace_one(s,
        " if(activeTab==='racecards')renderRacecards();\n}\nfunction rcBestRaceMetadata",
        " if(activeTab==='racecards')renderRacecards();\n if(activeTab==='powerresults')renderPowerResults();\n}\nfunction rcBestRaceMetadata",
        'Refresh Results screen immediately when official results are published')
    path.write_text(s,encoding='utf-8')


def patch_stage38_js():
    path=ROOT/'scripts/stage38_app.js'
    s=path.read_text(encoding='utf-8')
    s=replace_one(s,
        "<p class=\"hint\">Recorded ${s38esc(r.published_at||feed.generated_at)} · frozen score gap ${Number(r.decision?.gap||0).toFixed(3)}. No real bet placed.</p></article>",
        "<p class=\"hint\">Recorded ${s38esc(r.published_at||feed.generated_at)} · frozen score gap ${Number(r.decision?.gap||0).toFixed(3)}. No real bet placed.</p>"
        "<div class=\"actions\" style=\"margin-top:10px\"><button type=\"button\" class=\"action small outline\" data-rhp-hp-race=\"1\" "
        "data-rhp-hp-day=\"${s38esc(r.date)}\" data-rhp-hp-course=\"${s38esc(r.course)}\" "
        "data-rhp-hp-time=\"${s38esc(r.time)}\">View race</button></div></article>",
        'Add View Race to every published Hot Pot selection')
    context = '''// Stage68: only the provider's verified final finishing order; top three are
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
'''
    s=replace_one(s,
       'const s38OldResults=renderStrategyResults;',
       context+'const s38OldResults=renderStrategyResults;',
       'Explain finishing positions, SP, and official placings')
    s=replace_one(s,
        "${r.odds>1?s38esc((model==='Z'?'Pre-off odds':'Official SP')+' '+r.odds):'No verified bookmaker quote'}</p></article>",
        "${r.odds>1?s38esc((model==='Z'?'Pre-off odds':'Official SP')+' '+r.odds):'No verified bookmaker quote'}"
        "${r.position?' · Finish '+s38esc(r.position):r.finishing_status?' · '+s38esc(r.finishing_status):''}</p>"
        "${model==='D'?s38VerifiedFinishers(r):''}</article>",
        'Show official finishing position and runner placings in Results')
    handler = '''// Stage68: View Race works even when the selection has no bookmaker odds.
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
'''
    s=replace_one(s,'// Also load directly for cached app views which skip the usual tab setup.',
                  handler+'// Also load directly for cached app views which skip the usual tab setup.',
                  'Add full racecard pop-up handler for Hot Pot selections')
    path.write_text(s,encoding='utf-8')


def patch_workflows():
    path=ROOT/'.github/workflows/racing-hot-pots-five-minute.yml'
    s=path.read_text(encoding='utf-8')
    old="""      - name: Send newly detected Power Fav alerts
        env:
          ONESIGNAL_API_KEY: ${{ secrets.ONESIGNAL_API_KEY }}
          ONESIGNAL_SUBSCRIPTION_ID: ${{ secrets.ONESIGNAL_SUBSCRIPTION_ID }}
        run: python send-power-fav-alerts.py

"""
    s=replace_one(s,old,'','Disable legacy Power Fav push notifications')
    odds_publish='''      - name: Publish odds first (before slower racecard and result processing)
        shell: bash
        run: |
          set -euo pipefail
          git config user.name 'github-actions[bot]'
          git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
          git add data/latest-odds.json data/odds-history.json data/odds-archive/
          if git diff --cached --quiet; then exit 0; fi
          git commit -m 'Publish current bookmaker odds promptly'
          for attempt in 1 2 3 4; do
            if git pull --rebase origin main && git push origin HEAD:main; then exit 0; fi
            git rebase --abort || true
            sleep $((attempt * 3))
          done
          echo 'Could not publish timely odds: GitHub update conflict' >&2
          exit 1

'''
    s=replace_one(s,
       '      - name: Update final UK and Irish race results\n',
       odds_publish+'      - name: Update final UK and Irish race results\n',
       'Publish odds promptly rather than waiting for the slower result collection')
    reconciliation='''      - name: Reconcile verified results, finishing SPs and trainer/jockey form
        shell: bash
        run: |
          set -euo pipefail
          python scripts/stage68_result_reconciler.py
          # Only recompute 14-day form when new verified race results were appended.
          if [ -n "$(git status --porcelain -- data/racing-results/)" ]; then
            python calculate-strike-rates.py || echo "WARNING: Form rebuild failed; verified results will still be published."
          fi

'''
    s=replace_one(s,
       '      - name: Update and archive complete racecards once an hour\n',
       reconciliation+'      - name: Update and archive complete racecards once an hour\n',
       'Reconcile final results without rebuilding frozen Model D')
    s=replace_one(s,
       'git add data/aw2-track-stats.json data/latest-odds.json data/odds-history.json data/odds-archive/ data/power-fav-alert-state.json data/race-results.json data/racecards.json data/racecards-archive.json data/nonrunner-monitor.json',
       'git add data/aw2-track-stats.json data/latest-odds.json data/odds-history.json data/odds-archive/ data/race-results.json data/racecards.json data/racecards-archive.json data/nonrunner-monitor.json data/racing-results/ data/strike-rates.json data/form-identity-cache.json data/strategy-d-history.json data/strategy-d-results.json reports/stage32/form-rebuild-audit.json',
       'Publish reconciled final results, SP and form alongside other feeds')
    path.write_text(s,encoding='utf-8')

    path=ROOT/'.github/workflows/collect-racing-results.yml'
    s=path.read_text(encoding='utf-8')
    s=replace_one(s,
       '        run: python collect-racing-results.py\n',
       "        run: python collect-racing-results.py || echo 'Racing API today/free returned no qualifying results; checking verified PuntersEdge fallback.'\n",
       'Do not abort all form calculations if old daily API endpoint returns no results')
    fallback='''      - name: Refresh verified PuntersEdge results fallback
        env:
          PUNTERSEDGE_API_KEY: ${{ secrets.PUNTERSEDGE_API_KEY }}
        run: python collect-puntersedge-results.py

      - name: Reconcile final results into the dated form archive
        run: python scripts/stage68_result_reconciler.py

'''
    s=replace_one(s,
       '      - name: Calculate trainer and jockey strike rates\n',
       fallback+'      - name: Calculate trainer and jockey strike rates\n',
       'Keep 14-day form populated from independently verified results when daily source unavailable')
    s=replace_one(s,
       'git add data/racing-results/ data/strike-rates.json',
       'git add data/racing-results/ data/strike-rates.json data/race-results.json data/form-identity-cache.json data/strategy-d-history.json data/strategy-d-results.json reports/stage32/form-rebuild-audit.json',
       'Persist verified provider data and recomputed strike rates')
    s=replace_one(s,
       '            git push\n',
       '''            for attempt in 1 2 3 4; do
              if git pull --rebase origin main && git push origin HEAD:main; then
                exit 0
              fi
              git rebase --abort || true
              sleep $((attempt * 3))
            done
            exit 1
''',
       'Retry nightly form publication safely when multiple workflows write data')
    path.write_text(s,encoding='utf-8')



def patch_stage66_cache():
    # Explicitly improves the existing research-only A/B/C comparison. It does not
    # make C a live model, publish C selections or change its algorithm.
    path=ROOT/'.github/workflows/stage66-three-model-d-daily.yml'
    s=path.read_text(encoding='utf-8')
    cache='''      - name: Restore frozen historical SQLite baseline (research only)
        uses: actions/cache@v4
        with:
          path: reports/stage66/daily/original.sqlite
          key: stage66-frozen-baseline-ukie-v129-1988-20261007-v1

'''
    s=replace_one(s,
       '      - name: Download historical source and build baseline\n',
       cache+'      - name: Download historical source and build baseline\n',
       'Reuse verified immutable SQLite baseline instead of importing millions of rows each daily research run')
    s=replace_one(s,
       "          subprocess.run([sys.executable,'import_historical_ukie.py','--mode','import','--input',str(source),'--min-date','1988-01-01','--max-date','2026-10-07','--database','reports/stage66/daily/original.sqlite','--report','reports/stage66/daily/import.json'],check=True)",
       "          db=Path('reports/stage66/daily/original.sqlite')\n"
       "          if db.is_file() and db.stat().st_size >= 1000000:\n"
       "            import sqlite3\n"
       "            with sqlite3.connect('file:'+str(db.resolve())+'?mode=ro',uri=True) as cx:\n"
       "              valid=cx.execute('SELECT name FROM sqlite_master WHERE type=\"table\" AND name=\"historical_races\"').fetchone()\n"
       "            if not valid:raise RuntimeError('Cached baseline is not a historical SQLite racing database')\n"
       "            print('Stage66: reusing immutable cached historical SQLite instead of rebuilding it',flush=True)\n"
       "          else:\n"
       "            subprocess.run([sys.executable,'import_historical_ukie.py','--mode','import','--input',str(source),'--min-date','1988-01-01','--max-date','2026-10-07','--database',str(db),'--report','reports/stage66/daily/import.json'],check=True)",
       'Skip expensive baseline import only when cache contains the expected racing table')
    path.write_text(s,encoding='utf-8')


def patch_form_starters():
    path=ROOT/'scripts/stage32_form_rebuild.py'
    s=path.read_text(encoding='utf-8')
    old='''def is_starter(row: dict) -> bool:
    pos = str(row.get('position') or '').strip().upper()
    if pos in NONRUNNERS:
        return False
    if str(row.get('status') or '').strip().lower() in {'scratched','non-runner','withdrawn','abandoned'}:
        return False
    return True
'''
    new='''def is_starter(row: dict) -> bool:
    # Failed to complete is still a RUNNER for trainer/jockey strike-rate
    # denominators. Do not quietly drop falls and pulled-up horses.
    pos = str(row.get('position') or '').strip().upper()
    status = str(row.get('status') or '').strip().lower()
    if pos in {'NR','N/R','NON RUNNER','NON-RUNNER','WD','WITHDRAWN','SCRATCHED','DNS','VOID','ABANDONED'}:
        return False
    if status in {'nr','n/r','scratched','non runner','non-runner','withdrawn','abandoned','dns'}:
        return False
    if not pos:
        return status in {'unplaced','finished unplaced','pu','pulled up','f','fell','ur','unseated rider','bd','brought down','ro','ran out','r','refused','disqualified','dq'}
    return True
'''
    s=replace_one(s,old,new,'Correct 14-day form denominators for verified fallers/pulled-up runners')
    path.write_text(s,encoding='utf-8')

def main():
    patch_index()
    patch_stage38_js()
    patch_workflows()
    patch_form_starters()
    patch_stage66_cache()
    print('Stage68 reliability patches prepared; D(c) has not yet been promoted or silently substituted.')


if __name__=='__main__':
    main()
