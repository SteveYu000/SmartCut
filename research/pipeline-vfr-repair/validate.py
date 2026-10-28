"""Independent frame/VCL/timestamp verification of the isolated pipeline repair.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import difflib
import importlib.util
import json
from pathlib import Path
import subprocess
import time

ROOT=Path(__file__).resolve().parent
REPO=ROOT.parent
SDK=ROOT.parent/'h264/deps/ffmpeg-n8.1.3-24-g92611900bb-win64-gpl-shared-8.1/bin'
EXE=ROOT/'artifacts/bin/SmartCut.Cli/release/SmartCut.Cli.exe'
spec=importlib.util.spec_from_file_location('oracle',ROOT.parent/'losslesscut-comparison/run_suite.py')
oracle=importlib.util.module_from_spec(spec);spec.loader.exec_module(oracle)

def main():
    run=ROOT/('validation-'+time.strftime('%H%M%S'));run.mkdir()
    fixture=REPO/'h264/test-data/run-20261027-125923/source-vfr.mp4'
    source=oracle.load_source('h264',fixture)
    original=oracle.extract_assignment(REPO/'h264/tests/integration.py','cuts')
    jobs=[('vfr-'+name,fixture,source,ranges) for name,ranges in original.items()]
    jobs += [('vfr-extra-'+name,fixture,source,ranges) for name,ranges in {
        'single-long':[(23,24)],'head-ends-long':[(13,24)],'both-ends-long':[(13,228)],
        'single-copy-epoch':[(24,48)],'last-long':[(239,240)],'head-two-long':[(2,3)],
        'disjoint-long':[(2,3),(23,24),(47,48)]}.items()]
    ntsc=REPO/'h264/test-data/run-20261027-125923/source-ntsc.mp4'
    jobs.append(('ntsc-80-single-frame-joins',ntsc,oracle.load_source('h264',ntsc),[(i,i+1) for i in range(1,240,3)]))

    # VFR durations on a 90 kHz grid, deliberately not integer microseconds.
    fine=run/'source-90khz-vfr.mp4'
    expression="settb=1/90000,setpts='floor(N/3)*9010+if(eq(mod(N,3),0),0,if(eq(mod(N,3),1),3001,6005))'"
    oracle.run([SDK/'ffmpeg.exe','-v','error','-i',REPO/'h264/test-data/run-20261027-125923/source-closed_b3_id0.mp4',
                '-vf',expression,'-fps_mode','passthrough','-enc_time_base','1/90000','-c:v','libx264',
                '-preset','medium','-crf','14','-x264-params','keyint=24:min-keyint=24:scenecut=0:bframes=3:b-adapt=0:open-gop=0',
                '-video_track_timescale','90000','-an',fine])
    fine_data=oracle.load_source('h264',fine)
    jobs += [('fine-vfr-both',fine,fine_data,[(13,227)]),
             ('fine-vfr-80-single-frame-joins',fine,fine_data,[(i,i+1) for i in range(1,240,3)])]
    results={'cases':[],'prototype':str(EXE),'run':str(run),'source_test':str(REPO/'h264/tests/integration.py')}
    for name,path,src,ranges in jobs:
        output=run/(name+'.mp4');report=run/(name+'.json')
        command=[EXE,'-i',path,'-o',output,'--report',report,'--ffmpeg-dir',SDK,'--no-audio','--keep-work','--work-dir',run]
        for begin,end in ranges:command+=['--keep',f'{begin}:{end}']
        p=oracle.run(command,strict=False)
        row={'case':name,'ranges':ranges,'source':str(path),'output':str(output),'exit_code':p.returncode}
        if p.returncode:
            row.update(passed=False,issues=['process_exit'],log=p.stderr.decode(errors='replace')[-2500:])
        else:
            plan=json.loads(report.read_text(encoding='utf-8-sig'))
            row.update(oracle.check('h264',src,output,ranges,{'copied_frames':plan['copied_frames']}))
            row['copied_frames']=plan['copied_frames'];row['encoded_frames']=plan['encoded_frames']
        results['cases'].append(row)
        (ROOT/'validation.json').write_text(json.dumps(results,indent=2,ensure_ascii=False),encoding='utf-8')
        print(name,row['passed'],row.get('issues'),row.get('duration_seconds'),row.get('pts_max_error_seconds'),flush=True)
    old=(REPO/'pipeline/src/SmartCut.Core/PipelineRunner.cs').read_text(encoding='utf-8-sig').splitlines(keepends=True)
    new=(ROOT/'prototype/src/SmartCut.Core/PipelineRunner.cs').read_text(encoding='utf-8-sig').splitlines(keepends=True)
    patch=''.join(difflib.unified_diff(old,new,fromfile='a/research/pipeline/src/SmartCut.Core/PipelineRunner.cs',tofile='b/research/pipeline/src/SmartCut.Core/PipelineRunner.cs'))
    (ROOT/'vfr-repair.patch').write_text(patch,encoding='utf-8')
    print('PASS',sum(r['passed'] for r in results['cases']),'/',len(results['cases']),flush=True)

if __name__=='__main__':
    try: main()
    finally: oracle.POOL.shutdown(wait=True,cancel_futures=True)
