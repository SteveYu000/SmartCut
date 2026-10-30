"""Run native MKV prototype and unmodified upstream CLIs on identical inputs.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import argparse
import time
from common import *

CUTS={'partial':[(13,227)],'multi':[(13,81),(100,227)],'aligned':[(24,216)],
      'one':[(23,24)],'tail':[(213,240)]}
ENCODERS={'h264':['-c:v','libx264','-preset','medium','-crf','18'],
          'hevc':['-c:v','libx265','-preset','medium','-crf','18','-x265-params','log-level=error:pools=none'],
          'av1':['-c:v','libaom-av1','-cpu-used','6','-crf','18','-threads','4']}


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--projects',default='prototype,deepregular,skeskinen')
    ap.add_argument('--names'); ap.add_argument('--cuts',default=','.join(CUTS))
    ap.add_argument('--av1-policy',default='experimental',choices=['reject','full','experimental'])
    ap.add_argument('--seek',action='store_true'); args=ap.parse_args()
    fixtures=json.loads((ROOT/'evidence/fixtures.json').read_text())['fixtures']
    names=args.names.split(',') if args.names else list(fixtures)
    folder=ROOT/'test-data'/('matrix-'+time.strftime('%Y%m%d-%H%M%S'));folder.mkdir(parents=True)
    deep=REPO/'sources/DeepRegular-SmartCut-0.8.37-win64/smartcut/smartcut-cli.exe'
    temp=ROOT/'local/temp';temp.mkdir(parents=True,exist_ok=True)
    result={'ffmpeg':run([FF,'-version']).stdout.decode().splitlines()[0],
            'prototype_sha256':sha(EXE),'deepregular_sha256':sha(deep),
            'deepregular_commit':'d670099e9df622b9b9120fc46411e6154985b269',
            'skeskinen_commit':'73821ea01c29a4dca81a8865ef7d348d04bb0810',
            'av1_policy':args.av1_policy,'cases':[]}
    for name in names:
        spec=fixtures[name]; path=ROOT/spec['path']; assert sha(path)==spec['sha256']
        src=source(path); codec=spec['codec']
        for project in args.projects.split(','):
            for label in args.cuts.split(','):
                ranges=CUTS[label]; out=folder/f'{project}-{name}-{label}.mkv'; plan=out.with_suffix('.plan.json')
                if project=='prototype':
                    cmd=[EXE,'--av1-policy',args.av1_policy,'-v','error','-y','-i',path,*ENCODERS[codec],'--report',plan]
                    for a,b in ranges:cmd+=['--keep-frames',f'{a}:{b}']
                    cmd+=[out]; cwd=REPO
                elif project=='deepregular':
                    cmd=[deep,path,'-o',out,'--no-chapters','--index','auto']
                    for a,b in ranges:cmd+=['--keep',f"{float(src['times'][a]-src['times'][0]):.12f}-{float(src['times'][b]-src['times'][0]):.12f}"]
                    cwd=REPO
                else:
                    cmd=[sys.executable,'-m','smartcut',path,out,'--frames','--keep',
                         ','.join(str(v) for a,b in ranges for v in (a,b-1 if b<240 else -1))]
                    cwd=REPO/'sources/skeskinen-smartcut'
                env=dict(os.environ,PYTHONPATH=str(REPO/'research/h265/deps/python'),PYTHONIOENCODING='utf-8',TMP=str(temp),TEMP=str(temp))
                row={'project':project,'fixture':name,'cut':label,'ranges':ranges,
                     'input_sha256':src['sha256'],'command':list(map(str,cmd)),'cwd':str(cwd),'output':out.relative_to(ROOT).as_posix()}
                start=time.perf_counter()
                try:
                    p=run(cmd,check=False,cwd=cwd,env=env); row['exit_code']=p.returncode
                    out.with_suffix('.log').write_bytes(p.stdout+b'\nSTDERR\n'+p.stderr)
                    copy=None
                    if plan.exists():
                        row['plan']=json.loads(plan.read_text()); copy=set()
                        for u in row['plan']['units']:
                            if u['copy']: copy.update(range(u['begin'],u['end']))
                    row.update(verify(src,out,ranges,copy_indices=copy,seek=args.seek and label in ('partial','multi')) if p.returncode==0 else
                        dict(passed=False,issues=['process_exit'],error=p.stderr.decode(errors='replace')[-1500:]))
                except Exception as e: row.update(passed=False,issues=['exception'],error=str(e))
                row['seconds']=time.perf_counter()-start; result['cases'].append(row)
                save(folder/'results.json',result)
                print(project,name,label,row['passed'],row['issues'],flush=True)
    save(ROOT/'local/matrix-latest.json',{'path':str(folder/'results.json')})
    print(folder,flush=True)


if __name__=='__main__': main()
