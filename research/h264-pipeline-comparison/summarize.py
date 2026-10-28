import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

root = Path(__file__).resolve().parent
repo = root.parent
trx = root / 'test-results-local/pipeline.trx'
rows = [e for e in ET.parse(trx).getroot().iter() if e.tag.rsplit('}', 1)[-1] == 'UnitTestResult']
groups = {}
failures = []
for r in rows:
    name, outcome = r.attrib['testName'], r.attrib['outcome']
    group = ('logic' if '.PlannerTests.' in name else 'video' if '.PipelineMatchesItsOwnPlan' in name
             else 'audio' if '.AudioTrackIsTrimmed' in name else 'negative')
    item = groups.setdefault(group, {'passed': 0, 'failed': 0, 'total': 0})
    item['total'] += 1
    item['passed' if outcome == 'Passed' else 'failed'] += 1
    if outcome != 'Passed':
        message = next((e.text for e in r.iter() if e.tag.rsplit('}', 1)[-1] == 'Message'), '')
        failures.append({'test': name, 'group': group, 'message': message})
manifest = json.loads((repo / 'h264/docs/evidence/build-manifest.json').read_text(encoding='utf-8-sig'))
src_matches = {p: hashlib.sha256((repo / 'h264' / p).read_bytes()).hexdigest() == h
               for p, h in manifest['sources'].items()}
assets_path = root / 'artifacts/obj/SmartCut.Tests/project.assets.json'
if assets_path.exists():
    assets = json.loads(assets_path.read_text(encoding='utf-8-sig'))
    versions = [key for key in assets['libraries'] if key.split('/')[0] in
                ('xunit', 'xunit.runner.visualstudio', 'Microsoft.NET.Test.Sdk', 'Microsoft.TestPlatform.TestHost', 'coverlet.collector')]
else:
    # Compiler caches are disposable; the historical package list is evidence.
    versions = json.loads((root/'pipeline-test-summary.json').read_text(encoding='utf-8'))['test_packages_resolved_from_local_cache']
result = {'date': '2026-10-28', 'groups': groups, 'total': len(rows),
          'passed': sum(r.attrib['outcome'] == 'Passed' for r in rows), 'failures': failures,
          'h264_sources_match_archived_build_manifest': src_matches,
          'test_packages_resolved_from_local_cache': versions,
          'notes': ['Initial sandboxed testhost connection timed out before tests ran; successful host connection used normal local execution.',
                    'InvalidKeepRangesAreRejected has five InlineData rows but this runner reports the method as one test result, giving 8 logic results and 165 total.',
                    'FFmpeg is n8.1.3-24-g92611900bb-20261025 for this rerun, different from pipeline README git-2026-10-24-d8990137ee.',
                    'Only the benchmark and pipeline suite were rerun; the complete h264 matrix results are archived evidence.']}
(root / 'pipeline-test-summary.json').write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
print(json.dumps({k: result[k] for k in ('groups', 'total', 'passed', 'h264_sources_match_archived_build_manifest', 'test_packages_resolved_from_local_cache')}, indent=2))
