"""Check real MP4 timing boxes, in addition to ffprobe's derived duration.
SPDX-License-Identifier: GPL-3.0-or-later
"""
from fractions import Fraction
import json
import math
from pathlib import Path
from inspect_boxes import inspect

ROOT=Path(__file__).resolve().parent
results=json.loads((ROOT/'validation.json').read_text(encoding='utf-8'))
audit=[]
for row in results['cases']:
    output=Path(row['output'])
    plan=json.loads(output.with_suffix('.json').read_text(encoding='utf-8-sig'))
    expected=Fraction(plan['duration_ticks']*plan['time_base'][0],plan['time_base'][1])
    boxes=inspect(output)
    mdhd=boxes['moov/trak/mdia/mdhd']
    movie=boxes['moov/mvhd']
    stts=boxes['moov/trak/mdia/minf/stbl/stts']
    tkhd=boxes['moov/trak/tkhd']
    elst=boxes['moov/trak/edts/elst']
    checks={
        'mdhd_exact':Fraction(mdhd['duration'],mdhd['timescale'])==expected,
        'stts_decode_span_exact':Fraction(stts['decode_duration'],mdhd['timescale'])==expected,
        'mvhd_correct_rounding':movie['duration']==math.ceil(expected*movie['timescale']),
        'tkhd_correct_rounding':tkhd['duration']==math.ceil(expected*movie['timescale']),
        'elst_correct_rounding':sum(entry[0] for entry in elst['entries'])==math.ceil(expected*movie['timescale']),
    }
    audit.append({'case':row['case'],'expected_seconds':str(expected),'checks':checks,'passed':all(checks.values()),'boxes':boxes})
    print(row['case'],all(checks.values()),[name for name,value in checks.items() if not value])
(ROOT/'box-audit.json').write_text(json.dumps(audit,indent=2),encoding='utf-8')
print('PASS',sum(row['passed'] for row in audit),'/',len(audit))
