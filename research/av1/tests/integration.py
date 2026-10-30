"""Run native research CLI and independently verify source-relative outputs."""
import argparse
import time
from common import *
from upstream import CUTS

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--names',default='aom,svt,rav1e,main10,vfr,ntsc,offset,fragmented,color_sar,tiles,nvenc')
    ap.add_argument('--cuts',default=','.join(CUTS)); ap.add_argument('--encoder',default='libaom-av1')
    ap.add_argument('--extra',nargs='*',default=[]); args=ap.parse_args()
    exe=ROOT/'build/Release/av1_smart_render.exe'; folder=ROOT/'test-data'/('matrix-'+time.strftime('%Y%m%d-%H%M%S')); folder.mkdir(parents=True)
    results={'exe_sha256':sha(exe),'encoder':args.encoder,'cases':[]}
    manifest=json.loads((ROOT/'evidence/fixtures.json').read_text(encoding='utf-8'))['fixtures']
    for name in args.names.split(','):
        path=ROOT/'test-data/fixtures'/('source-'+name+'.mp4'); source=source_data(path)
        assert source['sha256']==manifest[name]['sha256'], 'Fixture identity changed'
        for label in args.cuts.split(','):
            ranges=CUTS[label]; out=folder/f'{name}-{label}.mp4'; report=out.with_suffix('.json')
            command=[exe,'-i',path,'-o',out,'--encoder',args.encoder,'--report',report,*args.extra]
            for a,b in ranges: command+=['--keep',f'{a}:{b}']
            row={'fixture':name,'cut':label,'ranges':ranges,'input_sha256':source['sha256'],'command':list(map(str,command)),
                 'output':out.relative_to(ROOT).as_posix()}
            started=time.perf_counter()
            try:
                p=run(command,check=False); out.with_suffix('.log').write_bytes(p.stderr); row['exit_code']=p.returncode
                row.update(verify(source,out,ranges) if p.returncode==0 else {'passed':False,'issues':['process_exit'],'stderr':p.stderr.decode(errors='replace')[-2000:]})
                if p.returncode==0:
                    row['plan']=json.loads(report.read_text(encoding='utf-8'))
                    if row['plan']['copied_frames']!=row['confirmed_copy_frames']:
                        row['issues'].append('copy_plan'); row['passed']=False
            except Exception as e: row.update(passed=False,issues=['exception'],error=str(e))
            row['seconds']=time.perf_counter()-started; results['cases'].append(row); save(folder/'results.json',results)
            print(name,label,row['passed'],row.get('issues'),row.get('confirmed_copy_frames'),row.get('sample_descriptions'),flush=True)
    print(folder,flush=True)

if __name__=='__main__': main()
