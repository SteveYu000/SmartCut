"""Ablations, AV1 policy, MKVToolNix controls and auxiliary-stream remux checks.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import time
from common import *
from matrix import ENCODERS, CUTS

MKV=REPO/'sources/mkvtoolnix-103.0-win64/mkvtoolnix/mkvmerge.exe'


def main():
    folder=ROOT/'test-data'/('experiments-'+time.strftime('%Y%m%d-%H%M%S'));folder.mkdir(parents=True)
    fixtures=json.loads((ROOT/'evidence/fixtures.json').read_text())['fixtures']
    result={'prototype_sha256':sha(EXE),'ffmpeg':run([FF,'-version']).stdout.decode().splitlines()[0],
            'mkvmerge':run([MKV,'--version']).stdout.decode().strip(),'mkvmerge_sha256':sha(MKV),'cases':[]}
    def record(row):
        result['cases'].append(row);save(folder/'results.json',result)
        print(row['name'],row.get('passed'),row.get('issues',row.get('error','')),flush=True)
    def cut(name,path,ranges,extra=(),expected_rejection=False):
        src=source(path); codec=src['stream']['codec_name'];out=folder/(name+'.mkv');plan=out.with_suffix('.json')
        cmd=[EXE,'-v','error','-y','-i',path,*ENCODERS[codec],*extra,'--report',plan]
        for a,b in ranges:cmd+=['--keep-frames',f'{a}:{b}']
        cmd+=[out];p=run(cmd,check=False);out.with_suffix('.log').write_bytes(p.stdout+b'\n'+p.stderr)
        row=dict(name=name,input_sha256=src['sha256'],command=list(map(str,cmd)),exit_code=p.returncode,
                 output=out.relative_to(ROOT).as_posix())
        if plan.exists():row['plan']=json.loads(plan.read_text())
        if expected_rejection:
            row.update(passed=p.returncode!=0 and not out.exists() and b'one Sequence Header' in p.stderr,
                       expected='reject mixed AV1 sequence headers before publishing')
        elif p.returncode:
            row.update(passed=False,error=p.stderr.decode(errors='replace')[-1800:])
        else:
            copies={i for u in row['plan']['units'] if u['copy'] for i in range(u['begin'],u['end'])}
            row.update(verify(src,out,ranges,copy_indices=copies,seek=True))
        return row,out

    for fixture in ('av1-svt','av1-rav1e'):
        path=ROOT/fixtures[fixture]['path']
        for label in ('partial','multi','tail'):
            for policy in ('reject','full'):
                row,_=cut(f'{fixture}-{label}-{policy}',path,CUTS[label],['--av1-policy',policy],policy=='reject')
                record(row)
    for fixture in ('h264-closed_b3_id0','hevc-closed'):
        path=ROOT/fixtures[fixture]['path']
        for mode in ('all','extradata'):
            row,_=cut(f'{fixture}-headers-{mode}',path,CUTS['partial'],['--header-mode',mode])
            row['ablation']=True;record(row)
        for mode in ('all','seams'):
            row,out=cut(f'{fixture}-seek-headers-{mode}',path,[(0,239)],['--header-mode',mode,'-g','3'])
            row['ablation']=True
            if row['exit_code']==0:
                s=probe(out)['streams'][0]
                try:
                    linear=decode(out,s);jump=decode(out,s,before=['-ss','9.12'])
                    row['tail_key_seek']=bool(len(jump) and np.array_equal(jump[0],linear[228]))
                except Exception as e:row['tail_key_seek']=False;row['tail_key_error']=str(e)
                row['passed']=row['passed'] and row['tail_key_seek']
            record(row)
    for fixture in ('h264-closed_b3_id0','hevc-open','av1-aom'):
        spec=fixtures[fixture];path=ROOT/spec['path'];original=source(path)
        zipped=folder/(fixture+'-zlib.mkv')
        cmd=[MKV,'-o',zipped,'--compression','0:zlib',path]
        p=run(cmd,check=False); zipped.with_suffix('.log').write_bytes(p.stdout+p.stderr)
        if p.returncode not in (0,1):record(dict(name=fixture+'-zlib',passed=False,error=p.stdout.decode(errors='replace')));continue
        zip_src=source(zipped)
        assert np.array_equal(original['frames'],zip_src['frames'])
        row,_=cut(f'{fixture}-compressed-input',zipped,CUTS['partial'])
        row['fixture_command']=list(map(str,cmd));record(row)
        # Same planner with native MP4 input, MKV output: no intermediate remux.
        row,_=cut(f'{fixture}-mp4-to-mkv',REPO/spec['source_path'],CUTS['partial'])
        record(row)
        split=folder/(fixture+'-split.mkv')
        cmd=[MKV,'-o',split,'--split','parts:00:00:00.520-00:00:09.080',path]
        p=run(cmd,check=False); candidates=list(folder.glob(fixture+'-split*.mkv'))
        if not candidates:
            record(dict(name=fixture+'-mkvmerge-split',passed=False,error=p.stdout.decode(errors='replace')));continue
        actual=source(candidates[0]);w,h=int(actual['stream']['width']),int(actual['stream']['height'])
        codes=[]
        for f in actual['frames']:
            codes.append(sum(int(f[:w*h].reshape(h,w)[6:14,8+i*16:14+i*16].mean()>127.5)<<i for i in range(10)))
        record(dict(name=fixture+'-mkvmerge-split',passed=True,control_only=True,command=list(map(str,cmd)),
                    requested_frames=[13,227],observed_first_frame=codes[0],observed_last_frame=codes[-1],
                    frame_count=len(codes),all_pixels_original=all(np.array_equal(f,original['frames'][i]) for f,i in zip(actual['frames'],codes))))
    # Microsecond timestamp-scale is a valid Matroska input; the unmodified
    # FFmpeg Matroska muxer still quantizes the result to milliseconds.
    spec=fixtures['h264-ntsc'];micro=folder/'ntsc-microsecond.mkv'
    cmd=[MKV,'-o',micro,'--timestamp-scale','1000',REPO/spec['source_path']];run(cmd)
    row,_=cut('microsecond-input',micro,CUTS['partial']);row['fixture_command']=list(map(str,cmd));record(row)
    for codec in ('h264','hevc','av1'):
        name={'h264':'h264-vfr','hevc':'hevc-vfr','av1':'av1-vfr'}[codec]
        path=ROOT/fixtures[name]['path'];src=source(path)
        old=next((REPO/'research/h264/deps').glob('ffmpeg-*-win64-gpl-shared-8.1'))/'bin/ffmpeg.exe'
        out=folder/(codec+'-old-ffmpeg-remux.mkv')
        cmd=[old,'-v','error','-y','-copyts','-i',path,'-map','0:v:0','-c','copy','-avoid_negative_ts','disabled',out]
        p=run(cmd,check=False)
        row=dict(name=codec+'-ffmpeg81-vfr-remux',command=list(map(str,cmd)),exit_code=p.returncode,control_only=True)
        row.update(verify(src,out,[(0,240)]) if p.returncode==0 else dict(passed=False,error=p.stderr.decode(errors='replace')))
        record(row)
    save(ROOT/'local/experiments-latest.json',{'path':str(folder/'results.json')})
    print(folder,flush=True)


if __name__=='__main__':main()
