"""Audit saved outputs, classify empty products, and cross-check the decoders.
SPDX-License-Identifier: GPL-3.0-or-later
"""
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import numpy as np
import run_suite as s

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):
            h.update(block)
    return h.hexdigest()

def main():
    path=s.ROOT/'results.json'
    result=json.loads(path.read_text(encoding='utf-8'))
    for row in result['cases']:
        if row['issues']!=['harness_or_inspection_error'] or not row.get('output'):
            continue
        output=Path(row['output'])
        p=s.run([s.APPLICATION/'resources/ffprobe.exe','-v','error','-show_streams','-show_format','-of','json',output])
        probe=json.loads(p.stdout)
        (Path(row['case_directory'])/'empty-output-ffprobe.json').write_bytes(p.stdout)
        if not probe['streams']:
            row.update(issues=['missing_video_track','frame_count'],error='Official app returned an MP4 with no video track',decoded_frames=0,samples=0)
            row['inspection_note']='Initial inspection raised StopIteration; bundled ffprobe independently confirms an empty container. This is a product-output failure.'
    for row in result['cases']:
        row.setdefault('expected_frames',sum(b-a for a,b in row['ranges']))
        if 'missing_video_track' in row['issues']:
            row.update(exact_vcl=0,normalized_vcl=0,pixel_identical=0,baseline_copied_frames=0,copy_efficiency_passed=False)
        (Path(row['case_directory'])/'result.json').write_text(json.dumps(row,indent=2,ensure_ascii=False),encoding='utf-8')
    result['summary']={}
    for codec in ('h264','h265'):
        rows=[r for r in result['cases'] if r['codec']==codec]
        result['summary'][codec]={'total':len(rows),'app_reported_output':sum(bool(r.get('export_event',{}).get('paths')) for r in rows),
                                  'passed':sum(r['passed'] for r in rows),'correctness_passed':sum(r.get('correctness_passed',False) for r in rows),
                                  'issues':dict(Counter(issue for r in rows for issue in r['issues']))}
    path.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    assert len(result['cases'])==240
    assert not any('harness_or_inspection_error' in r['issues'] for r in result['cases'])
    source_ok=all(sha(info['path'])==info['sha256'] for info in result['fixtures'].values())
    saved=[r for r in result['cases'] if r.get('output')]
    output_ok=all(sha(r['output'])==r['output_sha256'] for r in saved)
    input_copies_ok=True
    for row in result['cases']:
        folder=Path(row['case_directory'])
        project=json.loads((folder/'project.llc').read_text(encoding='utf-8'))
        copy=folder/project['mediaFileName']
        input_copies_ok &= sha(copy)==result['fixtures'][row['codec']+'/'+row['fixture']]['sha256']
    assert source_ok and output_ok and input_copies_ok
    by_fixture=[]
    for key in result['fixtures']:
        codec,name=key.split('/')
        rows=[r for r in result['cases'] if r['codec']==codec and r['fixture']==name]
        by_fixture.append({'codec':codec,'fixture':name,'total':len(rows),'correctness_passed':sum(r.get('correctness_passed',False) for r in rows),'passed':sum(r['passed'] for r in rows)})
    cross=[]
    for ident in ('h264/closed_b3_id0/full','h264/closed_b3_id0/both_edges','h265/open/partial'):
        row=next(r for r in result['cases'] if r['id']==ident)
        stream,_,_=s.probe(row['output']); pinned,p=s.decode(row['output'],stream)
        original=s.FFMPEG
        try:
            s.FFMPEG=s.APPLICATION/'resources/ffmpeg.exe'
            bundled,b=s.decode(row['output'],stream)
        finally:
            s.FFMPEG=original
        detail={'case':ident,'pinned_frames':len(pinned),'bundled_frames':len(bundled),
                      'pixels_equal':bool(np.array_equal(pinned,bundled)),
                      'pinned_decode_errors':bool(p.stderr.strip() or p.returncode),
                      'bundled_decode_errors':bool(b.stderr.strip() or b.returncode)}
        if ident=='h264/closed_b3_id0/both_edges':
            ids=[]
            for frame in pinned:
                lum=frame[:192*112].reshape(112,192)
                ids.append(sum(int(lum[6:14,8+bit*16:14+bit*16].mean()>127.5)<<bit for bit in range(10)))
            detail.update(first_frame_ids=ids[:16],last_frame_ids=ids[-8:],
                          missing_requested_ids=sorted(set(range(13,227))-set(ids)),
                          unexpected_frame_ids=sorted(set(ids)-set(range(13,227))))
        cross.append(detail)
    # Confirm CUDA and the comparison command work on the original passing output.
    good=s.REPO/'h265/test-data/experiments/gpu-main10-hev1.mp4'
    stream,_,_=s.probe(good); soft,_=s.decode(good,stream)
    hw=s.run([s.FFMPEG,'-v','error','-xerror','-err_detect','explode','-hwaccel','cuda','-i',good,
              '-map','0:v:0','-vf','setpts=N/25/TB','-enc_time_base','1/25','-fps_mode','passthrough',
              '-pix_fmt','yuv420p10le','-f','rawvideo','-'],strict=False)
    data=np.frombuffer(hw.stdout,dtype=np.uint16)
    n=int(stream['width'])*int(stream['height'])*3//2
    gpu=data.reshape((-1,n)) if len(data)%n==0 else None
    control={'input':str(good),'passed':hw.returncode==0 and not hw.stderr.strip() and gpu is not None and bool(np.array_equal(gpu,soft)),
             'software_frames':len(soft),'hardware_frames':len(gpu) if gpu is not None else None,
             'log':hw.stderr.decode(errors='replace')[-1800:]}
    manifest={
        'dates_beijing':'2026-10-28 to 2026-10-29',
        'application':'LosslessCut 3.70.2 official Windows x64 portable',
        'application_source_commit':'2610284b6c9a68db124a7a2ef436464ce41ea1d2',
        'official_asset':'https://github.com/mifi/lossless-cut/releases/download/v3.70.2/LosslessCut-win-x64.7z',
        'expected_official_asset_sha256':'1740c5f17b952c04cf1b1612cf32f56c68a88105c045199526c7ea9678d309b0',
        # Archive was verified during the original experiment, then removed
        # as redundant during workspace cleanup. Keep the recorded evidence.
        'download_sha256':json.loads((s.ROOT/'audit.json').read_text(encoding='utf-8'))['download_sha256'],
        'download_archive_retained':False,
        'app_exe_sha256':sha(s.APPLICATION/'LosslessCut.exe'),
        'app_asar_sha256':sha(s.APPLICATION/'resources/app.asar'),
        'bundled_ffmpeg':result['bundled_ffmpeg'],'oracle_ffmpeg':result['oracle_ffmpeg'],
        'source_commit':'c1a5d14667b3d82e47a970c93c5efff1e25f9ae1',
        'input_fixtures_byte_identical':source_ok,'case_input_copies_byte_identical':bool(input_copies_ok),'saved_output_hashes_match':output_ok,
        'source_fixture_count':len(result['fixtures']),'saved_main_outputs':len(saved),
        'summary':result['summary'],'by_fixture':by_fixture,'decoder_crosscheck':cross,'cuda_positive_control':control,
        'quality_flags_without_frame_identity_failure':sum('quality_below_30dB' in r['issues'] and 'frame_identity' not in r['issues'] for r in result['cases']),
        'notes':['No LosslessCut or bundled FFmpeg code was changed.',
                 'Only distinct media/range combinations are counted: AVC avc3/stsd and HEVC hev1/stsd mode variants are not duplicated for the native app.',
                 'Correctness is independent of copy efficiency; passed additionally requires at least the baseline positional VCL copy count.',
                 'Protocol setup failures are retained in results.harness_attempts, excluded from the 240 product cases.',
                 'Synthetic rawvideo output timing is used only during pixel extraction; real packet/frame PTS is independently checked.',
                 'HTTP event bodies remain pending because the official await-event route aborts on IncomingMessage.close. This does not alter export logic.']}
    assert manifest['download_sha256']==manifest['expected_official_asset_sha256']
    (s.ROOT/'audit.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding='utf-8')
    with (s.ROOT/'matrix.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.writer(f); writer.writerow(['id','app_output','correctness_passed','copy_efficiency_passed','decoded_frames','expected_frames','positional_vcl_copies','baseline_copies','issues'])
        for r in result['cases']:
            writer.writerow([r['id'],bool(r.get('export_event',{}).get('paths')),r.get('correctness_passed',False),r.get('copy_efficiency_passed',False),r.get('decoded_frames'),r.get('expected_frames',sum(b-a for a,b in r['ranges'])),r.get('normalized_vcl'),r.get('baseline_copied_frames'),';'.join(r['issues'])])
    print(json.dumps({'summary':manifest['summary'],'crosscheck':cross,'cuda_control':control,'quality_flags_without_identity_failure':manifest['quality_flags_without_frame_identity_failure']},indent=2))

if __name__=='__main__':
    try:
        main()
    finally:
        s.POOL.shutdown(wait=True,cancel_futures=True)
