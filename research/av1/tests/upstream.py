"""Run unmodified official DeepRegular Rust CLI and skeskinen Python CLI."""
import argparse
import sys
import time
from common import *

CUTS={'full':[(0,N)],'partial':[(13,227)],'multi':[(13,81),(100,227)],
      'aligned':[(24,216)],'one':[(23,24)],'tiny':[(23,25)],
      'copy_gap':[(24,72),(120,192)],'tail':[(213,N)]}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--names',default='aom,svt,main10,vfr')
    ap.add_argument('--cuts',default=','.join(CUTS)); ap.add_argument('--only',choices=['deepregular','skeskinen'])
    ap.add_argument('--index',choices=['auto','scan','container'],default='auto')
    args=ap.parse_args(); tag=time.strftime('%Y%m%d-%H%M%S')
    folder=ROOT/'test-data'/('upstream-'+tag); folder.mkdir(parents=True)
    scratch=ROOT/'local/temp'; scratch.mkdir(parents=True,exist_ok=True)
    deep=REPO/'sources/DeepRegular-SmartCut-0.8.37-win64/smartcut/smartcut-cli.exe'
    results={'deepregular_version':run([deep,'--version']).stdout.decode().strip(),'deepregular_exe_sha256':sha(deep),
             'deepregular_commit':'d670099e9df622b9b9120fc46411e6154985b269',
             'skeskinen_commit':'73821ea01c29a4dca81a8865ef7d348d04bb0810','index_mode':args.index,'fixtures':{},'cases':[]}
    for name in args.names.split(','):
        path=ROOT/'test-data/fixtures'/('source-'+name+'.mp4'); source=source_data(path)
        assert source['sha256']==json.loads((ROOT/'evidence/fixtures.json').read_text(encoding='utf-8'))['fixtures'][name]['sha256']
        results['fixtures'][name]={'sha256':source['sha256'],'path':path.relative_to(ROOT).as_posix()}
        for project in ('deepregular','skeskinen'):
            if args.only and project!=args.only: continue
            for label in args.cuts.split(','):
                ranges=CUTS[label]; output=folder/f'{project}-{name}-{label}.mp4'
                if project=='deepregular':
                    command=[deep,path,'-o',output,'--no-chapters','--index',args.index]
                    for a,b in ranges:
                        t=source['times']; tb=Fraction(source['stream']['time_base'])
                        command+=['--keep',f'{float((t[a]-t[0])*tb):.12f}-{float((t[b]-t[0])*tb):.12f}']
                    cwd=REPO
                else:
                    command=[sys.executable,'-m','smartcut',path,output,'--frames','--keep',','.join(str(v) for a,b in ranges for v in (a,b-1 if b<N else -1))]
                    cwd=REPO/'sources/skeskinen-smartcut'
                env=dict(os.environ,PYTHONPATH=str(REPO/'research/h265/deps/python'),PYTHONIOENCODING='utf-8',TMP=str(scratch),TEMP=str(scratch))
                row={'project':project,'fixture':name,'cut':label,'ranges':ranges,'input_sha256':source['sha256'],
                     'command':list(map(str,command)),'cwd':str(cwd)}
                start=time.perf_counter()
                try:
                    p=run(command,check=False,cwd=cwd,env=env,timeout=180)
                    output.with_suffix('.log').write_bytes(p.stdout+b'\nSTDERR\n'+p.stderr)
                    row['exit_code']=p.returncode
                    row.update(verify(source,output,ranges) if p.returncode==0 else {'passed':False,'issues':['process_exit'],'stderr':p.stderr.decode(errors='replace')[-1500:]})
                except Exception as e: row.update(passed=False,issues=['exception'],error=str(e))
                row['seconds']=time.perf_counter()-start; row['output']=output.relative_to(ROOT).as_posix()
                results['cases'].append(row); save(folder/'results.json',results)
                print(project,name,label,row['passed'],row.get('issues'),row.get('exact_packets_at_expected_positions'),row.get('config_sequence_differences'),flush=True)
    print(folder,flush=True)

if __name__=='__main__': main()
