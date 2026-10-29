"""Check fractional/VFR clocks and the literal HH:MM:SS example.
SPDX-License-Identifier: GPL-3.0-or-later
"""
from fractions import Fraction
import hashlib
from support import load_oracle
import json
from pathlib import Path
import subprocess
import time
import traceback
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / 'build/Release/smartcut.exe'
o = load_oracle()
FF, FP = o.FFMPEG, o.FFPROBE
RUN = ROOT / 'research/product-validation' / ('timestamps-' + time.strftime('%Y%m%d-%H%M%S'))
RUN.mkdir(parents=True)
RESULTS = {'run': str(RUN), 'executable_sha256': hashlib.sha256(EXE.read_bytes()).hexdigest(), 'cases': []}


def run(command):
    p = subprocess.run(list(map(str, command)), capture_output=True, timeout=180)
    if p.returncode:
        raise RuntimeError(p.stderr.decode(errors='replace')[-2500:])
    return p


def rounded(value):
    return (value.numerator * 2 + value.denominator) // (value.denominator * 2)


def video_check(codec, source, target, start, end):
    data = o.load_source(codec, source)
    tb = Fraction(data['stream']['time_base'])
    # The source MP4 fixtures begin at their media start_time, including offset.
    source_origin = Fraction(data['stream'].get('start_time', '0'))
    a, b = source_origin + start, source_origin + end
    wanted = [i for i, t in enumerate(data['times'][:-1]) if a <= t * tb < b]
    origin_ticks = rounded(a / tb)
    expected_pts = [(data['times'][i] - origin_ticks) * tb for i in wanted]
    expected_end = min(b, data['times'][wanted[-1] + 1] * tb) - origin_ticks * tb
    s, packets, _ = o.probe(target)
    out_tb = Fraction(s['time_base'])
    pts = [int(p['pts']) * out_tb for p in sorted(packets, key=lambda q: int(q['pts']))]
    assert pts == expected_pts, f'PTS mismatch: {pts[:3]} != {expected_pts[:3]}'
    frames = json.loads(run([FP, '-v', 'error', '-select_streams', 'v', '-show_frames',
                           '-show_entries', 'frame=pts', '-of', 'json', target]).stdout)['frames']
    assert [int(f['pts']) * out_tb for f in frames] == expected_pts
    assert all(int(p['dts']) <= int(p['pts']) for p in packets)
    assert all(int(a['dts']) < int(b['dts']) for a, b in zip(packets, packets[1:]))
    actual, _ = o.decode(target, s)
    assert len(actual) == len(wanted) == len(packets)
    strict = run([FF, '-v', 'error', '-xerror', '-err_detect', 'explode', '-i', target, '-map', '0:v',
                  '-vf', 'setpts=N/25/TB', '-enc_time_base', '1/25', '-fps_mode', 'passthrough', '-f', 'null', '-'])
    assert not strict.stderr.strip(), strict.stderr.decode(errors='replace')
    hashes = o.hashes(codec, target, s, packets)
    copied = 0
    width, height = int(s['width']), int(s['height'])
    for j, i in enumerate(wanted):
        if hashes[j]['normalized'] == data['hashes'][i]['normalized']:
            copied += 1
            assert np.array_equal(actual[j], data['pixels'][i])
        lum = actual[j, :width * height].reshape(height, width)
        barcode = sum(int(lum[6:14, 8 + bit * 16:14 + bit * 16].mean() > 127.5) << bit for bit in range(10))
        assert barcode == i, (j, i, barcode)
    assert abs(Fraction(s['start_time']) - expected_pts[0]) <= Fraction(1, 1000000)
    assert abs(Fraction(s['duration']) - (expected_end - expected_pts[0])) <= Fraction(1, 1000000), (s['duration'], expected_end, expected_pts[0])
    return {'video_frames': len(wanted), 'first_source_frame': wanted[0], 'last_source_frame': wanted[-1],
            'first_pts': str(pts[0]), 'last_pts': str(pts[-1]), 'presentation_end': str(expected_end),
            'copied_vcl_and_pixels': copied, 'decoded_pts_exact': True, 'strict_decode': True}


def case(name, test):
    row = {'case': name}
    try:
        row.update(test())
        row['passed'] = True
    except Exception:
        row.update(passed=False, error=traceback.format_exc())
    RESULTS['cases'].append(row)
    RESULTS['passed'] = sum(r['passed'] for r in RESULTS['cases'])
    RESULTS['total'] = len(RESULTS['cases'])
    (RUN / 'results.json').write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding='utf-8')
    (RUN.parent / 'timestamps-latest.json').write_text(json.dumps({k: RESULTS[k] for k in ('run', 'passed', 'total')}, indent=2), encoding='utf-8')
    print(name, row['passed'], row.get('error', '')[-350:], flush=True)


def clip(codec, fixture, before, cut, start, end):
    source = ROOT / (f'research/h264/test-data/run-20261027-125923/source-{fixture}.mp4' if codec == 'h264'
                     else f'research/h265/test-data/fixtures/source-{fixture}.mp4')
    target = RUN / (codec + '-' + fixture + '-' + str(time.time_ns()) + '.mp4')
    encoding = ['-c:v', 'libx264'] if codec == 'h264' else ['-c:v', 'libx265', '-x265-params', 'pools=4:frame-threads=2:log-level=error']
    p = run([EXE, '-hide_banner', '-v', 'error', *before, '-i', source, *cut, '-an', '-sn', *encoding, target])
    target.with_suffix('.json').write_bytes(p.stdout)
    return video_check(codec, source, target, Fraction(start), Fraction(end))


def aux_signature(path):
    d = json.loads(run([FP, '-v', 'error', '-select_streams', 'a', '-show_streams', '-show_packets',
                       '-show_data_hash', 'sha256', '-of', 'json', path]).stdout)
    packets = [{k: p.get(k) for k in ('pts', 'dts', 'duration', 'data_hash', 'side_data_list')} for p in d['packets']]
    pcm = run([FF, '-v', 'error', '-i', path, '-map', '0:a', '-f', 'f32le', '-']).stdout
    return {'packets': packets, 'pcm_sha256': hashlib.sha256(pcm).hexdigest(), 'pcm_bytes': len(pcm)}


def long_clock():
    # Four hours on the timeline, 240 coded frames: tests clock semantics
    # without claiming to be a one-hour throughput/endurance benchmark.
    source = ROOT / 'research/h264/test-data/run-20261027-125923/source-closed_b3_id0.mp4'
    sparse, avs, target, control = (RUN / name for name in ('source-4h.mp4', 'source-4h-audio.mp4', 'literal-example.mp4', 'literal-audio-ffmpeg.mp4'))
    run([FF, '-v', 'error', '-i', source, '-an', '-c:v', 'copy', '-bsf:v',
         'setts=pts=PTS*1500:dts=DTS*1500:duration=DURATION*1500', sparse])
    run([FF, '-v', 'error', '-i', sparse, '-itsoffset', '3600', '-f', 'lavfi', '-i',
         'sine=frequency=997:sample_rate=48000:duration=5', '-map', '0:v', '-map', '1:a', '-c:v', 'copy', '-c:a', 'aac', avs])
    p = run([EXE, '-hide_banner', '-y', '-v', 'error', '-i', avs, '-ss', '1:0:1', '-to', '2:0:1',
             '-map', '0:v:0?', '-map', '0:a:0?', '-c:v:0', 'libx264', '-preset:v:0', 'slower',
             '-c:a:0', 'aac', '-b:a:0', '192k', target])
    target.with_suffix('.json').write_bytes(p.stdout)
    run([FF, '-hide_banner', '-y', '-v', 'error', '-i', avs, '-ss', '1:0:1', '-to', '2:0:1',
         '-map', '0:a:0?', '-c:a:0', 'aac', '-b:a:0', '192k', control])
    row = video_check('h264', sparse, target, Fraction(3601), Fraction(7201))
    assert row['video_frames'] == 60 and row['first_source_frame'] == 61 and row['last_source_frame'] == 120
    assert aux_signature(target) == aux_signature(control), 'Literal example audio differs from stock FFmpeg'
    row.update(audio_packets_pcm_equal_stock_ffmpeg=True, sparse_four_hour_clock=True)
    return row


try:
    for codec in ('h264', 'h265'):
        for fixture in (('closed_b3_id0' if codec == 'h264' else 'open'), 'ntsc', 'vfr', 'offset'):
            case(codec + '-' + fixture + '-fractional', lambda c=codec, f=fixture:
                 clip(c, f, [], ['-ss', '0.53', '-to', '7.83'], '0.53', '7.83'))
        fixture = 'closed_b3_id0' if codec == 'h264' else 'open'
        case(codec + '-two-seek-groups', lambda c=codec, f=fixture:
             clip(c, f, ['-ss', '0.32'], ['-ss', '0.53', '-t', '2'], '0.85', '2.85'))
        case(codec + '-duration-precedes-to', lambda c=codec, f=fixture:
             clip(c, f, [], ['-ss', '0.53', '-t', '2', '-to', '1'], '0.53', '2.53'))
        case(codec + '-input-seek-ntsc', lambda c=codec:
             clip(c, 'ntsc', ['-ss', '0.53', '-to', '7.83'], [], '0.53', '7.83'))
    case('literal-hhmmss-example', long_clock)
finally:
    o.POOL.shutdown(wait=True, cancel_futures=True)
print(json.dumps({k: RESULTS[k] for k in ('passed', 'total')}, indent=2))
raise SystemExit(0 if RESULTS['passed'] == RESULTS['total'] else 1)
