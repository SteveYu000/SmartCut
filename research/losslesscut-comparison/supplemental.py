"""Run the original suites' applicable supplemental media scenarios on the app.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import json
from pathlib import Path
import traceback
import numpy as np
import run_suite as s

ROOT, REPO = s.ROOT, s.REPO

def main():
    hevc_ref=json.loads((REPO/'h265/test-data/experiments/results.json').read_text(encoding='utf-8-sig'))
    matrix=json.loads((REPO/'h265/test-data/matrix/results.json').read_text(encoding='utf-8-sig'))
    def copied(name):
        return next(r['plan']['copied_frames'] for r in hevc_ref['cases'] if r['name']==name)
    open_copy=next(r['plan']['copied_frames'] for r in matrix['cases'] if r['fixture']=='open' and r['cut']=='partial' and r['mode']=='hev1')
    experiments=REPO/'h265/test-data/experiments'
    jobs=[
        ('wrap','h265',experiments/'source-open.mp4',[(233,489),(527,739)],800,copied('wrap-hev1-bla1'),False),
        ('copy-gap','h265',experiments/'source-open.mp4',[(24,93),(312,429)],800,copied('copy-gap-hev1-bla1'),False),
        ('gpu-main10','h265',experiments/'gpu-fixtures/source-main10.mp4',[(13,81),(100,227)],240,copied('gpu-main10-hev1'),False),
        ('中文音轨在前-avc','h264',REPO/'h264/test-data/experiments-20261027-130026/音频在前 输入.mp4',[(13,227)],240,192,True),
        ('中文音轨在前-hevc','h265',experiments/'中文 视频.mp4',[(13,227)],240,open_copy,True),
    ]
    result={'cases':[],'seek':[],'hardware':[]}
    for name,codec,path,ranges,count,baseline,strip in jobs:
        folder=ROOT/'supplemental'/name
        row={'name':name,'codec':codec,'source':str(path),'ranges':ranges,'baseline_copied_frames':baseline}
        try:
            source=s.load_source(codec,path,count)
            row['source_sha256']=source['sha256']
            output,info=s.export(folder,path,source,ranges,'supp-'+name,strip_audio=strip)
            row.update(info)
            if output is None:
                row.update(passed=False,correctness_passed=False,issues=['app_export_error'])
            else:
                row.update(s.check(codec,source,output,ranges,{'copied_frames':baseline}))
        except Exception as exc:
            row.update(passed=False,issues=['exception'],error=str(exc))
            (folder/'exception.txt').write_text(traceback.format_exc(),encoding='utf-8')
        result['cases'].append(row)
        (ROOT/'supplemental.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
        print(name,row.get('passed'),row.get('issues'),flush=True)

    # Same CUDA/software comparison as h265/tests/experiments.py, on the native
    # app's output of the same 320x192 source and same kept frame ranges.
    gpu=next(r for r in result['cases'] if r['name']=='gpu-main10')
    if gpu.get('output'):
        path=Path(gpu['output']); stream,_,_=s.probe(path)
        soft,software=s.decode(path,stream)
        command=[s.FFMPEG,'-v','error','-xerror','-err_detect','explode','-hwaccel','cuda','-i',path,
                 '-map','0:v:0','-vf','setpts=N/25/TB','-enc_time_base','1/25','-fps_mode','passthrough',
                 '-pix_fmt','yuv420p10le','-f','rawvideo','-']
        hw=s.run(command,strict=False)
        data=np.frombuffer(hw.stdout,dtype=np.uint16)
        n=int(stream['width'])*int(stream['height'])*3//2
        pixels=data.reshape((-1,n)) if len(data)%n==0 else None
        equal=pixels is not None and np.array_equal(pixels,soft)
        result['hardware'].append({'input':str(path),'passed':hw.returncode==0 and not hw.stderr.strip() and bool(equal),
                                   'software_frames':len(soft),'hardware_frames':len(pixels) if pixels is not None else None,
                                   'pixel_equal':bool(equal),'exit_code':hw.returncode,'log':hw.stderr.decode(errors='replace')[-2500:],
                                   'note':'Decoder equality is separate from whether the cut itself passed the source-frame oracle.'})

    # All MP4-advertised sync points in one AVC boundary splice and the HEVC
    # Main10 experiment, matching the original random-access test method.
    matrix_rows=json.loads((ROOT/'results.json').read_text(encoding='utf-8'))['cases']
    avc=next(r for r in matrix_rows if r['id']=='h264/closed_b3_id0/both_edges')
    for label,path in [('avc-boundary',Path(avc['output'])),('hevc-main10',Path(gpu['output']))]:
        stream,packets,_=s.probe(path)
        linear,linear_run=s.decode(path,stream)
        display=sorted(range(len(packets)),key=lambda i:int(packets[i]['pts']))
        positions={packet:rank for rank,packet in enumerate(display)}
        tb=s.Fraction(stream['time_base'])
        pix='yuv420p10le' if '10' in stream['pix_fmt'] else 'yuv420p'
        size=int(stream['width'])*int(stream['height'])*3//2
        for i,packet in enumerate(packets):
            if 'K' not in packet['flags']:
                continue
            when=int(packet['pts'])*tb
            command=[s.FFMPEG,'-v','error','-xerror','-err_detect','explode','-ss',f'{float(when):.9f}','-i',path,
                     '-map','0:v:0','-vf','setpts=N/25/TB','-enc_time_base','1/25','-fps_mode','passthrough',
                     '-pix_fmt',pix,'-f','rawvideo','-']
            p=s.run(command,strict=False)
            data=np.frombuffer(p.stdout,dtype=np.uint16 if '10' in pix else np.uint8)
            frames=data.reshape((-1,size)) if len(data)%size==0 else None
            rank=positions[i]
            equal=frames is not None and np.array_equal(frames,linear[rank:])
            result['seek'].append({'case':label,'pts':str(when),'display_rank':rank,'passed':p.returncode==0 and not p.stderr.strip() and bool(equal),
                                   'suffix_pixels_equal':bool(equal),'frames':len(frames) if frames is not None else None,
                                   'exit_code':p.returncode,'log':p.stderr.decode(errors='replace')[-1200:]})
    (ROOT/'supplemental.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    print('seek',sum(r['passed'] for r in result['seek']),'/',len(result['seek']),flush=True)
    print('hardware',result['hardware'],flush=True)

if __name__=='__main__':
    try:
        main()
    finally:
        s.POOL.shutdown(wait=True,cancel_futures=True)
