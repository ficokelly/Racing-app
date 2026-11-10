#!/usr/bin/env python3
"""Stage87: lossless, independently restorable SQLite HPR horse-key shards.

Research-only store. Does not alter any frozen model or live data. Each horse,
including all its historical revisions, belongs to one deterministic shard.
GitHub Actions commits the shards only after an independent restore check.
"""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
import pathlib
import shutil
import sqlite3
import tempfile

N = 16
LIMIT = 16 * 1024 * 1024  # fail conservatively, well under GitHub file limits
COLS = ('horse_key','race_key','race_date','model_version','revision','source_sha',
        'hpr','confidence','payload','inserted_utc')
SCHEMA = '''CREATE TABLE IF NOT EXISTS hpr_ratings (
 horse_key TEXT NOT NULL, race_key TEXT NOT NULL, race_date TEXT NOT NULL,
 model_version TEXT NOT NULL, revision INTEGER NOT NULL, source_sha TEXT NOT NULL,
 hpr REAL NOT NULL, confidence TEXT NOT NULL, payload TEXT NOT NULL,
 inserted_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
 PRIMARY KEY(horse_key,race_key,model_version,revision));
 CREATE INDEX IF NOT EXISTS hpr_horse_date ON hpr_ratings(horse_key,race_date);
 CREATE INDEX IF NOT EXISTS hpr_race_date ON hpr_ratings(race_date);
 CREATE TABLE IF NOT EXISTS hpr_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);'''
ORDER = 'ORDER BY horse_key,race_key,model_version,revision'
FIELDS = ','.join(COLS)
INSERT = 'INSERT INTO hpr_ratings (' + FIELDS + ') VALUES (' + ','.join('?' for _ in COLS) + ')'


def read_db(path, *, create=False):
    path = pathlib.Path(path)
    if not create and not path.is_file():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(str(path))
    conn.execute('PRAGMA busy_timeout=10000')
    if create:
        conn.executescript(SCHEMA)
    else:
        if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('SQLite integrity check failed: ' + str(path))
        tables = {x[0] for x in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if tables != {'hpr_ratings','hpr_metadata'}:
            raise ValueError('Unrecognised HPR tables; refusing lossy archive: '+str(tables))
        actual = {x[1] for x in conn.execute('PRAGMA table_info(hpr_ratings)')}
        if actual != set(COLS):
            raise ValueError('Unexpected HPR schema: ' + str(actual ^ set(COLS)))
    return conn


def ledger(conn):
    rows = conn.execute('SELECT ' + FIELDS + ' FROM hpr_ratings ' + ORDER)
    h = hashlib.sha256()
    count = 0
    for row in rows:
        h.update(json.dumps(row,ensure_ascii=False,separators=(',',':')).encode('utf-8'))
        h.update(b'\n')
        count += 1
    return count, h.hexdigest()


def archive(database, directory):
    directory = pathlib.Path(directory)
    src = read_db(database)
    meta = list(src.execute('SELECT key,value FROM hpr_metadata ORDER BY key'))
    total, digest = ledger(src)
    if not total:
        raise ValueError('Refusing to archive an empty HPR ledger')
    directory.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='stage87-shards-') as tmpdir:
        tmp = pathlib.Path(tmpdir)
        dbs = []
        for i in range(N):
            c = read_db(tmp / f'part-{i:02d}.sqlite',create=True)
            c.executemany('INSERT INTO hpr_metadata(key,value) VALUES (?,?)',meta)
            dbs.append(c)
        counts = [0] * N
        try:
            for row in src.execute('SELECT ' + FIELDS + ' FROM hpr_ratings ' + ORDER):
                bucket = hashlib.sha256(row[0].encode('utf-8')).digest()[0] % N
                dbs[bucket].execute(INSERT,row)
                counts[bucket] += 1
            for c in dbs:
                c.commit()
        finally:
            for c in dbs:
                c.close()
            src.close()
        manifest = {'format':'stage87-hpr-sqlite-shards-v1','parts':N,
                    'total_rows':total,'ledger_sha256':digest,'metadata':meta,'shards':[]}
        for i in range(N):
            db = tmp / f'part-{i:02d}.sqlite'
            with read_db(db) as c:
                shard_n = c.execute('SELECT COUNT(*) FROM hpr_ratings').fetchone()[0]
                if shard_n != counts[i]:raise ValueError('Shard row mismatch')
            name = f'hpr-part-{i:02d}.sqlite.gz'
            out = tmp / name
            with db.open('rb') as source, out.open('wb') as target:
                with gzip.GzipFile(filename='',mode='wb',fileobj=target,mtime=0,compresslevel=6) as z:
                    shutil.copyfileobj(source,z,1024*1024)
            size = out.stat().st_size
            if size > LIMIT:raise ValueError(f'Shard {i} is {size:,} bytes; increase shard count safely')
            manifest['shards'].append({'file':name,'rows':counts[i],
                'bytes':size,'sha256':hashlib.sha256(out.read_bytes()).hexdigest()})
        (tmp/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
        # Source database remains untouched. Publish new snapshot atomically.
        staging = directory.with_name(directory.name + '-stage87-new')
        if staging.exists():shutil.rmtree(staging)
        staging.mkdir(parents=True)
        for entry in manifest['shards']:
            shutil.copyfile(tmp/entry['file'],staging/entry['file'])
        shutil.copyfile(tmp/'manifest.json',staging/'manifest.json')
        # Validate by reconstructing all shards BEFORE retiring prior files.
        rebuilt = tmp/'verified-restore.sqlite'
        restore(staging,rebuilt,legacy=None)
        if staging != directory:
            if directory.exists():shutil.rmtree(directory)
            staging.rename(directory)
    return manifest


def restore(directory,destination,legacy=None):
    directory = pathlib.Path(directory)
    destination = pathlib.Path(destination)
    manifest_file = directory/'manifest.json'
    destination.parent.mkdir(parents=True,exist_ok=True)
    if not manifest_file.is_file():
        if legacy is None or not pathlib.Path(legacy).is_file():
            raise FileNotFoundError('No HPR shard manifest or verified legacy archive')
        with gzip.open(legacy,'rb') as source, destination.open('wb') as out:
            shutil.copyfileobj(source,out,1024*1024)
        with read_db(destination) as con:
            count, digest = ledger(con)
        return {'mode':'legacy','total_rows':count,'ledger_sha256':digest}
    m = json.loads(manifest_file.read_text(encoding='utf-8'))
    if m.get('format') != 'stage87-hpr-sqlite-shards-v1' or len(m.get('shards',[])) != m.get('parts'):
        raise ValueError('Invalid Stage87 shard manifest')
    if destination.exists():destination.unlink()
    target = read_db(destination,create=True)
    target.executemany('INSERT INTO hpr_metadata(key,value) VALUES (?,?)',m['metadata'])
    try:
        with tempfile.TemporaryDirectory(prefix='stage87-restore-') as tmpdir:
            for entry in m['shards']:
                filename = entry['file']
                if pathlib.Path(filename).name != filename:raise ValueError('Invalid shard name')
                archive_file = directory/filename
                raw = archive_file.read_bytes()
                if len(raw) != entry['bytes'] or hashlib.sha256(raw).hexdigest() != entry['sha256']:
                    raise ValueError('HPR shard checksum mismatch: ' + filename)
                temp_db = pathlib.Path(tmpdir)/'shard.sqlite'
                with gzip.open(archive_file,'rb') as source, temp_db.open('wb') as out:
                    shutil.copyfileobj(source,out,1024*1024)
                with read_db(temp_db) as source:
                    if list(source.execute('SELECT key,value FROM hpr_metadata ORDER BY key')) != [tuple(x) for x in m['metadata']]:
                        raise ValueError('Shard metadata mismatch: '+filename)
                    shard_count = 0
                    for row in source.execute('SELECT ' + FIELDS + ' FROM hpr_ratings ' + ORDER):
                        target.execute(INSERT,row)  # duplicates are errors, not silently ignored
                        shard_count += 1
                    if shard_count != entry['rows']:
                        raise ValueError('Shard count mismatch: '+filename)
                temp_db.unlink()
        target.commit()
        count,digest = ledger(target)
        if count != m['total_rows'] or digest != m['ledger_sha256']:
            raise ValueError('Combined HPR ledger mismatch: restore incomplete')
        if target.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('Restored HPR DB failed integrity check')
        return {'mode':'shards','total_rows':count,'ledger_sha256':digest}
    except Exception:
        target.close()
        destination.unlink(missing_ok=True)
        raise
    finally:
        try:target.close()
        except Exception:pass


def self_test():
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td)
        src = read_db(p/'source.sqlite',create=True)
        src.execute('INSERT INTO hpr_metadata VALUES (?,?)',('version','HPR-test'))
        rows = [('P|ALPHA|SIRE|DAM','HIST|one','2026-11-07','v0',1,'sig1',61.4,'PARTIAL','{"hpr":61.4}','2026-11-08T00:00:00Z'),
                ('P|ALPHA|SIRE|DAM','HIST|one','2026-11-07','v0',2,'sig2',63.4,'HIGH','{"hpr":63.4}','2026-11-09T00:00:00Z'),
                ('ID|€234','HIST|two','2026-11-09','v0',1,'sig3',81.0,'HIGH','{"hpr":81}','2026-11-10T00:00:00Z')]
        src.executemany(INSERT,rows);src.commit()
        original = ledger(src);src.close()
        manifest = archive(p/'source.sqlite',p/'shards')
        result = restore(p/'shards',p/'restored.sqlite')
        with read_db(p/'restored.sqlite') as restored:
            assert ledger(restored) == original
        assert manifest['total_rows'] == 3 and result['mode'] == 'shards'
        (p/'shards'/'hpr-part-00.sqlite.gz').write_bytes(b'corrupt')
        try:restore(p/'shards',p/'bad.sqlite')
        except ValueError:pass
        else:raise AssertionError('Corruption not detected')
    print('PASS: lossless HPR shard roundtrip, revisions, Unicode, checksum guard')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('mode',choices=('archive','restore','self-test'))
    ap.add_argument('--db',type=pathlib.Path)
    ap.add_argument('--dir',type=pathlib.Path,default=pathlib.Path('data/hpr/shards'))
    ap.add_argument('--legacy',type=pathlib.Path,default=pathlib.Path('data/hpr/hpr-ratings.sqlite.gz'))
    opts = ap.parse_args()
    if opts.mode == 'self-test':self_test();return
    if not opts.db:ap.error('--db required')
    res = archive(opts.db,opts.dir) if opts.mode == 'archive' else restore(opts.dir,opts.db,opts.legacy)
    print(json.dumps(res,indent=2))

if __name__ == '__main__':main()
