"""Freeze AV1 integration and matching-binary regression evidence (no media).
SPDX-License-Identifier: GPL-3.0-or-later
"""
import argparse
from datetime import datetime
import platform
import re
import subprocess
import sys
from av1 import PRODUCT, ROOT, SDK, FF, sha, save, json, Path
from archive import portable


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--runs', type=Path, nargs='+', required=True,
                    help='Completed AV1 matrix and feature run directories')
    args = ap.parse_args()
    executable = PRODUCT/'build/Release/smartcut.exe'
    digest = sha(executable)
    base = PRODUCT/'research/product-validation'
    out = base/'evidence/av1'
    out.mkdir(parents=True, exist_ok=True)
    summaries = {}
    for folder in args.runs:
        data = json.loads((folder/'results.json').read_text(encoding='utf-8'))
        assert data['executable_sha256'] == digest, f'Executable changed: {folder}'
        assert data['cases'] and all(row['passed'] for row in data['cases']), folder
        name = data.get('encoder', 'features')
        assert name not in summaries, name
        for row in data['cases']:
            if row.get('output'):
                video = PRODUCT/row['output']
                assert sha(video) == row['output_sha256'], video
                log = video.with_suffix('.log')
                if log.exists() and log.stat().st_size:
                    relative = Path('logs')/(name+'-'+log.name)
                    (out/relative).parent.mkdir(parents=True, exist_ok=True)
                    (out/relative).write_bytes(log.read_bytes())
                    row['archived_log'] = relative.as_posix()
                    row['log_sha256'] = sha(log)
        summaries[name] = {'total': len(data['cases']), 'passed': sum(row['passed'] for row in data['cases'])}
        save(out/(name+'.json'), portable(data))
    assert set(summaries) == {'libaom-av1', 'libsvtav1', 'librav1e', 'av1_nvenc', 'features'}
    assert [summaries[name]['total'] for name in ('libaom-av1', 'libsvtav1', 'librav1e', 'av1_nvenc', 'features')] == [88, 32, 12, 8, 32]
    for name, count in [('matrix', 480), ('streams', 16), ('parameters', 38), ('timestamps', 15)]:
        pointer = json.loads((base/(name+'-latest.json')).read_text(encoding='utf-8'))
        data = json.loads((Path(pointer['run'])/'results.json').read_text(encoding='utf-8'))
        assert data.get('sha256', data.get('executable_sha256')) == digest, name
        assert len(data['cases']) == count and all(row['passed'] for row in data['cases']), name
        save(out/('regression-'+name+'.json'), portable(data))
        summaries['regression-'+name] = {'total': count, 'passed': count}
    ctest = PRODUCT/'build/Testing/Temporary/LastTest.log'
    assert ctest.read_bytes().count(b'Test Passed.') == 2
    (out/'ctest.log').write_bytes(ctest.read_bytes())
    summaries['ctest'] = {'total': 2, 'passed': 2}
    manifest = json.loads((ROOT/'evidence/fixtures.json').read_text(encoding='utf-8'))
    for fixture in manifest['fixtures'].values():
        assert sha(ROOT/fixture['path']) == fixture['sha256']
    save(out/'fixtures.json', manifest)
    regression_fixtures = json.loads((base/'regenerated-fixtures.json').read_text(encoding='utf-8-sig'))
    for fixture in regression_fixtures['fixtures']:
        directory = PRODUCT/('research/h264/test-data/run-20261027-125923' if fixture['codec'] == 'h264'
                             else 'research/h265/test-data/fixtures')
        assert sha(directory/('source-'+fixture['name']+'.mp4')) == fixture['sha256']
    save(out/'regression-fixtures.json', portable(regression_fixtures))
    save(out/'research-baseline.json', json.loads((ROOT/'evidence/audit.json').read_text(encoding='utf-8')))
    checksums = {}
    for path in [*sorted((PRODUCT/'src').glob('*')), *sorted((PRODUCT/'tests').glob('*.py')),
                 PRODUCT/'CMakeLists.txt', PRODUCT/'CMakePresets.json']:
        if path.is_file():
            import hashlib
            checksums[path.relative_to(PRODUCT).as_posix()] = {
                'sha256': sha(path),
                'sha256_lf': hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()}
    save(out/'source-checksums.json', checksums)
    files = list((PRODUCT/'build/CMakeFiles').glob('*/CMakeCXXCompiler.cmake'))
    compiler = files[0].read_text(encoding='utf-8') if files else ''
    version = re.search(r'set\(CMAKE_CXX_COMPILER_VERSION "([^"]+)', compiler)
    environment = {
        'date': datetime.now().strftime('%Y-%m-%d'), 'timezone': 'Asia/Shanghai',
        'platform': platform.platform(), 'python': sys.version,
        'base_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=PRODUCT, text=True).strip(),
        'compiler': version.group(1) if version else None,
        'sdk': str(SDK), 'ffmpeg': subprocess.check_output([str(FF), '-version'], text=True).splitlines()[0],
        'gpu': subprocess.check_output(['nvidia-smi', '--query-gpu=name,driver_version', '--format=csv,noheader'], text=True).strip(),
        'executable_sha256': digest, 'summary': summaries,
        'total': sum(v['total'] for v in summaries.values()),
        'passed': sum(v['passed'] for v in summaries.values()),
        'media_and_binaries_tracked': False,
    }
    save(out/'environment.json', portable(environment))
    print(json.dumps(environment, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
