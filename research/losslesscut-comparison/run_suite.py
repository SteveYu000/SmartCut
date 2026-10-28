"""Black-box LosslessCut 3.70.2 Smart Cut against the existing AVC/HEVC suites.

SPDX-License-Identifier: GPL-3.0-or-later
The official portable application must be running with this directory's isolated
config and --http-api 49327. No LosslessCut algorithms are reimplemented here.
"""
import argparse
import ast
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
import hashlib
import http.client
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import time
import traceback
import urllib.request
import numpy as np

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
APPLICATION = ROOT.parent.parent / 'sources/LosslessCut-3.70.2-win64'
SDK = ROOT.parent/'h264/deps/ffmpeg-n8.1.3-24-g92611900bb-win64-gpl-shared-8.1'
FFMPEG, FFPROBE = SDK / 'bin/ffmpeg.exe', SDK / 'bin/ffprobe.exe'
os.environ['FFMPEG_ROOT'] = str(SDK)
API = 'http://127.0.0.1:49327/api'
LOG = ROOT / 'application.stdout.log'
POOL = ThreadPoolExecutor(max_workers=2)

class NoVideoTrack(AssertionError):
    pass

def run(command, strict=True):
    p = subprocess.run([str(x) for x in command], capture_output=True, timeout=120)
    if strict and p.returncode:
        raise RuntimeError(p.stderr.decode('utf-8', 'replace')[-2000:])
    return p

def api(endpoint, body=None, timeout=45):
    data = json.dumps({} if body is None else body).encode()
    request = urllib.request.Request(API + endpoint, data=data, headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(request, timeout=timeout) as r:
        payload = r.read()
        return json.loads(payload) if payload else None

def await_event(name, timeout):
    # The release attaches abort to IncomingMessage.close; ending an ordinary
    # POST body aborts its own wait immediately. Keep a non-JSON request body
    # pending until the server sends the event. This changes only HTTP framing,
    # not application settings, export logic, or FFmpeg arguments.
    conn = http.client.HTTPConnection('127.0.0.1',49327,timeout=timeout)
    try:
        conn.putrequest('POST','/api/await-event/'+name)
        conn.putheader('Content-Type','application/octet-stream')
        conn.putheader('Content-Length','1')
        conn.endheaders()
        response = conn.getresponse()
        payload = response.read()
        if response.status != 200:
            raise RuntimeError(f'event HTTP {response.status}: '+payload.decode(errors='replace')[:400])
        return json.loads(payload)
    finally:
        conn.close()

def extract_assignment(path, variable):
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    node = next(n for n in tree.body if isinstance(n, ast.Assign) and
                any(isinstance(t, ast.Name) and t.id == variable for t in n.targets))
    scope = {'N': 240}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), scope)
    return scope[variable]

# Reuse the HEVC suite's byte inspection/configuration normalization helpers.
spec = importlib.util.spec_from_file_location('hevc_test_oracle', REPO / 'h265/tests/common.py')
hevc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hevc)

def avc_hashes(path, stream, packets):
    # Same length-prefixed VCL SHA-256 representation as h264/tests/integration.py.
    raw = path.read_bytes()
    result = []
    length = int(stream.get('nal_length_size', 4))
    for packet in sorted(packets, key=lambda p: int(p['pts'])):
        start, size = int(packet['pos']), int(packet['size'])
        data, pos, vcl = raw[start:start+size], 0, bytearray()
        while pos < len(data):
            count = int.from_bytes(data[pos:pos+length], 'big')
            pos += length
            if count <= 0 or pos + count > len(data):
                raise AssertionError('invalid AVC NAL framing')
            nal = data[pos:pos+count]
            if nal[0] & 31 in (1, 5):
                vcl += struct.pack('>I', len(nal)) + nal
            pos += count
        digest = hashlib.sha256(vcl).hexdigest()
        result.append({'hash': digest, 'normalized': digest})
    return result

def probe(path):
    d = json.loads(run([FFPROBE, '-v', 'error', '-show_streams', '-show_packets', '-of', 'json', path]).stdout)
    s = next((s for s in d['streams'] if s['codec_type'] == 'video'),None)
    if s is None:
        raise NoVideoTrack('output has no video track')
    return s, [p for p in d.get('packets', []) if p['stream_index'] == s['index']], d['streams']

def decode(path, s):
    pix = 'yuv420p10le' if '10' in s['pix_fmt'] else 'yuvj420p' if s['pix_fmt'] == 'yuvj420p' else 'yuv420p'
    # Synthetic rawvideo timing avoids confusing raw mux rounding with a codec error.
    # Real packet and decoded-frame timestamps are checked independently below.
    p = run([FFMPEG, '-v', 'error', '-i', path, '-map', '0:v:0', '-vf', 'setpts=N/25/TB',
             '-enc_time_base', '1/25', '-fps_mode', 'passthrough', '-pix_fmt', pix, '-f', 'rawvideo', '-'], strict=False)
    data = np.frombuffer(p.stdout, dtype=np.uint16 if '10' in pix else np.uint8)
    frame_size = int(s['width']) * int(s['height']) * 3 // 2
    if len(data) % frame_size:
        raise AssertionError('partial decoded frame')
    return data.reshape((-1, frame_size)), p

def hashes(codec, path, stream, packets):
    if codec == 'h264':
        return avc_hashes(path, stream, packets)
    h = hevc.samples(path, packets)
    return [h[i] for i in sorted(range(len(packets)), key=lambda i: int(packets[i]['pts']))]

def load_source(codec, path, expected_frames=240):
    s, p, streams = probe(path)
    raw, dec = decode(path, s)
    if dec.returncode or dec.stderr.strip() or len(raw) != expected_frames:
        raise AssertionError('input fixture did not decode cleanly')
    display = sorted(p, key=lambda q: int(q['pts']))
    times = [int(q['pts']) for q in display]
    times.append(times[-1] + int(display[-1]['duration']))
    return {'stream': s, 'packets': p, 'pixels': raw, 'times': times,
            'hashes': hashes(codec, path, s, p), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}

def check(codec, source, output, ranges, baseline):
    s, packets, tracks = probe(output)
    actual, decoded = decode(output, s)
    wanted = [i for begin, end in ranges for i in range(begin, end)]
    row = {'expected_frames': len(wanted), 'decoded_frames': len(actual), 'samples': len(packets),
           'issues': [], 'exact_vcl': 0, 'normalized_vcl': 0, 'pixel_identical': 0,
           'copied_pixel_mismatches': 0, 'barcode_errors': [], 'baseline_copied_frames': baseline['copied_frames']}
    issues = row['issues']
    if len(tracks) != 1 or tracks[0]['codec_type'] != 'video':
        issues.append('unexpected_tracks')
    if len(actual) != len(wanted) or len(packets) != len(wanted):
        issues.append('frame_count')
    if decoded.returncode or decoded.stderr.strip():
        issues.append('decode_error')
        row['decode_log'] = decoded.stderr.decode('utf-8', 'replace')[-2500:]
    strict = run([FFMPEG, '-v', 'error', '-xerror', '-err_detect', 'explode', '-i', output, '-map', '0:v:0',
                  '-vf', 'setpts=N/25/TB', '-enc_time_base', '1/25', '-fps_mode', 'passthrough', '-f', 'null', '-'], strict=False)
    if strict.returncode or strict.stderr.strip():
        issues.append('strict_decode')
        row['strict_decode_log'] = strict.stderr.decode('utf-8', 'replace')[-2500:]

    expected_pts, cursor = [], Fraction(0)
    tb, out_tb = Fraction(source['stream']['time_base']), Fraction(s['time_base'])
    for begin, end in ranges:
        expected_pts += [cursor + (source['times'][i] - source['times'][begin]) * tb for i in range(begin, end)]
        cursor += (source['times'][end] - source['times'][begin]) * tb
    ordered_packets = sorted(packets, key=lambda p: int(p['pts']))
    pts = [int(p['pts']) * out_tb for p in ordered_packets]
    if pts != expected_pts:
        issues.append('pts')
    row['pts_mismatches'] = sum(a != b for a, b in zip(pts, expected_pts)) + abs(len(pts)-len(expected_pts))
    row['first_pts_seconds'] = float(pts[0]) if pts else None
    row['normalized_pts_match'] = bool(pts) and [p-pts[0] for p in pts] == expected_pts
    row['pts_max_error_seconds'] = float(max((abs(a-b) for a,b in zip(pts,expected_pts)), default=0))
    fd = json.loads(run([FFPROBE, '-v', 'error', '-select_streams', 'v:0', '-show_frames',
                         '-show_entries', 'frame=pts,best_effort_timestamp', '-of', 'json', output]).stdout).get('frames', [])
    frame_pts = [int(f.get('pts', f.get('best_effort_timestamp', -1))) * out_tb for f in fd]
    if frame_pts != expected_pts:
        issues.append('decoded_pts')
    dts = [int(p['dts']) for p in packets]
    if not all(a < b for a,b in zip(dts,dts[1:])) or not all(int(p['dts']) <= int(p['pts']) for p in packets):
        issues.append('dts')
    row['expected_duration_seconds'] = float(cursor)
    row['duration_seconds'] = float(s['duration']) if 'duration' in s else None
    if 'duration' not in s or abs(Fraction(s['duration']) - cursor) > Fraction(1, 1000000):
        issues.append('duration')
    for key in ('sample_aspect_ratio', 'color_range', 'color_space', 'color_transfer', 'color_primaries') + (('pix_fmt',) if codec == 'h265' else ()):
        if key in source['stream'] and s.get(key) != source['stream'][key]:
            issues.append('metadata_' + key)
    row['codec_tag'] = s.get('codec_tag_string')
    row['sample_description_count'] = len(hevc.configs(output)) if codec == 'h265' else None
    output_hashes = hashes(codec, output, s, packets)
    source_hashes = source['hashes']
    row['vcl_multiset_preserved'] = sum((Counter(x['hash'] for x in output_hashes) & Counter(source_hashes[i]['hash'] for i in wanted)).values())
    psnrs = []
    width,height = int(source['stream']['width']),int(source['stream']['height'])
    same_dimensions = (int(s['width']),int(s['height'])) == (width,height)
    if not same_dimensions:
        issues.append('dimensions')
    peak = 1023.0 if source['pixels'].dtype == np.uint16 else 255.0
    for position, rank in enumerate(wanted[:len(actual)]):
        exact = position < len(output_hashes) and output_hashes[position]['hash'] == source_hashes[rank]['hash']
        normalized = position < len(output_hashes) and output_hashes[position]['normalized'] == source_hashes[rank]['normalized']
        row['exact_vcl'] += int(exact)
        row['normalized_vcl'] += int(normalized)
        if not same_dimensions:
            continue
        equal = np.array_equal(actual[position], source['pixels'][rank])
        row['pixel_identical'] += int(equal)
        if normalized and not equal:
            row['copied_pixel_mismatches'] += 1
        lum = actual[position,:width*height].reshape(height,width)
        if codec == 'h264':
            barcode = sum(int(lum[6:14,8+bit*16:14+bit*16].mean() > peak/2) << bit for bit in range(10))
        else:
            barcode = sum(int(lum[4:16,6+bit*16:16+bit*16].mean() > peak/2) << bit for bit in range(10))
        if barcode != rank and len(row['barcode_errors']) < 12:
            row['barcode_errors'].append([position, rank, barcode])
        if not equal:
            mse = np.square(actual[position].astype(np.float64)-source['pixels'][rank].astype(np.float64)).mean()
            psnrs.append(float(10*math.log10(peak*peak/mse)))
    row['min_frame_psnr_db'] = min(psnrs) if psnrs else None
    if row['barcode_errors']:
        issues.append('frame_identity')
    if row['copied_pixel_mismatches']:
        issues.append('copied_pixels')
    if psnrs and min(psnrs) < 30:
        issues.append('quality_below_30dB')
    row['copy_efficiency_passed'] = row['normalized_vcl'] >= baseline['copied_frames']
    row['correctness_passed'] = not issues
    row['passed'] = not issues and row['copy_efficiency_passed']
    return row

def export(case_dir, source_path, source, ranges, case_id, strip_audio=False, event_timeout=30):
    case_dir.mkdir(parents=True, exist_ok=True)
    media = case_dir / (case_id.replace('/', '-') + '-' + str(time.time_ns()) + '.mp4')
    if not media.exists():
        shutil.copyfile(source_path, media)
    if hashlib.sha256(media.read_bytes()).hexdigest() != source['sha256']:
        raise AssertionError('fixture copy checksum differs')
    tb, first = Fraction(source['stream']['time_base']), source['times'][0]
    project = {'version': 2, 'mediaFileName': media.name,
               'cutSegments': [{'start':float((source['times'][a]-first)*tb),
                                'end':float((source['times'][b]-first)*tb), 'name':str(i)}
                               for i,(a,b) in enumerate(ranges)]}
    project_path = case_dir / 'project.llc'
    project_path.write_text(json.dumps(project, indent=2), encoding='utf-8')
    log_begin = LOG.stat().st_size
    api('/action/closeCurrentFile')
    time.sleep(0.08)
    api('/action/openFiles', [str(project_path)], timeout=30)
    time.sleep(0.25)  # allow the new React state/keyboard-action closure to commit
    if strip_audio:
        api('/action/toggleStripAudio')
        time.sleep(0.12)
    completed = POOL.submit(await_event, 'export-complete', event_timeout)
    started = POOL.submit(await_event, 'export-start', 5)
    time.sleep(0.05)
    begin = time.perf_counter()
    api('/action/export')  # main action response does not await the export promise
    try:
        event_start = started.result(timeout=7)
        if os.path.normcase(event_start['path']) != os.path.normcase(str(media)):
            raise AssertionError('export-start refers to a different input')
        event = completed.result(timeout=event_timeout+2)
    except Exception:
        # Drain the outstanding waiter before another case can emit its event.
        try:
            completed.result(timeout=event_timeout+2)
        except Exception:
            pass
        raise
    elapsed = time.perf_counter()-begin
    time.sleep(0.05)
    with LOG.open('rb') as f:
        f.seek(log_begin)
        (case_dir/'application.log').write_bytes(f.read())
    if not event.get('paths'):
        return None, {'export_event':event, 'export_seconds':elapsed, 'app_export_error':True}
    if len(event['paths']) != 1:
        raise AssertionError('expected one merged output')
    output = Path(event['paths'][0])
    if not output.is_file():
        raise AssertionError('app returned a nonexistent output path')
    saved = case_dir/'output.mp4'
    if saved.exists():
        saved = case_dir/f'output-{time.time_ns()}.mp4'
    shutil.copyfile(output, saved)
    # Keep app-owned output files in exports/; unique input names avoid collisions.
    return saved, {'export_event':event, 'export_seconds':elapsed, 'output':str(saved), 'output_sha256':hashlib.sha256(saved.read_bytes()).hexdigest()}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--only', help='comma-separated codec/fixture/cut identifiers')
    args = parser.parse_args()
    references = {
        'h264':json.loads((REPO/'h264/docs/evidence/final-integration.json').read_text(encoding='utf-8-sig')),
        'h265':json.loads((REPO/'h265/test-data/matrix/results.json').read_text(encoding='utf-8-sig'))}
    cuts = {'h264':extract_assignment(REPO/'h264/tests/integration.py','cuts'),
            'h265':extract_assignment(REPO/'h265/tests/integration.py','CUTS')}
    fixture_dirs = {'h264':REPO/'h264/test-data/run-20261027-125923', 'h265':REPO/'h265/test-data/fixtures'}
    report_path = ROOT/'results.json'
    results = json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else {'application':'LosslessCut 3.70.2 official win-x64 portable', 'cases':[], 'fixtures':{}}
    retryable = [r for r in results['cases'] if 'harness_or_inspection_error' in r.get('issues',[])]
    if retryable:
        results.setdefault('harness_attempts',[]).extend(retryable)
        results['cases'] = [r for r in results['cases'] if r not in retryable]
    results['oracle_ffmpeg'] = run([FFMPEG,'-version']).stdout.decode().splitlines()[0]
    results['bundled_ffmpeg'] = run([APPLICATION/'resources/ffmpeg.exe','-version']).stdout.decode().splitlines()[0]
    done = {r['id'] for r in results['cases']}
    only = set(args.only.split(',')) if args.only else None
    for codec in ('h264','h265'):
        for name, fixture_ref in references[codec]['fixtures'].items():
            pending = [label for label in cuts[codec] if f'{codec}/{name}/{label}' not in done and
                       (only is None or f'{codec}/{name}/{label}' in only)]
            if not pending:
                continue
            path = fixture_dirs[codec]/f'source-{name}.mp4'
            source = load_source(codec,path)
            assert source['sha256'] == fixture_ref['sha256'], 'input differs from original suite'
            results['fixtures'][f'{codec}/{name}'] = {'path':str(path),'sha256':source['sha256']}
            for label in pending:
                ident = f'{codec}/{name}/{label}'
                case_dir = ROOT/'cases'/ident
                ranges = cuts[codec][label]
                baseline = next(r for r in references[codec]['cases'] if r['fixture'] == name and
                                (r.get('label') if codec == 'h264' else r.get('cut')) == label and
                                r['mode'] == ('avc3' if codec == 'h264' else 'hev1') and
                                (r.get('crf',18) == 18))
                if codec == 'h265':
                    baseline = baseline['plan']
                row = {'id':ident,'codec':codec,'fixture':name,'cut':label,'ranges':ranges,'case_directory':str(case_dir)}
                try:
                    output, info = export(case_dir,path,source,ranges,ident)
                    row.update(info)
                    if output is None:
                        row.update(passed=False,correctness_passed=False,issues=['app_export_error'])
                    else:
                        row.update(check(codec,source,output,ranges,baseline))
                except NoVideoTrack as exc:
                    row.update(passed=False,correctness_passed=False,issues=['missing_video_track','frame_count'],error=str(exc),decoded_frames=0,samples=0)
                except Exception as exc:
                    row.update(passed=False,correctness_passed=False,issues=['harness_or_inspection_error'],error=str(exc))
                    (case_dir/'exception.txt').write_text(traceback.format_exc(),encoding='utf-8')
                results['cases'].append(row)
                results['summary'] = {c:{'total':sum(x['codec']==c for x in results['cases']),
                                         'passed':sum(x['codec']==c and x['passed'] for x in results['cases']),
                                         'correctness_passed':sum(x['codec']==c and x.get('correctness_passed',False) for x in results['cases']),
                                         'issues':dict(Counter(issue for x in results['cases'] if x['codec']==c for issue in x['issues']))}
                                      for c in ('h264','h265')}
                temp = report_path.with_suffix('.tmp')
                temp.write_text(json.dumps(results,indent=2,ensure_ascii=False),encoding='utf-8')
                temp.replace(report_path)
                (case_dir/'result.json').write_text(json.dumps(row,indent=2,ensure_ascii=False),encoding='utf-8')
                print(ident,row['passed'],row['issues'],row.get('decoded_frames'),row.get('normalized_vcl'),flush=True)
    print(json.dumps(results['summary'],indent=2),flush=True)

if __name__ == '__main__':
    try:
        main()
    finally:
        POOL.shutdown(wait=True,cancel_futures=True)
