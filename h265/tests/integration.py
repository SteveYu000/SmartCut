"""End-to-end HEVC/MP4 tests; run after building smart_render.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import argparse
import time
import traceback
from common import *

CUTS={'partial':[(13,227)], 'cra_leading':[(22,74)], 'multi':[(13,81),(100,227)],
      'full':[(0,N)], 'one':[(23,24)], 'cra_exact':[(24,213)],
      'tail':[(213,N)], 'tiny_disjoint':[(1,3),(22,25),(71,73)],
      'adjacent':[(0,21),(21,69)], 'before_cra':[(0,23)]}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--exe',type=Path,default=ROOT/'build/Release/smart_render.exe')
    ap.add_argument('--fixtures',default=','.join(SPECS))
    ap.add_argument('--cuts',default=','.join(CUTS))
    ap.add_argument('--reuse-fixtures',type=Path)
    ap.add_argument('--modes',default='hev1,stsd')
    ap.add_argument('--access',default='cra')
    ap.add_argument('--lossless',action='store_true')
    ap.add_argument('--no-bla',action='store_true')
    a=ap.parse_args(); run=ROOT/'test-output'/time.strftime('integration-%Y%m%d-%H%M%S'); run.mkdir(parents=True)
    results={'run':str(run),'cases':[],'fixtures':{},'exe_sha256':hashlib.sha256(a.exe.read_bytes()).hexdigest()}
    for name in a.fixtures.split(','):
        path=(a.reuse_fixtures.resolve()/('source-'+name+'.mp4')) if a.reuse_fixtures else fixture(run,name)
        src=source_data(path)
        results['fixtures'][name]={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'profile':src[0]['profile'], 'types':sorted(set(t for x in src[4] for t in x['types']))}
        for label in a.cuts.split(','):
            ranges=CUTS[label]
            for mode in a.modes.split(','):
                out=run/f'{name}-{label}-{mode}.mp4'
                cmd=[a.exe.resolve(),'-i',path,'-o',out,'--ps-mode',mode,'--access',a.access]
                if a.lossless: cmd+=['--lossless']
                if a.no_bla: cmd+=['--experimental-no-bla']
                for x,y in ranges: cmd+=['--keep',f'{x}:{y}']
                row={'fixture':name,'cut':label,'ranges':ranges,'mode':mode,'access':a.access,'lossless':a.lossless,'bla':not a.no_bla,'command':[str(x) for x in cmd]}
                try:
                    r=process(cmd,ok=False); out.with_suffix('.log').write_bytes(r.stderr)
                    row['exit_code']=r.returncode
                    if r.returncode: row.update(passed=False,issues=['process_exit'],error=r.stderr.decode(errors='replace'))
                    else:
                        plan=json.loads(r.stdout); out.with_suffix('.json').write_bytes(r.stdout)
                        row.update(verify(src,out,ranges,True)); row['plan']=plan
                        if row['normalized_vcl']<plan['copied_frames']: row['issues'].append('copy_vcl_changed')
                        if a.lossless and row['pixel_identical']!=row['expected_frames']: row['issues'].append('not_lossless')
                        os_,_=probe(out)
                        for key in ('sample_aspect_ratio','color_range','color_space','color_transfer','color_primaries','pix_fmt'):
                            if key in src[0] and os_.get(key)!=src[0][key]: row['issues'].append('metadata_'+key)
                        row['sample_descriptions']=len(configs(out))
                        row['passed']=not row['issues']
                except Exception as e:
                    row.update(passed=False,issues=['exception'],error=str(e)); traceback.print_exc()
                results['cases'].append(row)
                (run/'results.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
                print(name,label,mode,row['passed'],row.get('issues'),row.get('normalized_vcl'),flush=True)
    print(run,flush=True)
    return 0 if all(x['passed'] for x in results['cases']) else 1

if __name__=='__main__': raise SystemExit(main())
