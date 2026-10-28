"""Archive the recorded research runs without copying build products.
This is the publication manifest for the 2026-10-27 experiment, not a test runner.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import hashlib
import json
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[1]
RUNS={
    'matrix':('integration-20261027-195651',None,'results.json'),
    'lossless':('integration-20261027-194654',None,'results.json'),
    'idr-baseline':('integration-20261027-194603',None,'results.json'),
    'experiments':('experiments-20261027-200513',None,'results.json'),
    'upstream-skeskinen':('upstream-20261027-192616','skeskinen-','verified-results.json'),
    'upstream-deepregular-python':('upstream-20261027-193211','deepregular_python-','verified-results.json'),
    'upstream-copy-gap':('upstream-20261027-195552',None,'results.json'),
}

def copy(src,dst):
    dst.parent.mkdir(parents=True,exist_ok=True)
    if dst.exists():
        if src.read_bytes()!=dst.read_bytes(): raise RuntimeError(f'Refusing to overwrite changed evidence: {dst}')
    else: shutil.copyfile(src,dst)

for name,(run,prefix,report) in RUNS.items():
    folder=ROOT/'test-output'/run
    copy(folder/report,ROOT/'docs/evidence'/(name+'.json'))
    copy(folder/report,ROOT/'test-data'/name/'results.json')
    for path in sorted(folder.rglob('*')):
        if not path.is_file() or path.suffix not in ('.mp4','.json','.log'): continue
        if path.name in ('results.json','verified-results.json'): continue
        if prefix and not path.name.startswith(prefix): continue
        copy(path,ROOT/'test-data'/name/path.relative_to(folder))

manifest={'experiment_date':'2026-10-27','archive_date':'2026-10-28',
          'source_runs':{k:v[0] for k,v in RUNS.items()},'files':[]}
for path in sorted((ROOT/'test-data').rglob('*')):
    if path.is_file() and path.suffix in ('.mp4','.json','.log') and path.name!='manifest.json':
        manifest['files'].append({'path':path.relative_to(ROOT).as_posix(),'bytes':path.stat().st_size,
                                  'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
(ROOT/'test-data/manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
videos=[x for x in manifest['files'] if x['path'].endswith('.mp4')]
print(json.dumps({'files':len(manifest['files']),'mp4_files':len(videos),
                  'media_bytes':sum(x['bytes'] for x in videos),'total_bytes':sum(x['bytes'] for x in manifest['files'])},indent=2))
