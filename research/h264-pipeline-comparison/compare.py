"""Compare unchanged H.264 implementations using one source and one FFmpeg build.

End-to-end process wall time excludes independent output validation. Defaults,
including each implementation's thread policy, are retained. No --verify in timing.
"""
import hashlib
import json
import statistics
import subprocess
import time
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
SDK = ROOT.parent/'h264/deps/ffmpeg-n8.1.3-24-g92611900bb-win64-gpl-shared-8.1/bin'
CPP = ROOT.parent/'h264/build/Release/smart_render.exe'
CS = ROOT / 'artifacts/bin/SmartCut.Cli/release/SmartCut.Cli.exe'
FFMPEG, FFPROBE = SDK / 'ffmpeg.exe', SDK / 'ffprobe.exe'
SOURCE = REPO / 'h264/test-data/experiments-20261027-130026/benchmark-1080p.mp4'
WORK = ROOT / ('benchmark-' + time.strftime('%H%M%S'))
WORK.mkdir()

def run(args):
    start = time.perf_counter()
    p = subprocess.run([str(a) for a in args], capture_output=True)
    elapsed = time.perf_counter() - start
    if p.returncode:
        raise RuntimeError(p.stderr.decode('utf-8', 'replace')[-3000:])
    return elapsed, p

def probe(path):
    return json.loads(run([FFPROBE, '-v', 'error', '-select_streams', 'v:0',
                          '-show_entries', 'stream=time_base,duration:packet=pts,dts,duration',
                          '-of', 'json', path])[1].stdout)

def validate(source, output, keeps):
    src, out = probe(source), probe(output)
    st = Fraction(src['streams'][0]['time_base'])
    ot = Fraction(out['streams'][0]['time_base'])
    frames = sorted(src['packets'], key=lambda x: x['pts'])
    ticks = [p['pts'] for p in frames] + [frames[-1]['pts'] + frames[-1]['duration']]
    expected, offset = [], Fraction(0)
    for begin, end in keeps:
        expected += [offset + (ticks[i] - ticks[begin]) * st for i in range(begin, end)]
        offset += (ticks[end] - ticks[begin]) * st
    actual = sorted(p['pts'] * ot for p in out['packets'])
    assert actual == expected, 'output PTS/frame count differs'
    dts = [p['dts'] for p in out['packets']]
    assert all(a < b for a, b in zip(dts, dts[1:])), 'nonmonotonic DTS'
    assert all(p['dts'] <= p['pts'] for p in out['packets']), 'DTS exceeds PTS'
    assert abs(Fraction(out['streams'][0]['duration']) - offset) <= Fraction(1, 1000000), 'duration differs'
    _, p = run([FFMPEG, '-v', 'error', '-xerror', '-err_detect', 'explode', '-i', output,
                '-map', '0:v:0', '-fps_mode', 'passthrough', '-f', 'null', '-'])
    assert not p.stderr.strip(), p.stderr.decode('utf-8', 'replace')[-1000:]
    return {'passed': True, 'frames': len(actual), 'pts': 'exact', 'dts': 'monotonic_and_le_pts',
            'duration': 'within_1us', 'strict_decode': 'no_errors'}

long_source = WORK / 'source-60s.mp4'
run([FFMPEG, '-v', 'error', '-stream_loop', '2', '-i', SOURCE, '-map', '0:v:0', '-c', 'copy', long_source])
cases = [
    ('20s_two_edges', SOURCE, [(13, 587)]),
    ('20s_copy_only', SOURCE, [(60, 540)]),
    ('60s_late_selection', long_source, [(1213, 1787)]),
    ('20s_five_ranges', SOURCE, [(13 + i * 120, 107 + i * 120) for i in range(5)]),
]
result = {
    'repository_commit': run(['git', '-C', REPO, 'rev-parse', 'HEAD'])[1].stdout.decode().strip(),
    'ffmpeg': run([FFMPEG, '-version'])[1].stdout.decode().splitlines()[0],
    'cpp_exe_sha256': hashlib.sha256(CPP.read_bytes()).hexdigest(),
    'source_sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
    'method': 'One warmup per implementation and case, then 3 interleaved end-to-end runs; warm filesystem cache; no audio; no verify in timer; original encoder/thread settings retained.',
    'cases': []
}
for name, source, keeps in cases:
    row = {'case': name, 'source': str(source), 'keeps': keeps, 'runs': [], 'validation': {}}
    for repeat in range(4):
        order = ['h264', 'pipeline'] if repeat % 2 == 0 else ['pipeline', 'h264']
        for arm in order:
            output = WORK / f'{name}-{arm}-{repeat}.mp4'
            report = WORK / f'{name}-{arm}-{repeat}.json'
            cmd = [CPP if arm == 'h264' else CS, '-i', source, '-o', output, '--report', report]
            for begin, end in keeps:
                cmd += ['--keep', f'{begin}:{end}']
            if arm == 'pipeline':
                cmd += ['--no-audio', '--ffmpeg-dir', SDK, '--work-dir', WORK]
            seconds, p = run(cmd)
            payload = json.loads(report.read_text(encoding='utf-8-sig'))
            item = {'arm': arm, 'repeat': repeat, 'warmup': repeat == 0, 'wall_seconds': seconds,
                    'copied_frames': payload['copied_frames'], 'encoded_frames': payload['encoded_frames'],
                    'output': str(output), 'command': [str(v) for v in cmd]}
            row['runs'].append(item)
            print(json.dumps({k: item[k] for k in ('arm', 'repeat', 'wall_seconds', 'copied_frames', 'encoded_frames')}, ensure_ascii=False), flush=True)
    for arm in ('h264', 'pipeline'):
        values = [r['wall_seconds'] for r in row['runs'] if r['arm'] == arm and not r['warmup']]
        row[f'{arm}_median_seconds'] = statistics.median(values)
        row['validation'][arm] = validate(source, WORK / f'{name}-{arm}-3.mp4', keeps)
    row['pipeline_over_h264'] = row['pipeline_median_seconds'] / row['h264_median_seconds']
    result['cases'].append(row)
    (ROOT / 'comparison-benchmark.json').write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({k: row[k] for k in ('case', 'h264_median_seconds', 'pipeline_median_seconds', 'pipeline_over_h264')}, ensure_ascii=False), flush=True)
print('Complete:', ROOT / 'comparison-benchmark.json', flush=True)
