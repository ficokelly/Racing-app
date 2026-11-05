#!/usr/bin/env python3
"""Safely snapshot and verify independent Racing Hot Pots model learning.

Python 3.11+; stdlib only. READ-ONLY with respect to source repository.

Usage:
  python stage46_preserve_learning.py snapshot --repo-root . --output "$RUNNER_TEMP/rhp-model-checkpoint"
  python stage46_preserve_learning.py verify --checkpoint "$RUNNER_TEMP/rhp-model-checkpoint"

Every snapshot copies the original trained states AND their existing predictions,
so later upgrades can be compared without accidentally rewriting the baseline.
It does NOT claim to retrain A/B/C/D/Z or promote any candidate.
"""
from __future__ import annotations
import argparse, datetime, hashlib, json, os, shutil, subprocess
from pathlib import Path

# Source learning states: required files missing -> fail closed.
REQUIRED = {
    'A': [
        'data/strategy-historical-model.json',
        'data/strategy-online-calibration.json',
        'data/strategy-ab-learning.json',
    ],
    'B': [
        'data/strategy-model-b-historical.json',
        'data/strategy-b-calibration.json',
    ],
    'C': [
        'data/strategy-model-c-historical.json',
        'data/strategy-c-learning.json',
        'data/strategy-c-history.json',
    ],
    'D': [
        'reports/stage30/stage30-summary.json',
        'reports/stage34/stage34-frozen-track-profiles.json',
    ],
    'Z': [
        'data/model-z-strategies.json',
        'data/strategy-z-paper-history.json',
    ],
}
OPTIONAL = [
    'data/history-integrity-audit.json',
    'data/strategy-learning-history.json',
    'data/strategy-commentary-learning.json',
    'data/strategy-ab-commentary-learning.json',
    'data/strategy-model-c-2025-report.json',
    'data/strategy-ab-historical-roi.json',
    'data/strategy-historical-model-stage24-candidate.json',
    'data/strategy-historical-model-pre-stage25.json',
    'data/strategy-model-b-pre-stage25.json',
    'data/strategy-model-c-historical-pre-stage25.json',
    'data/strategy-probabilities.json',
    'data/strategy-b-probabilities.json',
    'data/strategy-c-probabilities.json',
    'data/strategy-d-probabilities.json',
    'data/strategy-z-probabilities.json',
    'data/strategy-d-history.json',
    'data/model-z-status.json',
    'scripts/stage22_model_c.py',
    'scripts/stage30_gap_aware_abcd.py',
    'scripts/stage38_model_dz_live.py',
    'scripts/stage38_fullfield_bridge.py',
    '.github/workflows/stage13-strategy-learning.yml',
    '.github/workflows/stage21-model-c-live.yml',
    '.github/workflows/stage38-all-models-app-and-coverage.yml',
]

def sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):
            h.update(chunk)
    return h.hexdigest()

def git_sha(root:Path)->str|None:
    try:return subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True,stderr=subprocess.DEVNULL).strip()
    except (OSError,subprocess.CalledProcessError):return None

def snapshot(root:Path,out:Path)->dict:
    root=root.resolve();out=out.resolve()
    if root==out or root in out.parents and '/data/' in str(out):
        raise ValueError('Do not store checkpoint within active data directory')
    if (out/'manifest.json').exists():
        raise FileExistsError('Checkpoint already exists; create a new version, never overwrite it')
    targets={p for arr in REQUIRED.values() for p in arr}
    missing=[p for p in sorted(targets) if not (root/p).is_file() or (root/p).stat().st_size==0]
    if missing:raise FileNotFoundError('Missing essential original learning state: '+', '.join(missing))
    manifest={
       'schema_version':1,'purpose':'FROZEN_BASELINE_BEFORE_CONDITION_AWARE_RETRAIN',
       'created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
       'repository_commit':git_sha(root),
       'source_root':'repository_at_pinned_commit',
       'trained_models':['A','B','C','D','Z'],
       'preservation_policy':{
           'never_mutate_original_checkpoints':True,
           'do_not_reset_learning_history':True,
           'stage_new_versions_separately':True,
           'do_not_retrain_with_future_test_results':True,
           'no_live_model_changes':True,
           'new_feature_policy':'frozen old model + separate additive challenger; only use true warm-start if algorithm supports it',
           'rollback':'restore files from checkpoint after SHA256 verification; requires explicit human approval',
       },
       'by_model':REQUIRED,'files':{},'missing_optional':[]}
    out.mkdir(parents=True,exist_ok=True)
    for rel in sorted(targets|set(OPTIONAL)):
        src=root/rel
        if not src.is_file() or src.stat().st_size==0:
            manifest['missing_optional'].append(rel);continue
        dst=out/'files'/rel;dst.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(src,dst)
        manifest['files'][rel]={'bytes':dst.stat().st_size,'sha256':sha256(dst),'required':rel in targets}
        if sha256(src)!=manifest['files'][rel]['sha256']:
            raise RuntimeError('Source changed during copy: '+rel)
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n',encoding='utf8')
    verify(out)
    return manifest

def verify(out:Path)->dict:
    out=out.resolve();m=json.loads((out/'manifest.json').read_text(encoding='utf8'))
    if m.get('preservation_policy',{}).get('never_mutate_original_checkpoints') is not True:
        raise RuntimeError('Expected immutable baseline policy absent')
    required={p for paths in REQUIRED.values() for p in paths}
    if not required.issubset(m['files']):raise RuntimeError('Missing required original model snapshots')
    for rel,meta in m['files'].items():
        p=(out/'files'/rel).resolve()
        if not p.is_relative_to((out/'files').resolve()) or not p.is_file():
            raise RuntimeError('Checkpoint file missing or unsafe: '+rel)
        if p.stat().st_size!=meta['bytes'] or sha256(p)!=meta['sha256']:
            raise RuntimeError('Checkpoint changed or corrupted: '+rel)
    return {'verified':True,'model_families':list(REQUIRED),'files':len(m['files']),'repository_commit':m.get('repository_commit')}

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('command',choices=['snapshot','verify']);ap.add_argument('--repo-root',type=Path,default=Path('.'))
    ap.add_argument('--output',type=Path);ap.add_argument('--checkpoint',type=Path)
    a=ap.parse_args()
    if a.command=='snapshot':
        if not a.output:ap.error('snapshot requires --output')
        m=snapshot(a.repo_root,a.output)
        print(json.dumps({'created_checkpoint':str(a.output),'source_commit':m['repository_commit'],'families':m['trained_models'],'file_count':len(m['files']),'optional_missing':m['missing_optional']},indent=2))
    else:
        if not a.checkpoint:ap.error('verify requires --checkpoint')
        print(json.dumps(verify(a.checkpoint),indent=2))
if __name__=='__main__':main()
