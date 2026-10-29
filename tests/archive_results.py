"""Archive completed, matching-build product validation evidence.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import hashlib
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import numpy as np
from support import ROOT, FF, SDK

BASE = ROOT / 'research/product-validation'
OUT = BASE / 'evidence'
EXE = ROOT / 'build/Release/smartcut.exe'
sha = hashlib.sha256(EXE.read_bytes()).hexdigest()
loaded = {}
for name, total in [('matrix', 480), ('streams', 16), ('parameters', 38), ('timestamps', 15)]:
    pointer = json.loads((BASE / (name + '-latest.json')).read_text(encoding='utf-8-sig'))
    path = Path(pointer['run']) / 'results.json'
    result = json.loads(path.read_text(encoding='utf-8-sig'))
    assert len(result['cases']) == total, (name, len(result['cases']), total)
    assert all(row['passed'] for row in result['cases']), name
    assert result.get('sha256', result.get('executable_sha256')) == sha, name + ': executable differs'
    loaded[name] = (path, result)

ctest = ROOT / 'build/Testing/Temporary/LastTest.log'
assert ctest.read_bytes().count(b'Test Passed.') == 2, 'Run CTest before archiving'
fixtures = json.loads((BASE / 'regenerated-fixtures.json').read_text(encoding='utf-8-sig'))
references = {
    'h264': json.loads((ROOT / 'research/h264/docs/evidence/final-integration.json').read_text(encoding='utf-8-sig')),
    'h265': json.loads((ROOT / 'research/h265/test-data/matrix/results.json').read_text(encoding='utf-8-sig')),
}
assert len(fixtures['fixtures']) == 24
for row in fixtures['fixtures']:
    folder = ROOT / ('research/h264/test-data/run-20261027-125923' if row['codec'] == 'h264' else 'research/h265/test-data/fixtures')
    actual = hashlib.sha256((folder / ('source-' + row['name'] + '.mp4')).read_bytes()).hexdigest()
    expected = references[row['codec']]['fixtures'][row['name']]['sha256']
    assert row['sha256'] == actual == expected, row

OUT.mkdir(parents=True, exist_ok=True)
for name, (path, _) in loaded.items():
    shutil.copyfile(path, OUT / (name + '.json'))
shutil.copyfile(ctest, OUT / 'ctest.log')
shutil.copyfile(BASE / 'regenerated-fixtures.json', OUT / 'source-fixtures.json')
checksums = {}
for path in [*sorted((ROOT / 'src').glob('*')), *sorted((ROOT / 'tests').glob('*.py')),
             ROOT / 'CMakeLists.txt', ROOT / 'CMakePresets.json']:
    if path.is_file():
        checksums[str(path.relative_to(ROOT)).replace('\\', '/')] = hashlib.sha256(path.read_bytes()).hexdigest()
(OUT / 'source-checksums.json').write_text(json.dumps(checksums, indent=2), encoding='utf-8')
compiler_files = list((ROOT / 'build/CMakeFiles').glob('*/CMakeCXXCompiler.cmake'))
compiler = compiler_files[0].read_text() if compiler_files else ''
version = re.search(r'set\(CMAKE_CXX_COMPILER_VERSION "([^"]+)', compiler)
ffversion = subprocess.check_output([str(FF), '-version'], text=True, encoding='utf-8').splitlines()[0]
environment = {
    'date': '2026-10-29', 'timezone': 'Asia/Shanghai', 'platform': platform.platform(),
    'python': sys.version, 'numpy': np.__version__, 'sdk': str(SDK), 'ffmpeg': ffversion,
    'compiler_version': version.group(1) if version else None,
    'base_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
    'executable_sha256': sha,
    'summary': {'h264_video': {'passed': 260, 'total': 260}, 'hevc_video': {'passed': 220, 'total': 220},
                'streams': {'passed': 16, 'total': 16}, 'parameters': {'passed': 38, 'total': 38},
                'timestamps': {'passed': 15, 'total': 15}, 'ctest': {'passed': 2, 'total': 2}},
    'total_passed': 551, 'total': 551,
    'historical_fixture_checksums_equal': True,
    'media_and_binaries_tracked': False,
}
(OUT / 'environment.json').write_text(json.dumps(environment, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(environment, ensure_ascii=False, indent=2))
