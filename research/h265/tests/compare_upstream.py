"""Run the actual unmodified Python upstreams, then our independent oracle.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import argparse
import os
import sys
import time
import traceback
from common import *

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--sources',type=Path,required=True)
    ap.add_argument('--python-deps',type=Path,default=ROOT/'deps/python')
    ap.add_argument('--only',choices=['skeskinen','deepregular_python'])
    ap.add_argument('--reuse-fixtures',type=Path)
    ap.add_argument('--long',action='store_true',help='POC-wrap pure-copy splice comparison')
    a=ap.parse_args()
    run=ROOT/'test-output'/time.strftime('upstream-%Y%m%d-%H%M%S'); run.mkdir(parents=True)
    scratch=ROOT/'deps/upstream-temp'; scratch.mkdir(parents=True,exist_ok=True)
    results={'run':str(run),'cases':[],'fixture_sha256':{},'ffmpeg_version':process([FFMPEG,'-version']).stdout.decode()}
    cuts={'partial':[(13,227)], 'cra_leading':[(22,74)], 'multi':[(13,81),(100,227)], 'full':[(0,N)]}
    if a.long: cuts={'copy_gap':[(24,93),(312,429)]}
    for name in (('open',) if a.long else ('closed','open','main10','vfr')):
        path=(a.reuse_fixtures.resolve()/('source-'+name+'.mp4')) if a.reuse_fixtures else fixture(run,name,800 if a.long else N)
        src=source_data(path)
        results['fixture_sha256'][name]=hashlib.sha256(path.read_bytes()).hexdigest()
        print('FIXTURE',name,flush=True)
        for upstream,dirname in [('skeskinen','skeskinen-smartcut'),('deepregular_python','DeepRegular-SmartCut')]:
            if a.only and upstream!=a.only: continue
            for label,ranges in cuts.items():
                out=run/f'{upstream}-{name}-{label}.mp4'
                cmd=[sys.executable,'-m','smartcut',str(path)]
                if upstream=='skeskinen':
                    cmd += [str(out),'--frames','--keep',','.join(str(v) for x,y in ranges for v in (x, y-1 if y<len(src[2]) else -1))]
                else:
                    cmd += ['-o',str(out),'--video-encoder','libx265']
                    tb=Fraction(src[0]['time_base']); t=src[3]
                    for x,y in ranges: cmd += ['--keep',f'{float((t[x]-t[0])*tb):.9f}-{float((t[y]-t[0])*tb):.9f}']
                env=dict(os.environ,PATH=str(SDK/'bin')+os.pathsep+os.environ['PATH'],PYTHONPATH=str(a.python_deps),PYTHONIOENCODING='utf-8',TMP=str(scratch),TEMP=str(scratch))
                entry={'upstream':upstream,'fixture':name,'cut':label,'ranges':ranges,'command':cmd,'cwd':str(a.sources/dirname)}
                start=time.perf_counter()
                try:
                    r=process(cmd,ok=False,cwd=a.sources/dirname,env=env)
                    entry['exit_code']=r.returncode
                    out.with_suffix('.log').write_bytes(r.stdout+b'\nSTDERR\n'+r.stderr)
                    if r.returncode: entry.update(passed=False,issues=['process_exit'])
                    else: entry.update(verify(src,out,ranges))
                except Exception as e:
                    entry.update(passed=False,issues=['exception'],error=str(e)); traceback.print_exc()
                entry['seconds']=time.perf_counter()-start
                results['cases'].append(entry)
                (run/'results.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
                print(upstream,name,label,entry['passed'],entry.get('issues'),entry.get('exact_vcl'),flush=True)
    print(run,flush=True)

if __name__=='__main__': main()
