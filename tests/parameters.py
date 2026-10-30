"""Check encoder parity against stock FFmpeg and CLI failure behavior.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import hashlib
from support import load_oracle
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / 'build/Release/smartcut.exe'
oracle = load_oracle()
FF, FP = oracle.FFMPEG, oracle.FFPROBE
RUN = ROOT / 'research/product-validation' / ('parameters-' + time.strftime('%Y%m%d-%H%M%S'))
RUN.mkdir(parents=True)
SOURCES = {
    'h264': ROOT / 'research/h264/test-data/run-20261027-125923/source-closed_b3_id0.mp4',
    'h265': ROOT / 'research/h265/test-data/fixtures/source-open.mp4',
}
BASELINES = {
    'h264': ROOT / 'research/h264/test-data/run-20261027-125923/closed_b3_id0-both_edges-avc3-crf18.json',
    'h265': ROOT / 'research/h265/test-data/matrix/open-partial-hev1.json',
}
DATA = {}
RESULTS = {'run': str(RUN), 'executable_sha256': hashlib.sha256(EXE.read_bytes()).hexdigest(), 'cases': []}


def run(command, ok=True):
    p = subprocess.run(list(map(str, command)), capture_output=True, timeout=180)
    if ok and p.returncode:
        raise RuntimeError(p.stderr.decode(errors='replace')[-3000:])
    return p


def record(name, test):
    row = {'case': name}
    try:
        row.update(test())
        row['passed'] = row.get('passed', True)
    except Exception:
        row.update(passed=False, error=traceback.format_exc())
    RESULTS['cases'].append(row)
    RESULTS['passed'] = sum(r['passed'] for r in RESULTS['cases'])
    RESULTS['total'] = len(RESULTS['cases'])
    (RUN / 'results.json').write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding='utf-8')
    (RUN.parent / 'parameters-latest.json').write_text(json.dumps({k: RESULTS[k] for k in ('run', 'passed', 'total')}, indent=2), encoding='utf-8')
    print(name, row['passed'], row.get('issues', []), row.get('error', '')[-350:], flush=True)


def variant(name, codec, settings, fallback=False):
    source = SOURCES[codec]
    if codec not in DATA:
        DATA[codec] = oracle.load_source(codec, source)
    baseline = json.loads(BASELINES[codec].read_text(encoding='utf-8-sig'))
    expected = dict(baseline)
    if fallback:
        expected.update(copied_frames=0, encoded_frames=214)
    target = RUN / (name + '.mp4')
    command = [EXE, '-hide_banner', '-v', 'error', '-i', source, '--keep-frames', '13:227',
               '-an', '-sn', '-c:v', 'libx264' if codec == 'h264' else 'libx265', *settings, target]
    p = run(command)
    target.with_suffix('.log').write_bytes(p.stderr)
    plan = json.loads(p.stdout)
    target.with_suffix('.json').write_text(json.dumps(plan, indent=2), encoding='utf-8')
    row = oracle.check(codec, DATA[codec], target, [[13, 227]], expected)
    assert plan['lossless_full_encode_fallback'] == fallback
    assert plan['copied_frames'] == expected['copied_frames']
    assert plan['encoded_frames'] == expected['encoded_frames']
    if fallback:
        assert row['pixel_identical'] == 214, 'Lossless settings were not preserved'
        row['lossless_pixels_equal_source'] = True
    else:
        # Compare the same first independent encoding job, not a full transcode
        # whose rate-control history necessarily differs from a boundary job.
        first = plan['units'][0]
        control = RUN / (name + '-stock-ffmpeg.mp4')
        begin, end = first['begin'], first['end']
        select = f'select=between(n\\,{begin}\\,{end - 1})'
        run([FF, '-hide_banner', '-v', 'error', '-copyts', '-i', source, '-vf', select,
             '-fps_mode', 'passthrough', '-enc_time_base', DATA[codec]['stream']['time_base'],
             '-an', '-c:v', 'libx264' if codec == 'h264' else 'libx265', *settings, control])
        st, packets, _ = oracle.probe(control)
        control_vcl = oracle.hashes(codec, control, st, packets)
        st, packets, _ = oracle.probe(target)
        actual_vcl = oracle.hashes(codec, target, st, packets)[:end - begin]
        assert control_vcl == actual_vcl, 'Boundary VCL differs from stock FFmpeg with the same options'
        row['stock_ffmpeg_boundary_vcl_equal'] = True
        row['boundary_frames_compared'] = end - begin
    row['command'] = list(map(str, command))
    return row


X265 = ['-x265-params', 'pools=4:frame-threads=2:log-level=error']
VARIANTS = [
    ('avc-defaults', 'h264', [], False),
    ('avc-slower-default-crf', 'h264', ['-preset:v:0', 'slower'], False),
    ('avc-ultrafast-crf12', 'h264', ['-preset', 'ultrafast', '-crf', '12'], False),
    ('avc-bf8', 'h264', ['-preset', 'slower', '-bf', '8', '-x264-params', 'b-adapt=0'], False),
    ('avc-small-gop', 'h264', ['-preset', 'slower', '-g', '4', '-x264-params', 'scenecut=0:open-gop=0'], False),
    ('avc-open-gop', 'h264', ['-preset', 'slower', '-g', '4', '-x264-params', 'scenecut=0:open-gop=1'], False),
    ('avc-sps-id3', 'h264', ['-preset', 'slower', '-x264-params', 'sps-id=3'], False),
    ('avc-baseline', 'h264', ['-profile:v', 'baseline', '-bf', '0'], False),
    ('avc-bitrate', 'h264', ['-preset', 'slower', '-b:v', '1600k'], False),
    ('avc-qp0', 'h264', ['-preset', 'slower', '-qp', '0'], True),
    ('avc-crf0', 'h264', ['-preset', 'slower', '-crf', '0'], True),
    ('hevc-default-crf', 'h265', X265, False),
    ('hevc-slower', 'h265', ['-preset', 'slower', *X265], False),
    ('hevc-ultrafast-crf12', 'h265', ['-preset', 'ultrafast', '-crf', '12', *X265], False),
    ('hevc-bf8', 'h265', ['-preset', 'slower', '-bf', '8', '-x265-params', 'pools=4:frame-threads=2:log-level=error:b-adapt=0'], False),
    ('hevc-small-gop', 'h265', ['-g', '4', '-x265-params', 'pools=4:frame-threads=2:log-level=error:open-gop=1:scenecut=0'], False),
    ('hevc-lossless', 'h265', ['-x265-params', 'pools=4:frame-threads=2:log-level=error:lossless=1'], False),
]


def rejection(name, extra, source=None, message=None):
    target = RUN / (name + '.mp4')
    sentinel = b'existing user file must survive\x00\xff'
    target.write_bytes(sentinel)
    command = [EXE, '-y', '-hide_banner', '-v', 'error', '-i', source or SOURCES['h264'],
               '--keep-frames', '13:227', '-an', '-sn', '-c:v', 'libx264', *extra, target]
    p = run(command, ok=False)
    assert p.returncode != 0, 'Invalid command succeeded'
    assert target.read_bytes() == sentinel, 'Failed job changed an existing destination'
    if message:
        assert message in p.stderr.decode(errors='replace'), p.stderr.decode(errors='replace')
    return {'exit_code': p.returncode, 'existing_destination_unchanged': True,
            'stderr': p.stderr.decode(errors='replace')[-1200:]}


def pure_copy():
    target = RUN / 'pure-copy.mp4'
    p = run([EXE, '-hide_banner', '-v', 'error', '-i', SOURCES['h264'], '--keep-frames', '24:216',
             '-an', '-c:v', 'copy', target])
    plan = json.loads(p.stdout)
    assert plan['encoded_frames'] == 0
    return oracle.check('h264', DATA['h264'], target, [[24, 216]], {'copied_frames': 192})


def analyze():
    target = RUN / 'analyze.mp4'
    p = run([EXE, '-hide_banner', '-v', 'error', '-i', SOURCES['h264'], '--keep-frames', '13:227',
             '-an', '-c:v', 'libx264', '--analyze', target])
    assert not target.exists()
    assert json.loads(p.stdout)['status'] == 'analyzed'
    return {'output_not_created': True}


def paths_and_overwrite():
    folder = RUN / '\u4e2d\u6587 space'
    folder.mkdir()
    source, target = folder / '\u6e90\u89c6\u9891 input.mp4', folder / '\u8f93\u51fa output.mp4'
    shutil.copyfile(SOURCES['h264'], source)
    before = source.read_bytes()
    p = run([EXE, '-y', '-i', source, '--keep-frames', '13:227', '-an', '-c:v', 'libx264', source], ok=False)
    assert p.returncode and source.read_bytes() == before
    alias = folder / 'input-alias.mp4'
    os.link(source, alias)
    p = run([EXE, '-y', '-i', source, '--keep-frames', '13:227', '-an', '-c:v', 'libx264', alias], ok=False)
    assert p.returncode and source.read_bytes() == before
    target.write_bytes(b'old destination')
    preserved_alias = folder / 'old-destination-alias.mp4'
    os.link(target, preserved_alias)
    args = [EXE, '-hide_banner', '-v', 'error', '-i', source, '--keep-frames', '13:227', '-an', '-c:v', 'libx264']
    for overwrite in ([], ['-n']):
        p = run([*args, *overwrite, target], ok=False)
        assert p.returncode and target.read_bytes() == b'old destination'
    run([*args, '-y', target])
    assert source.read_bytes() == before and preserved_alias.read_bytes() == b'old destination'
    row = oracle.check('h264', DATA['h264'], target, [[13, 227]], {'copied_frames': 192})
    row['unicode_paths_and_atomic_replace'] = True
    return row


def auxiliary_quoting():
    pointer = json.loads((RUN.parent / 'streams-latest.json').read_text())
    source = Path(pointer['run']) / 'source-avs.mp4'
    folder = RUN / 'aux spaces'
    folder.mkdir()
    target = folder / 'output.mp4'
    title = 'Alpha "beta" C:\\Media\\'
    p = run([EXE, '-hide_banner', '-v', 'error', '-i', source, '-ss', '1', '-t', '2',
             '-map', '0:v', '-map', '0:a:0', '-c:v', 'libx264', '-c:a', 'aac', '-b:a', '192k',
             '-metadata', 'title=' + title, target])
    data = json.loads(run([FP, '-v', 'error', '-show_format', '-of', 'json', target]).stdout)
    assert data['format']['tags']['title'] == title
    return {'quoted_metadata_roundtrip': True, 'exit_code': p.returncode}


try:
    for name, codec, settings, fallback in VARIANTS:
        record(name, lambda n=name, c=codec, s=settings, f=fallback: variant(n, c, s, f))
    for name, extra, source, message in [
        ('mismatch-avc-hevc', ['-c:v', 'libx265'], None, 'Video codec mismatch'),
        ('mismatch-hevc-avc', [], SOURCES['h265'], 'Video codec mismatch'),
        ('mismatch-av1', ['-c:v', 'libsvtav1'], None, 'Video codec mismatch'),
        ('unknown-encoder', ['-c:v', 'encoder_does_not_exist'], None, 'encoder'),
        ('unknown-option', ['-presett:v', 'slower'], None, 'option'),
        ('invalid-preset', ['-preset', 'invalid_preset'], None, 'encoder'),
        ('copy-partial', ['-c:v', 'copy'], None, 'Frame-exact boundaries require'),
        ('required-map-missing', ['-map', '0:v:99'], None, None),
        ('video-filter', ['-vf', 'scale=64:64'], None, 'full FFmpeg transcode'),
        ('fps-change', ['-r:v', '30'], None, 'full FFmpeg transcode'),
        ('pixel-format-change', ['-pix_fmt', 'yuv444p'], None, 'must match'),
        ('wrong-container-tag', ['-tag:v', 'hvc1'], None, 'sample entry'),
        ('bad-range', ['--keep-frames', '200:199'], None, 'ranges'),
        ('range-past-eof', ['--keep-frames', '300:400'], None, 'ranges'),
        ('overlapping-ranges', ['--keep-frames', '20:40'], None, 'ranges'),
        ('range-plus-time', ['-ss', '1'], None, 'cannot be combined'),
        ('multiple-inputs', ['-i', str(SOURCES['h264'])], None, 'One input'),
    ]:
        record(name, lambda n=name, e=extra, s=source, m=message: rejection(n, e, s, m))
    record('copy-complete-idr-epochs', pure_copy)
    record('analyze-no-output', analyze)
    record('unicode-hardlinks-atomic-overwrite', paths_and_overwrite)
    record('auxiliary-windows-argument-quoting', auxiliary_quoting)
finally:
    oracle.POOL.shutdown(wait=True, cancel_futures=True)
print(json.dumps({k: RESULTS[k] for k in ('passed', 'total')}, indent=2), flush=True)
raise SystemExit(0 if RESULTS['passed'] == RESULTS['total'] else 1)
