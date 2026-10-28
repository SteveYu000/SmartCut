"""Validate the reorganized workspace without changing historical outcomes.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import ast
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.parse

ROOT=Path(__file__).resolve().parent
if ROOT.name=='tools':ROOT=ROOT.parent
parser=argparse.ArgumentParser()
parser.add_argument('--metadata-only',action='store_true',help='Reuse prior smoke results after clearing build caches')
args=parser.parse_args()
RESEARCH=ROOT/'research'
SDK=RESEARCH/'h264/deps/ffmpeg-n8.1.3-24-g92611900bb-win64-gpl-shared-8.1/bin'
os.environ['PYTHONDONTWRITEBYTECODE']='1'
report={'date':'2026-10-29','python_files':0,'json_files':0,'broken_links':[],'smoke':[]}
for path in RESEARCH.rglob('*'):
    if not path.is_file() or any(part in {'deps','bin','obj','artifacts','__pycache__'} for part in path.parts):continue
    if path.suffix=='.py':ast.parse(path.read_text(encoding='utf-8-sig'),filename=str(path));report['python_files']+=1
    if path.suffix in {'.json','.llc'}:json.loads(path.read_text(encoding='utf-8-sig'));report['json_files']+=1

for path in [ROOT/'README.md',*RESEARCH.rglob('*.md')]:
    if 'deps' in path.parts:continue
    for link in re.findall(r'\]\(([^)]+)\)',path.read_text(encoding='utf-8-sig')):
        link=link.strip('<>')
        if re.match(r'^[a-z]+:',link,re.I) or link.startswith('#'):continue
        target=urllib.parse.unquote(link.split('#')[0])
        if not (path.parent/target).exists():report['broken_links'].append({'file':str(path.relative_to(ROOT)),'target':target})

# Preserve the historical manifest verbatim. Record current navigation-only
# differences in a new manifest, without editing the original evidence.
manifest_path=RESEARCH/'h265/test-data/manifest.json'
manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
updated=[]
for row in manifest['files']:
    path=RESEARCH/'h265'/row['path']
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    if digest!=row['sha256']:
        updated.append({'path':'research/h265/'+row['path'],'original_sha256':row['sha256'],
                        'current_sha256':digest,'original_bytes':row['bytes'],'current_bytes':path.stat().st_size})
(RESEARCH/'layout-checksums.json').write_text(json.dumps(updated,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
report['refreshed_manifest_entries']=len(updated)

# Existing Markdown retains the original wording, with only README paths moved.
original_names=subprocess.check_output(['git','-c','core.quotepath=false','ls-tree','-r','--name-only','origin/main'],cwd=ROOT,encoding='utf-8').splitlines()
prose=[]
for name in original_names:
    if not name.endswith('.md'):continue
    expected=subprocess.check_output(['git','show','origin/main:'+name],cwd=ROOT)
    target=(RESEARCH/'source-review/REPORT.md' if name=='Smart_Rendering源码研究报告.md' else
            RESEARCH/name if name.split('/')[0] in {'h264','h265','pipeline'} else ROOT/name)
    if name=='README.md':
        text=expected.decode('utf-8').replace('](Smart_Rendering源码研究报告.md)','](research/source-review/REPORT.md)')
        for project in ('h264','h265','pipeline'):
            text=text.replace(']('+project+'/','](research/'+project+'/').replace('`'+project+'/`','`research/'+project+'/`')
        expected=text.encode('utf-8')
    prose.append({'original':name,'current':str(target.relative_to(ROOT)),'unchanged_except_paths':target.read_bytes()==expected})
report['original_markdown']=prose
if args.metadata_only:
    prior=json.loads((RESEARCH/'workspace-validation.json').read_text(encoding='utf-8'))
    report['smoke']=prior['smoke']
    report['smoke_reused_after_cache_cleanup']=True
    report['passed']=not report['broken_links'] and all(row['passed'] for row in report['smoke']) and all(row['unchanged_except_paths'] for row in prose)
    (RESEARCH/'workspace-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({key:report[key] for key in ('python_files','json_files','broken_links','refreshed_manifest_entries','passed')},ensure_ascii=False,indent=2))
    raise SystemExit(0 if report['passed'] else 1)

spec=importlib.util.spec_from_file_location('workspace_oracle',RESEARCH/'losslesscut-comparison/run_suite.py')
oracle=importlib.util.module_from_spec(spec);spec.loader.exec_module(oracle)
checks=ROOT/'local/checks'/str(time.time_ns());checks.mkdir(parents=True,exist_ok=True)
jobs=[
    ('h264-cpp','h264',ROOT/'local/build/h264/Release/smart_render.exe',RESEARCH/'h264/test-data/run-20261027-125923/source-closed_b3_id0.mp4'),
    ('h265-cpp','h265',ROOT/'local/build/h265/Release/smart_render.exe',RESEARCH/'h265/test-data/fixtures/source-open.mp4'),
    ('pipeline','h264',ROOT/'local/build/pipeline/bin/SmartCut.Cli/release/SmartCut.Cli.exe',RESEARCH/'h264/test-data/run-20261027-125923/source-closed_b3_id0.mp4'),
    ('pipeline-vfr','h264',ROOT/'local/build/vfr/bin/SmartCut.Cli/release/SmartCut.Cli.exe',RESEARCH/'h264/test-data/run-20261027-125923/source-vfr.mp4'),
]
try:
    for name,codec,exe,source in jobs:
        output=checks/(name+'.mp4');plan=checks/(name+'.json')
        command=[exe,'-i',source,'-o',output,'--keep','13:227','--report',plan]
        if name.startswith('pipeline'):command+=['--no-audio','--ffmpeg-dir',SDK,'--work-dir',checks]
        oracle.run(command)
        details=json.loads(plan.read_text(encoding='utf-8-sig'))
        result=oracle.check(codec,oracle.load_source(codec,source),output,[(13,227)],{'copied_frames':details['copied_frames']})
        report['smoke'].append({'project':name,**result})
        print(name,result['passed'],result['issues'],flush=True)
finally:oracle.POOL.shutdown(wait=True,cancel_futures=True)
report['passed']=not report['broken_links'] and all(row['passed'] for row in report['smoke'])
(RESEARCH/'workspace-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({k:report[k] for k in ('python_files','json_files','broken_links','refreshed_manifest_entries','passed')},ensure_ascii=False,indent=2))
