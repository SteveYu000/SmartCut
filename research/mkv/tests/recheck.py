"""Re-audit existing exports without altering their media or rerunning encoders."""
import argparse
from common import *

ap=argparse.ArgumentParser();ap.add_argument('results');args=ap.parse_args()
p=Path(args.results);data=json.loads(p.read_text(encoding='utf-8'));fixtures=json.loads((ROOT/'evidence/fixtures.json').read_text(encoding='utf-8'))['fixtures']
sources={}
for row in data['cases']:
    if row.get('exit_code')!=0:continue
    name=row['fixture']
    if name not in sources:sources[name]=source(ROOT/fixtures[name]['path'])
    copy=None
    if 'plan' in row:
        copy={i for u in row['plan']['units'] if u['copy'] for i in range(u['begin'],u['end'])}
    row.update(verify(sources[name],ROOT/row['output'],row['ranges'],copy_indices=copy,
                      seek=bool(row.get('seeks'))))
    print(row['project'],name,row['cut'],row['issues'],flush=True)
save(p.with_name('rechecked-results.json'),data)
