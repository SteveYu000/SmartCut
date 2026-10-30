"""Production AV1 matrix using the pinned research media and independent oracle.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import argparse
import sys
import time
from support import ROOT, SDK

sys.path.insert(0, str(ROOT/'research/av1/tests'))
import os
os.environ['FFMPEG_ROOT'] = str(SDK)
from common import *
from upstream import CUTS
import common as oracle

# common.ROOT is the research directory; product paths stay explicit.
PRODUCT = Path(__file__).resolve().parents[1]


def video_sample_tables(path, details=False):
    """Extend the single-track research box oracle to video+audio/subtitles."""
    data = Path(path).read_bytes()
    tables, configs, descriptions = {}, [], []
    def descend(a, b):
        for kind, s, e in boxes(data, a, b):
            if kind == b'trak':
                handlers = [data[h+8:h+12] for tag, m, n in boxes(data, s, e) if tag == b'mdia'
                            for child, h, z in boxes(data, m, n) if child == b'hdlr']
                if handlers == [b'vide']:
                    descend(s, e)
            elif kind in (b'moov', b'mdia', b'minf', b'stbl'):
                descend(s, e)
            elif kind == b'stsd':
                for tag, v, z in boxes(data, s+8, e):
                    if tag != b'av01':
                        continue
                    desc = dict(zip(('width', 'height'), struct.unpack_from('>HH', data, v+24)))
                    for child, k, l in boxes(data, v+78, z):
                        if child == b'av1C':
                            configs.append(data[k:l])
                        elif child == b'colr' and data[k:k+4] == b'nclx':
                            desc['colr'] = dict(zip(('color_primaries', 'transfer_characteristics', 'matrix_coefficients'),
                                                   struct.unpack_from('>HHH', data, k+4)))
                            desc['colr']['color_range'] = data[k+10] >> 7
                    descriptions.append(desc)
            elif kind in (b'stsc', b'stco', b'co64', b'ctts'):
                tables[kind] = data[s:e]
    descend(0, len(data))
    mapping = []
    d = tables[b'stsc']
    entries = [struct.unpack_from('>III', d, 8+i*12) for i in range(struct.unpack_from('>I', d, 4)[0])]
    chunks = tables.get(b'stco', tables.get(b'co64'))
    count = struct.unpack_from('>I', chunks, 4)[0]
    for i, (first, n, desc) in enumerate(entries):
        last = entries[i+1][0] if i+1 < len(entries) else count+1
        mapping.extend([desc-1]*((last-first)*n))
    assert configs and mapping and all(0 <= n < len(configs) for n in mapping)
    result = (configs, mapping, b'ctts' in tables)
    return (*result, descriptions) if details else result


oracle.sample_tables = video_sample_tables


def check_product(src, out, ranges, plan):
    result = verify(src, out, ranges)
    s, packets = probe(out)
    raw, pixels = packets_bytes(out, packets), decode(out, s)
    wanted = [i for a, b in ranges for i in range(a, b)]
    copy = set()
    keys = [i for i, info in enumerate(src['infos']) if info['sync']]
    for a, b in ranges:
        entry = next((k for k in keys if k >= a), b)
        copy.update(range(entry, b))
    errors = []
    for output_index, source_index in enumerate(wanted):
        if source_index in copy:
            if output_index >= len(raw) or raw[output_index] != src['raw'][source_index]:
                errors.append([output_index, source_index, 'packet'])
            if output_index >= len(pixels) or not np.array_equal(pixels[output_index], src['frames'][source_index]):
                errors.append([output_index, source_index, 'pixels'])
    if errors:
        result['issues'].append('av1_copy_prefix')
    if plan['copied_frames'] != len(copy) or plan['encoded_frames'] != len(wanted)-len(copy):
        result['issues'].append('av1_minimal_head_plan')
    if plan['decode_delay_ticks'] != 0 or plan['signed_composition_offsets']:
        result['issues'].append('av1_decode_schedule')
    result.update(passed=not result['issues'], optimized_copy_frames=len(copy), copy_errors=errors, plan=plan)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--names', default='aom,svt,rav1e,main10,vfr,ntsc,offset,fragmented,color_sar,tiles,nvenc')
    ap.add_argument('--cuts', default=','.join(CUTS))
    ap.add_argument('--encoder', default='libaom-av1', choices=['libaom-av1', 'libsvtav1', 'librav1e', 'av1_nvenc'])
    args = ap.parse_args()
    exe = PRODUCT/'build/Release/smartcut.exe'
    folder = PRODUCT/'research/product-validation'/('av1-'+time.strftime('%Y%m%d-%H%M%S'))
    folder.mkdir(parents=True)
    manifest = json.loads((ROOT/'evidence/fixtures.json').read_text(encoding='utf-8'))['fixtures']
    result = {'executable_sha256': sha(exe), 'encoder': args.encoder, 'cases': []}
    settings = {
        'libaom-av1': ['-cpu-used', '8', '-crf', '18', '-threads', '4'],
        'libsvtav1': ['-preset', '8', '-crf', '18', '-svtav1-params', 'lp=4'],
        'librav1e': ['-speed', '8', '-qp', '60', '-threads', '4'],
        'av1_nvenc': ['-preset', 'p4', '-rc', 'constqp', '-qp', '18'],
    }
    for name in args.names.split(','):
        source = source_data(ROOT/manifest[name]['path'])
        assert source['sha256'] == manifest[name]['sha256'], 'Research fixture changed'
        for label in args.cuts.split(','):
            ranges = CUTS[label]
            out = folder/f'{name}-{label}.mp4'
            report = out.with_suffix('.json')
            command = [exe, '-hide_banner', '-loglevel', 'error', '-i', source['path'], '-an', '-sn',
                       '-c:v:0', args.encoder, *settings[args.encoder], '--report', report]
            for a, b in ranges:
                command += ['--keep-frames', f'{a}:{b}']
            command += [out]
            row = {'fixture': name, 'cut': label, 'ranges': ranges, 'input_sha256': source['sha256'],
                   'command': list(map(str, command)), 'output': str(out.relative_to(PRODUCT))}
            try:
                p = run(command, check=False)
                out.with_suffix('.log').write_bytes(p.stderr)
                row['exit_code'] = p.returncode
                if p.returncode:
                    row.update(passed=False, issues=['process_exit'], error=p.stderr.decode(errors='replace')[-3500:])
                else:
                    plan = json.loads(report.read_text(encoding='utf-8'))
                    row.update(check_product(source, out, ranges, plan))
            except Exception as e:
                row.update(passed=False, issues=['validation_error'], error=str(e))
            result['cases'].append(row)
            result['summary'] = {'total': len(result['cases']), 'passed': sum(r['passed'] for r in result['cases'])}
            save(folder/'results.json', result)
            print(name, label, row['passed'], row.get('issues'), row.get('error', '')[-500:], flush=True)
    print(folder, result['summary'], flush=True)
    return 0 if all(r['passed'] for r in result['cases']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
