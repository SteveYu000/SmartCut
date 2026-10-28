"""Summarize unchanged test cases before and after the isolated VFR repair.
SPDX-License-Identifier: GPL-3.0-or-later
"""
from collections import Counter
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parent
BASE=ROOT.parent/'h264-pipeline-comparison/test-results-local/pipeline.trx'
CURRENT=ROOT/'test-results/pipeline-vfr-repaired.trx'
NS={'t':'http://microsoft.com/schemas/VisualStudio/TeamTest/2010'}

def read(path):
    xml=ET.parse(path)
    rows=[{'name':r.attrib['testName'],'outcome':r.attrib['outcome'],
           'message':r.findtext('t:Output/t:ErrorInfo/t:Message',default='',namespaces=NS)}
          for r in xml.findall('.//t:UnitTestResult',NS)]
    groups={}
    for row in rows:
        name=row['name']
        group=('video' if 'PipelineMatchesItsOwnPlanAndTheEngineCounts' in name else
               'audio' if 'AudioTrackIsTrimmedToTheSameWindowsAsTheVideo' in name else
               'logic' if '.PlannerTests.' in name else 'negative')
        groups.setdefault(group,Counter())[row['outcome']]+=1
    return {'path':str(path),'counts':dict(Counter(r['outcome'] for r in rows)),
            'groups':{k:dict(v) for k,v in groups.items()},
            'failures':[r for r in rows if r['outcome']!='Passed']}

before=read(BASE);after=read(CURRENT)
old_fail={r['name'] for r in before['failures']};new_fail={r['name'] for r in after['failures']}
summary={'before':before,'after':after,'fixed':sorted(old_fail-new_fail),'new_failures':sorted(new_fail-old_fail)}
(ROOT/'regression-summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps({key:summary[key] for key in ('fixed','new_failures')},indent=2,ensure_ascii=False))
print('Before:',before['groups'])
print('After:',after['groups'])
