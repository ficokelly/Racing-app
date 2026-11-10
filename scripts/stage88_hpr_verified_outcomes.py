#!/usr/bin/env python3
"""Stage 88 HPR v1 research adapter: recover EXPLICIT documented non-finishers.

This script imports the existing frozen Stage86 research engine and changes only
how a finite set of already-recorded official result codes are interpreted for a
SEPARATE disposable Stage88 research backtest. Never rewrites original Stage86,
production Model D, or D(c). Never infers placements, winner statuses or margins.

Examples: a result with position='PU' and status='ran' is a verified pulled-up
runner, not an unknown numeric placing. Similarly F/UR/BD/RR/RO, and the historic
archive's explicit SU/REF/CO non-finisher codes. DSQ remains excluded as a
separate disqualification category; blank, '-', and 'RAN' remain unresolved.
"""
from __future__ import annotations
import importlib

VERSION='HPR-v1-2026-11-10-explicit-dnf-research'
# Position field can be nonnumeric in the results feed. Never fuzzy-match.
EXPLICIT_NONFINISH_CODES=frozenset(('pu','f','ur','bd','rr','ro','su','ref','co'))
EXPLICIT_NONFINISH_WORDS=frozenset((
  'pulled up','fell','unseated','unseated rider','brought down','refused',
  'ran out','slipped up','carried out'))
STOP=frozenset(('nr','non-runner','nonrunner','withdrawn','scratched','dns','did not start'))

def explicit_dnf(event):
    """True ONLY for a source's observed nonfinish code, not for missing results."""
    values=[event.get('position'),event.get('finish_pos'),event.get('status'),event.get('outcome')]
    def canon(v):return str(v or '').strip().lower().replace('_',' ').replace('-',' ')
    # A non-runner has no HPR performance. Do not convert to 5-points.
    statuses=(canon(event.get('status')),canon(event.get('outcome')))
    if any(s in ('nr','non runner','nonrunner','withdrawn','scratched','dns','did not start') for s in statuses):
        return False
    return any(canon(v) in EXPLICIT_NONFINISH_CODES|EXPLICIT_NONFINISH_WORDS for v in values)

def patch_engine(base):
    original=base.run_quality
    def verified_quality(event):
        # A numeric valid placing always wins over an ancillary DNF text flag;
        # conflicting source evidence should be treated as an investigation.
        if base.number(event.get('position')) is None and explicit_dnf(event):
            n=int(base.number(event.get('field_size')) or 0)
            if n<2:return None,'INVALID_FIELD'
            return 5.,'KNOWN_NONFINISHER_EXPLICIT_CODE'
        return original(event)
    base.run_quality=verified_quality
    base.VERSION=VERSION
    return base

def self_test():
    assert explicit_dnf({'position':'PU','status':'ran'})
    assert explicit_dnf({'position':'F','status':''})
    assert explicit_dnf({'position':None,'outcome':'SU'})
    assert explicit_dnf({'position':None,'status':'REF'})
    for ambiguous in ('','-','RAN','DSQ','void'):
        assert not explicit_dnf({'position':ambiguous,'status':'ran'}),ambiguous
    assert not explicit_dnf({'position':'PU','status':'NR'})
    class Fake:
        @staticmethod
        def number(v):
            try:return float(v)
            except (TypeError,ValueError):return None
        @staticmethod
        def run_quality(e):return (63.,'FINISHED')
    patch_engine(Fake)
    assert Fake.run_quality({'position':'PU','field_size':8,'status':'ran'})==(5.,'KNOWN_NONFINISHER_EXPLICIT_CODE')
    assert Fake.run_quality({'position':4,'field_size':8,'status':'ran'})==(63.,'FINISHED')
    assert Fake.run_quality({'position':'DSQ','field_size':8,'status':'ran'})==(63.,'FINISHED')
    print('PASS: explicit DNF code recovery, non-runner guard, unchanged numeric placings')

def main():
    self_test()
    import stage86_hpr_engine as base
    patch_engine(base)
    # Original self-tests still run on --mode self-test; backtest output goes to
    # the distinct output directory chosen in the workflow, never persistent HPR.
    base.main()

if __name__=='__main__':main()
