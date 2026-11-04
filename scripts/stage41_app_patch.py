#!/usr/bin/env python3
"""Idempotent Stage41 additive entry points, no model logic changes."""
from pathlib import Path

def patch(root):
    path=Path(root)/'index.html'
    if not path.exists():return False
    s=path.read_text(encoding='utf-8');original=s
    menu='<a class="form-price-nav" href="./form-price.html" aria-label="Form versus bookmaker prices">💡 Form vs Price</a>'
    if 'class="form-price-nav"' not in s:
        marker='</nav></div></div><div class="rhp-main-toolbar">'
        if marker not in s:raise RuntimeError('App menu anchor changed; refusing unsafe patch')
        s=s.replace(marker,menu+marker,1)
    for target in ('strategy-selection-list','strategy-results-list'):
        # The app has different div attribute orders for selections and results.
        code=(f'<div class="rhp-strategy-items" id="{target}"' if target=='strategy-selection-list'
              else f'<div id="{target}" class="rhp-strategy-items"')
        nav=f'<div class="panel stage41-link" style="padding:12px 14px;margin:10px 0"><strong>💡 Form versus bookmaker prices</strong><p style="margin:5px 0;line-height:1.5">Independent form ranking versus actual bookmaker odds for every assessable race. <a href="./form-price.html">Open the paper-value research</a>.</p></div>'
        if target not in s:raise RuntimeError('App list anchor missing: '+target)
        if f'data-stage41="{target}"' not in s:
            s=s.replace(code,nav.replace('class="panel stage41-link"',f'class="panel stage41-link" data-stage41="{target}"')+code,1)
    if s!=original:path.write_text(s,encoding='utf-8')
    return s!=original

def self_test():
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        p=Path(t)/'index.html'
        p.write_text('<nav id="section-buttons"></nav></div></div><div class="rhp-main-toolbar">'+
          '<div class="rhp-strategy-items" id="strategy-selection-list"></div>'+
          '<div id="strategy-results-list" class="rhp-strategy-items"></div>')
        assert patch(t) is True
        assert patch(t) is False
        s=p.read_text();assert s.count('class="form-price-nav"')==1 and s.count('class="panel stage41-link"')==2
    print('Stage41 nav patch self-test passed')

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--repo',default='.');p.add_argument('--self-test',action='store_true')
    a=p.parse_args();self_test() if a.self_test else patch(a.repo)
