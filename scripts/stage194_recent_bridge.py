"""Compatibility adapter: retain Stage171's original bridge code and validate only the approved post-baseline additions."""
import inspect
import json
import pathlib
import textwrap
import stage171_recent_history as original

OLD = "assert baseline==BASE,('unexpected bridge baseline',baseline)"
NEW = 'validate_recovered_baseline(con, baseline, target, root)'

def validate_recovered_baseline(con, baseline, target, root):
    base = original.BASE
    assert baseline and base <= baseline < target, ('unexpected bridge baseline', baseline, target)
    if baseline == base:
        return
    from scripts import stage182c_verified_source as V
    ledger = json.loads((pathlib.Path(root)/'stage182h-approved-history.json').read_text())
    assert ledger['approved_run'] == 45522254220 and ledger['source_version'] == 132 and len(ledger['races']) == 12
    allowed = {e['id']:e for e in ledger['races'] if base < e['date'] < target}
    recent = list(con.execute('select id,race_date from historical_races where race_date>?', (base,)))
    assert recent and len(recent)==len({r[0] for r in recent}), 'invalid post-baseline race identifiers'
    for rid, date in recent:
        assert rid in allowed and allowed[rid]['date'] == date, ('unapproved post-baseline race', rid, date)
        race, runners = V.fetch(con, rid)
        assert len(runners) == allowed[rid]['runners'] and V.signature(race, runners) == allowed[rid]['sha256'], ('post-baseline recovery changed', rid)
    print('STAGE194_VERIFIED_POST_BASELINE', len(recent), baseline, flush=True)

def adapted_bridge():
    source = textwrap.dedent(inspect.getsource(original.append_bridge))
    # Fail closed if Stage171's upstream implementation changes unexpectedly.
    assert source.count(OLD) == 1, 'Stage171 assertion changed; review adapter before using'
    assert 'slots&existing' in source and 'BASE<day<target' in source, 'Stage171 overlap or time guard changed'
    ns = dict(vars(original), validate_recovered_baseline=validate_recovered_baseline)
    exec(compile(source.replace(OLD, NEW, 1), 'stage194-guarded-stage171', 'exec'), ns)
    return ns['append_bridge']

def append_bridge(db, reference, target, root=pathlib.Path('data')):
    return adapted_bridge()(db, reference, target, root)
