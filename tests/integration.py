"""Production CLI against the archived, independently checked AVC/HEVC matrix.
SPDX-License-Identifier: GPL-3.0-or-later
The research fixtures and numpy are required. Media and binaries stay local.
"""
import argparse
from collections import Counter
import hashlib
from support import load_oracle
import json
from pathlib import Path
import subprocess
import time
import traceback

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--exe',type=Path,default=ROOT/'build/Release/smartcut.exe')
parser.add_argument('--quick',action='store_true')
parser.add_argument('--skip-hardware',action='store_true',help='omit NVENC input fixtures')
parser.add_argument('--codecs',default='h264,h265')
parser.add_argument('--modes',default='inband,stsd')
parser.add_argument('--fixtures',help='optional comma-separated fixture names')
parser.add_argument('--cuts',help='optional comma-separated cut names')
args=parser.parse_args()
oracle=load_oracle()
OUTPUT=ROOT/'research/product-validation'/('matrix-'+time.strftime('%Y%m%d-%H%M%S'))
OUTPUT.mkdir(parents=True)
H264=['closed_b3_id0','closed_b3_id3','baseline','open_gop','mixed_open_idr','high10','multi_slice','ntsc','vfr','offset','fragmented','fullrange_sar','nvenc']
H265=['closed','open','main10','vfr','ntsc','offset','fragmented','multislice','color_sar','radl','nvenc']
results={'executable':str(args.exe),'sha256':hashlib.sha256(args.exe.read_bytes()).hexdigest(),'run':str(OUTPUT),'cases':[]}

def save():
    results['summary']={}
    for codec in ('h264','h265'):
        rows=[r for r in results['cases'] if r['codec']==codec]
        results['summary'][codec]={'total':len(rows),'passed':sum(r['passed'] for r in rows)}
    (OUTPUT/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'research/product-validation/matrix-latest.json').write_text(json.dumps({'run':str(OUTPUT),'summary':results['summary']},indent=2),encoding='utf-8')

try:
    for codec in args.codecs.split(','):
        cuts=oracle.extract_assignment(ROOT/('research/h264/tests/integration.py' if codec=='h264' else 'research/h265/tests/integration.py'),'cuts' if codec=='h264' else 'CUTS')
        fixtures=H264 if codec=='h264' else H265
        if args.quick:fixtures=['closed_b3_id0','vfr','open_gop'] if codec=='h264' else ['open','main10','vfr']
        if args.skip_hardware:fixtures=[name for name in fixtures if name!='nvenc']
        if args.fixtures:fixtures=[name for name in fixtures if name in args.fixtures.split(',')]
        for fixture in fixtures:
            source_root=ROOT/('research/h264/test-data/run-20261027-125923' if codec=='h264' else 'research/h265/test-data/fixtures')
            source=source_root/('source-'+fixture+'.mp4')
            data=oracle.load_source(codec,source)
            for cut,ranges in cuts.items():
                if args.quick and cut not in ('both_edges','multiple','single_frame','partial','multi','one'):continue
                if args.cuts and cut not in args.cuts.split(','):continue
                baseline=ROOT/(f'research/h264/test-data/run-20261027-125923/{fixture}-{cut}-avc3-crf18.json' if codec=='h264' else f'research/h265/test-data/matrix/{fixture}-{cut}-hev1.json')
                expected=json.loads(baseline.read_text(encoding='utf-8-sig'))
                for mode in args.modes.split(','):
                    name=f'{codec}-{fixture}-{cut}-{mode}'
                    output=OUTPUT/(name+'.mp4');report=output.with_suffix('.json')
                    command=[args.exe,'-hide_banner','-loglevel','error','-i',source,'-map','0:v:0?','-an','-sn',
                             '-c:v:0','libx264' if codec=='h264' else 'libx265','-preset:v:0','slower','-crf:v:0','18']
                    if codec=='h265':command+=['-x265-params:v:0','pools=4:frame-threads=2:log-level=error']
                    if mode=='stsd':command+=['-tag:v:0','avc1' if codec=='h264' else 'hvc1']
                    for begin,end in ranges:command+=['--keep-frames',f'{begin}:{end}']
                    command+=['--report',report,output]
                    row={'codec':codec,'fixture':fixture,'cut':cut,'mode':mode,'ranges':ranges,'command':[str(x) for x in command]}
                    try:
                        process=oracle.run(command,strict=False);output.with_suffix('.log').write_bytes(process.stderr)
                        row['exit_code']=process.returncode
                        if process.returncode:row.update(passed=False,issues=['process_exit'],error=process.stderr.decode(errors='replace')[-3000:])
                        else:
                            plan=json.loads(report.read_text(encoding='utf-8-sig'))
                            row.update(oracle.check(codec,data,output,ranges,expected))
                            row['planned_copy_matches_research']=plan['copied_frames']==expected['copied_frames']
                            row['planned_encode_matches_research']=plan['encoded_frames']==expected['encoded_frames']
                            if not row['planned_copy_matches_research'] or not row['planned_encode_matches_research']:
                                row['passed']=False;row['issues'].append('plan_differs_from_research')
                    except Exception:row.update(passed=False,issues=['harness_error'],error=traceback.format_exc())
                    results['cases'].append(row);save()
                    if not row['passed']:print(name,'FAIL',row['issues'],row.get('error','')[-300:],flush=True)
            print(codec,fixture,results['summary'][codec],flush=True)
    print(json.dumps(results['summary'],indent=2),flush=True)
finally:oracle.POOL.shutdown(wait=True,cancel_futures=True)
raise SystemExit(0 if results['cases'] and all(r['passed'] for r in results['cases']) else 1)
