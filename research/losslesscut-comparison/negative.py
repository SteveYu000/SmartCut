"""Observable malformed-input and no-overwrite checks on the official app.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import hashlib
import json
from pathlib import Path
import time
import traceback
import run_suite as s

def main():
    result=[]
    refs={
        'h264':s.REPO/'h264/test-data/run-20261027-125923/source-closed_b3_id0.mp4',
        'h265':s.REPO/'h265/test-data/fixtures/source-open.mp4'}
    cases=[
        ('avc-invalid-nal','h264',s.REPO/'h264/test-data/experiments-20261027-130026/bad-length.mp4'),
        ('hevc-invalid-nal','h265',s.REPO/'h265/test-data/experiments/negative-invalid-nal-length.mp4'),
        ('hevc-invalid-hvcc','h265',s.REPO/'h265/test-data/experiments/negative-invalid-hvcc-array.mp4')]
    for name,codec,path in cases:
        row={'name':name,'input':str(path),'input_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        folder=s.ROOT/'negative'/name
        source=s.load_source(codec,refs[codec])
        source['sha256']=row['input_sha256']
        try:
            output,info=s.export(folder,path,source,[(0,240)],'negative-'+name,event_timeout=8)
            row.update(info)
            row['passed']=output is None
            row['observation']='application_reported_export_error' if output is None else 'application_returned_output_for_malformed_input'
            if output is not None:
                inspect=s.run([s.FFPROBE,'-v','error','-show_streams','-of','json',output],strict=False)
                (folder/'ffprobe.json').write_bytes(inspect.stdout)
                row['output_bytes']=output.stat().st_size
        except Exception as exc:
            # An API/renderer load timeout alone is not evidence of a deliberate
            # validation rejection, so keep it inconclusive rather than a pass.
            row.update(passed=None,observation='no_completed_export_observed',error=str(exc))
            (folder/'exception.txt').write_text(traceback.format_exc(),encoding='utf-8')
        result.append(row)
        (s.ROOT/'negative.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(name,row['passed'],row['observation'],flush=True)
    source=s.load_source('h264',refs['h264'])
    folder=s.ROOT/'negative/existing-output'
    output,info=s.export(folder,refs['h264'],source,[(0,240)],'negative-existing')
    target=Path(info['export_event']['paths'][0])
    original=target.read_bytes()
    sentinel=b'LOSSLESSCUT TEST SENTINEL - DO NOT OVERWRITE'
    try:
        target.write_bytes(sentinel)
        done=s.POOL.submit(s.await_event,'export-complete',8)
        start=s.POOL.submit(s.await_event,'export-start',5)
        time.sleep(0.05)
        s.api('/action/export')
        started=start.result(timeout=7)
        completed=done.result(timeout=10)
        row={'name':'existing-output','passed':target.read_bytes()==sentinel,
             'export_event':completed,'sentinel_unchanged':target.read_bytes()==sentinel}
        result.append(row)
        print('existing-output',row['passed'],flush=True)
    finally:
        target.write_bytes(original)
    (s.ROOT/'negative.json').write_text(json.dumps(result,indent=2),encoding='utf-8')

if __name__=='__main__':
    try:
        main()
    finally:
        s.POOL.shutdown(wait=True,cancel_futures=True)
