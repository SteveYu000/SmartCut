"""Revalidate archived outputs and freeze portable, media-free evidence."""
import argparse
from collections import Counter
from common import *

def portable(value):
    if isinstance(value,dict): return {k:portable(v) for k,v in value.items()}
    if isinstance(value,list): return [portable(v) for v in value]
    if isinstance(value,str): return value.replace(str(REPO),'${REPO}').replace(str(REPO).replace('\\','/'),'${REPO}')
    return value

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--runs',type=Path,nargs='+',required=True); ap.add_argument('--output',required=True)
    args=ap.parse_args(); cache={}; results={'runs':[],'cases':[]}
    manifest=json.loads((ROOT/'evidence/fixtures.json').read_text(encoding='utf-8'))['fixtures']
    for path in args.runs:
        old=json.loads((path/'results.json').read_text(encoding='utf-8')); results['runs'].append({k:v for k,v in old.items() if k not in ('cases','fixtures')})
        for row in old['cases']:
            row=dict(row); name=row['fixture']; output=ROOT/row['output']; row['validation_errors']=[]
            if name not in cache: cache[name]=source_data(ROOT/manifest[name]['path'])
            src=cache[name]; assert src['sha256']==manifest[name]['sha256']==row['input_sha256']
            if row.get('exit_code')==0 and output.exists():
                try:
                    row.update(verify(src,output,row['ranges']))
                    if 'plan' in row and row['plan']['copied_frames']!=row['confirmed_copy_frames']:
                        row['passed']=False; row['issues'].append('copy_plan')
                except Exception as e: row.update(passed=False,issues=['strict_validation_error'],validation_errors=[str(e)])
            log=output.with_suffix('.log')
            if log.exists() and log.stat().st_size:
                relative=Path('evidence/logs')/(path.name+'-'+log.name)
                target=ROOT/relative; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(log.read_bytes())
                row['log']=relative.as_posix(); row['log_sha256']=sha(target)
            results['cases'].append(row)
    results['summary']={'total':len(results['cases']),'passed':sum(r['passed'] for r in results['cases']),
        'issues':dict(Counter(x for r in results['cases'] for x in r.get('issues',[])))}
    save(ROOT/'evidence'/args.output,portable(results)); print(args.output,results['summary'],flush=True)

if __name__=='__main__': main()
