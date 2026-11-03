#!/usr/bin/env python3
"""Additive unified A/B/C/D/Z app upgrade. Preserves existing tabs, models and sources."""
from pathlib import Path
import argparse,re
CSS='''
/* Stage38 readable narrative and five-model navigation */
.rhp-model-picker .rhp-ab-switch,.rhp-ab-switch{display:flex!important;gap:7px!important;overflow-x:auto!important;scrollbar-width:thin;padding:3px 0!important;grid-template-columns:none!important}
.rhp-model-picker .rhp-ab-switch button,.rhp-ab-switch button{flex:1 0 78px!important;min-width:76px!important;padding:11px 8px!important;font-size:14px!important}
.rhp-verdict.s38-verdict,.rhp-verdict{font-size:14.5px!important;line-height:1.62!important;padding:14px!important;overflow-wrap:anywhere}
.rhp-verdict.s38-verdict strong{font-size:15px!important;color:var(--theme-accent,#a8f5d5);line-height:1.4}
.rhp-horse-comment{font-size:14.5px!important;line-height:1.64!important;padding:14px!important;overflow-wrap:anywhere}
.rhp-horse-comment b,.rhp-horse-comment strong{font-size:inherit!important}
.s38-para{display:block;margin-top:4px;line-height:1.62;white-space:normal}
.s38-para.s38-after{margin-top:12px}
.s38-verdict .s38-para{margin-top:11px}
.s38-overview{margin:18px 0;border:1px solid var(--theme-border,#54616b);border-radius:13px;padding:12px;background:var(--theme-panel,rgba(128,128,128,.08))}
.s38-overview>summary{font-weight:750;font-size:15px;cursor:pointer}
.s38-overview-items{display:grid;gap:8px;margin-top:14px}
.s38-overview-race{display:flex;justify-content:space-between;gap:13px;border-bottom:1px solid var(--theme-border,rgba(128,128,128,.3));padding:10px 0;font-size:13.5px;line-height:1.45}
.s38-overview-race>strong{flex:0 0 38%}
.s38-overview-race span{min-width:0;flex:1;text-align:right}
.s38-overview-race small{display:block;opacity:.8;font-size:12px;margin-top:4px}
.s38-paper-card p{font-size:14.5px;line-height:1.6}
/* Horse-card wrap: do not squeeze trainer and jockey into an unreadable strip */
.summarycard>summary{flex-wrap:wrap!important;align-items:flex-start}
.summarycard>summary>span:first-child{min-width:0!important;flex:1 1 46%!important}
.summarycard>summary>.rhp-win-chance{flex:0 0 auto!important;max-width:100%;white-space:normal!important;overflow-wrap:anywhere}
.rc-people-preview{display:block!important;max-width:100%;overflow-wrap:break-word}
.rc-people-preview .rc-person-line{font-size:13px!important;line-height:1.45!important;margin:4px 0}
@media(max-width:430px){.summarycard>summary>span:first-child{flex:1 1 58%!important}.summarycard>summary .rc-odds-button{min-width:82px;max-width:108px}.summarycard>summary>.rhp-win-chance{flex:1 0 100%!important;text-align:left}}
'''

def run(path,script_path):
 p=Path(path);s=p.read_text(encoding='utf8');js=Path(script_path).read_text(encoding='utf8')
 marker='/* Stage38 readable narrative and five-model navigation */'
 jsmarker='// Stage38: integrated, labelled A/B/C/D/Z independent race assessment views.'
 def exact(a,b,label):
  nonlocal s
  if b in s:
   print('Already applied',label);return
  n=s.count(a)
  if n!=1:raise RuntimeError(f'Anchor {label}: expected exactly 1, got {n}')
  s=s.replace(a,b,1)
 exact('Compare A, B and C','Compare A, B, C, D and Z','racecards title')
 exact('data-rc-model="C">Model C</button>', 'data-rc-model="C">Model C</button><button type="button" data-rc-model="D">Model D</button><button type="button" data-rc-model="Z">Model Z</button>','racecard buttons')
 exact('data-ab-model="C" data-ab-view="selections">Model C</button>', 'data-ab-model="C" data-ab-view="selections">Model C</button><button type="button" data-ab-model="D" data-ab-view="selections">Model D</button><button type="button" data-ab-model="Z" data-ab-view="selections">Model Z</button>','selection buttons')
 exact('data-ab-model="C" data-ab-view="results">Model C</button>', 'data-ab-model="C" data-ab-view="results">Model C</button><button type="button" data-ab-model="D" data-ab-view="results">Model D</button><button type="button" data-ab-model="Z" data-ab-view="results">Model Z</button>','result buttons')
 # Add D/Z to persisted racecard model only, not automatically selected.
 exact("['A','B','C'].includes(v.rcSelectedModel)","['A','B','C','D','Z'].includes(v.rcSelectedModel)",'persisted model')
 if marker not in s:
  # Keep before first style close and after older theme classes; specificity protects theme.
  if '</style>' not in s:raise RuntimeError('App style section missing')
  s=s.replace('</style>',CSS+'\n</style>',1)
 if jsmarker not in s:
  anchor='setup();\n// Manual refresh'
  if s.count(anchor)!=1:raise RuntimeError('Missing last setup invocation')
  s=s.replace(anchor,js+'\nsetup();\n// Manual refresh',1)
 if s.count('data-rc-model="D"')!=1 or s.count('data-rc-model="Z"')!=1:raise RuntimeError('Duplicate model-picker button')
 if s.count(jsmarker)!=1 or s.count(marker)!=1:raise RuntimeError('Missing Stage38 additive guards')
 p.write_text(s,encoding='utf8')
 # Retain existing manual-only Model Z paper observations; add a distinct automatic ledger.
 z=Path('model-z.html')
 if z.exists():
  h=z.read_text(encoding='utf8')
  if 'id="s38-automatic-z"' not in h:
   anchor='<section class="section"><div class="panel"><div class="subhead"><h2>Paper tracking</h2>'
   panel='<section class="section"><div class="panel"><h2>Automatic Model Z paper selections</h2><div id="s38-automatic-z" class="small">Checking pre-race paper logs…</div></div></section>\n'
   if h.count(anchor)!=1:raise RuntimeError('Model Z paper panel anchor changed; refusing unsafe patch')
   h=h.replace(anchor,panel+anchor,1)
  if '// Automatic frozen Model Z selections are published separately from manual observations.' not in h:
   path=Path('scripts/stage38_z_dashboard.js')
   js=path.read_text(encoding='utf8') if path.exists() else ''
   if not js:raise RuntimeError('Missing Stage38 Z dashboard source')
   if h.count('</body></html>')!=1:raise RuntimeError('Model Z footer anchor changed')
   h=h.replace('</body></html>','<script>\n'+js+'\n</script></body></html>',1)
  z.write_text(h,encoding='utf8')
 print('Stage38 installed: readable commentary, 5-model racecards/selections/results, source guard preserved')

if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('--index',default='index.html');a.add_argument('--js',default='scripts/stage38_app.js');a.add_argument('--self-test',action='store_true');q=a.parse_args()
 if q.self_test:
  from tempfile import TemporaryDirectory
  with TemporaryDirectory() as tmp:
   p=Path(tmp)/'index.html';p.write_text('''<html><style></style><body>Compare A, B and C
<button data-rc-model="C">Model C</button>
<button data-ab-model="C" data-ab-view="selections">Model C</button>
<button data-ab-model="C" data-ab-view="results">Model C</button>
['A','B','C'].includes(v.rcSelectedModel)
<script>function setup(){}\nsetup();
// Manual refresh</script></body></html>''')
   run(p,q.js);old=p.read_bytes();run(p,q.js);assert p.read_bytes()==old
  print('Stage38 UI idempotence and anchor test passed')
 else:run(q.index,q.js)
