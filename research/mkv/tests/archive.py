"""Archive textual evidence; media, SDKs, upstream downloads and builds stay local.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import argparse
from collections import Counter
import shutil
from common import *

ap=argparse.ArgumentParser()
ap.add_argument('--prototype',required=True);ap.add_argument('--upstream',required=True)
ap.add_argument('--experiments',required=True);ap.add_argument('--streams',required=True);ap.add_argument('--edges',required=True)
args=ap.parse_args();dest=ROOT/'evidence';dest.mkdir(exist_ok=True)
datasets={}
for key in ('prototype','upstream','experiments','streams','edges'):
    p=Path(getattr(args,key));d=json.loads(p.read_text(encoding='utf-8'));datasets[key]=d
    if key!='upstream':assert d['prototype_sha256']==sha(EXE), (key,'binary changed')
    save(dest/(key+'.json'),d)
assert len(datasets['prototype']['cases'])==110
assert len(datasets['upstream']['cases'])==220
assert len(datasets['experiments']['cases'])==33
assert len(datasets['streams']['cases'])==5
assert len(datasets['edges']['cases'])==2
build=json.loads((ROOT/'build/core/manifest.json').read_text(encoding='utf-8'))
for name,row in build.items():
    assert hashlib.sha256((REPO/'src'/name).read_text(encoding='utf-8').encode()).hexdigest()==row['source_lf_sha256']
    shutil.copyfile(ROOT/'build/core'/(name+'.diff'),dest/('core-'+name+'.diff'))
save(dest/'core-adapter.json',build)
save(dest/'trace-skeskinen.json',json.loads((ROOT/'test-data/trace-skeskinen/results.json').read_text(encoding='utf-8')))

manifest={'prototype_exe_sha256':sha(EXE),'ffmpeg_exe_sha256':sha(FF),
          'ffmpeg_version':run([FF,'-version']).stdout.decode(),
          'ffmpeg_sdk_package':{
              'url':'https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-9.0.2-full_build-shared.7z',
              'published_sha256':'4d2060a8b34a940aa47d785142055bb92a63053781e55f2ace4546edd519a8f5'},
          'git_head_at_archive':run(['git','rev-parse','HEAD'],cwd=REPO).stdout.decode().strip(),
          'files':{},'upstream':[]}
files=list((ROOT/'src').glob('*'))+list((ROOT/'tests').glob('*.py'))+[ROOT/'prepare_core.py',ROOT/'CMakeLists.txt']
files += [REPO/'src'/n for n in ('media.hpp','video.cpp','av1.cpp','cli.cpp','platform.cpp')]
files += [REPO/p for p in ('research/h264/tests/integration.py','research/h265/tests/common.py',
                           'research/av1/tests/fixtures.py','research/av1/tests/common.py','research/av1/tests/sequence.py',
                           'tests/prepare_fixtures.py')]
for p in files:
    raw=p.read_bytes();manifest['files'][p.relative_to(REPO).as_posix()]={
        'sha256':hashlib.sha256(raw).hexdigest(),'lf_sha256':hashlib.sha256(raw.replace(b'\r\n',b'\n')).hexdigest()}
for folder in ('ffmpeg-n9.0.2-matroska','mkvtoolnix-103.0-reference'):
    manifest['upstream']+=json.loads((REPO/'sources'/folder/'manifest.json').read_text(encoding='utf-8'))
for project,commit,paths in [
    ('DeepRegular-SmartCut-v0.8.37','d670099e9df622b9b9120fc46411e6154985b269',
     ['rust/crates/core/src/cut.rs','rust/crates/core/src/input.rs','rust/crates/core/src/sound.rs']),
    ('skeskinen-smartcut','73821ea01c29a4dca81a8865ef7d348d04bb0810',
     ['smartcut/video_cutter.py','smartcut/media_container.py','smartcut/smart_cut.py'])]:
    for name in paths:
        p=REPO/'sources'/project/name
        manifest['upstream'].append(dict(project=project,commit=commit,path=name,sha256=sha(p)))
save(dest/'manifest.json',manifest)
summary={'matrix':{},'experiments':{},'total_matrix_attempts':330,'extra_cases':40}
for project in ('prototype','deepregular','skeskinen'):
    rows=[x for d in (datasets['prototype'],datasets['upstream']) for x in d['cases'] if x['project']==project]
    summary['matrix'][project]={'cases':len(rows),'passed':sum(x['passed'] for x in rows),
        'issues':dict(Counter(i for x in rows for i in x['issues'])),'codecs':{}}
    for codec in ('h264','hevc','av1'):
        subset=[x for x in rows if x['fixture'].startswith(codec+'-')]
        summary['matrix'][project]['codecs'][codec]={'cases':len(subset),'passed':sum(x['passed'] for x in subset),
            'strict_decode_and_count':sum(x.get('decoded_frames')==x.get('expected_frames') and x['exit_code']==0 and 'strict_decode' not in x['issues'] for x in subset),
            'max_pts_error_ms':max(x.get('max_pts_error_ms',0) for x in subset)}
for key in ('experiments','streams','edges'):
    rows=datasets[key]['cases'];summary['experiments'][key]={'cases':len(rows),'passed':sum(x['passed'] for x in rows),
        'not_passed':[x['name'] for x in rows if not x['passed']]}
save(dest/'summary.json',summary)
print(json.dumps(summary,indent=2,ensure_ascii=False))
