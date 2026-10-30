"""AV1 options, timed cuts, auxiliary streams, seeks and hardware integration.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import argparse
import traceback
from av1 import *
from fixtures import AOM


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--skip-hardware', action='store_true')
    args = ap.parse_args()
    exe = PRODUCT/'build/Release/smartcut.exe'
    folder = PRODUCT/'research/product-validation'/('av1-'+time.strftime('%Y%m%d-%H%M%S')+'-features')
    folder.mkdir(parents=True)
    result = {'executable_sha256': sha(exe), 'cases': [], 'generated_fixtures': []}
    sources = {}
    manifest = json.loads((ROOT/'evidence/fixtures.json').read_text(encoding='utf-8'))['fixtures']
    def source(name):
        if name not in sources:
            sources[name] = source_data(ROOT/manifest[name]['path'])
            assert sources[name]['sha256'] == manifest[name]['sha256']
        return sources[name]
    def emit(name, test):
        row = {'case': name}
        try:
            row.update(test())
            row['passed'] = True
        except Exception:
            row.update(passed=False, error=traceback.format_exc())
        result['cases'].append(row)
        result['summary'] = {'total': len(result['cases']), 'passed': sum(r['passed'] for r in result['cases'])}
        save(folder/'results.json', result)
        print(name, row['passed'], row.get('error', '')[-600:], flush=True)
    def export(name, src, extra, encoder='libaom-av1'):
        out = folder/(name+'.mp4')
        report = out.with_suffix('.json')
        settings = ['-cpu-used', '8', '-crf', '18', '-threads', '4'] if encoder == 'libaom-av1' else ['-preset', '8', '-crf', '18', '-svtav1-params', 'lp=4']
        cmd = [exe, '-hide_banner', '-loglevel', 'error', '-i', src['path'], '-c:v', encoder,
               *settings, *extra, '--report', report, out]
        p = run(cmd, check=False)
        out.with_suffix('.log').write_bytes(p.stderr)
        assert p.returncode == 0, p.stderr.decode(errors='replace')
        return out, json.loads(report.read_text(encoding='utf-8')), list(map(str, cmd))
    def clip(name):
        src = source(name)
        out, plan, cmd = export('seek-'+name, src, ['-an', '-sn', '--keep-frames', '13:227'])
        checked = check_product(src, out, [(13, 227)], plan)
        assert checked['passed'], checked['issues']
        return out, checked, cmd
    for name in ('svt', 'rav1e', 'main10', 'vfr', 'nvenc'):
        out, checked, cmd = clip(name)
        def seek_check(out=out, checked=checked, cmd=cmd):
            s, packets = probe(out)
            pixels = decode(out, s)
            assert np.array_equal(pixels, decode(out, s, 'libaom-av1'))
            keys = [i for i, p in enumerate(packets) if 'K' in p['flags']]
            tests = sorted(set([0, keys[1], keys[-1]]))
            for i in tests:
                seconds = int(packets[i]['pts'])*Fraction(s['time_base'])
                assert np.array_equal(pixels[i:], decode(out, s, before=['-ss', f'{float(seconds):.12f}']))
            return {'command': cmd, 'validation': checked, 'seek_samples': tests, 'second_decoder_equal': True}
        emit('seek-and-second-decoder-'+name, seek_check)
    for name, begin, end in [('aom', '0.501', '0.962'), ('vfr', '0.53', '1.173'),
                             ('ntsc', '0.37', '2.71'), ('offset', '0.501', '1.173')]:
        def timed(name=name, begin=begin, end=end):
            src = source(name)
            out, plan, cmd = export('timed-'+name, src, ['-an', '-sn', '-ss', begin, '-to', end])
            s, packets = probe(out)
            pixels = decode(out, s)
            tb = Fraction(src['stream']['time_base'])
            origin = src['times'][0]*tb
            a, b = origin+Fraction(begin), origin+Fraction(end)
            wanted = [i for i in range(N) if a <= src['times'][i]*tb < b]
            rounded = lambda q: (q.numerator*2+q.denominator)//(2*q.denominator)
            start = rounded(a/tb)*tb
            times = [(src['times'][i]*tb-start) for i in wanted]
            ot = Fraction(s['time_base'])
            assert [int(p['pts'])*ot for p in packets] == times
            assert all(p['pts'] == p['dts'] for p in packets)
            expected_end = min(src['times'][wanted[-1]+1]*tb, rounded(b/tb)*tb)-start
            assert (int(packets[-1]['pts'])+int(packets[-1]['duration']))*ot == expected_end
            assert len(pixels) == len(wanted)
            for j, i in enumerate(wanted):
                width, height = int(s['width']), int(s['height'])
                lum = pixels[j, :width*height].reshape(height, width)
                code = sum(int(lum[5:15, 7+k*16:15+k*16].mean() > 127.5) << k for k in range(10))
                assert code == i, (j, i, code)
            assert not video_sample_tables(out)[2]
            return {'input_sha256': src['sha256'], 'output_sha256': sha(out), 'command': cmd,
                    'first_pts': str(times[0]), 'end': str(expected_end), 'frames': len(wanted), 'plan': plan}
        emit('timed-'+name, timed)
    def negative(name, flags, diagnostic, overwrite=False):
        src = source('aom')
        out = folder/('reject-'+name+'.mp4')
        if overwrite:
            out.write_bytes(b'keep-existing-output')
        before = sha(out) if out.exists() else None
        bounds = [] if '--keep-frames' in flags else ['--keep-frames', '0:240']
        cmd = [exe, '-hide_banner', '-loglevel', 'error', '-i', src['path'], '-an', '-sn',
               *bounds, *flags, out]
        p = run(cmd, check=False)
        message = p.stderr.decode(errors='replace')
        assert p.returncode != 0 and diagnostic in message, message
        assert (sha(out) if out.exists() else None) == before
        return {'input_sha256': src['sha256'], 'command': list(map(str, cmd)), 'stderr': message, 'exit_code': p.returncode}
    for name, flags, diagnostic, keep in [
        ('codec', ['-c:v', 'libx264'], 'Video codec mismatch', False),
        ('default-codec', [], 'Video codec mismatch', False),
        ('tag', ['-c:v', 'libaom-av1', '-tag:v', 'avc1'], 'av01', False),
        ('option', ['-c:v', 'libaom-av1', '-preset', 'slower'], 'Unknown or unsupported', False),
        ('pixel-format', ['-c:v', 'libaom-av1', '-pix_fmt', 'yuv444p'], '-pix_fmt', False),
        ('protected-mismatch', ['-y', '-c:v', 'libx264'], 'Video codec mismatch', True),
        ('copy-partial', ['-c:v', 'copy', '--keep-frames', '13:227'], 'Frame-exact boundaries require', False),
    ]:
        emit('reject-'+name, lambda n=name, f=flags, d=diagnostic, k=keep: negative(n, f, d, k))
    def copy_tail():
        src = source('rav1e')
        out = folder/'copy-tail.mp4'
        report = out.with_suffix('.json')
        cmd = [exe, '-i', src['path'], '-an', '-sn', '-c:v', 'copy', '--keep-frames', '24:227', '--report', report, out]
        run(cmd)
        row = check_product(src, out, [(24, 227)], json.loads(report.read_text(encoding='utf-8')))
        assert row['passed'] and row['plan']['encoded_frames'] == 0
        return {'command': list(map(str, cmd)), 'validation': row}
    emit('copy-tail-without-encoder', copy_tail)
    def unicode():
        src = dict(source('aom'))
        path = folder/'中文 源.mp4'
        path.write_bytes(Path(src['path']).read_bytes())
        src['path'] = str(path)
        out, plan, cmd = export('中文 剪切', src, ['-an', '-sn', '--keep-frames', '13:24', '--keep-frames', '24:48'])
        row = check_product(src, out, [(13, 48)], plan)
        assert row['passed']
        return {'command': cmd, 'validation': row}
    emit('unicode-adjacent', unicode)
    for name, encoder, settings in [
        ('aom-default-crf', 'libaom-av1', ['-cpu-used', '8', '-threads', '4']),
        ('aom-lookahead', 'libaom-av1', ['-cpu-used', '6', '-crf', '24', '-lag-in-frames', '16', '-threads', '4']),
        ('aom-intra', 'libaom-av1', ['-cpu-used', '8', '-crf', '24', '-g', '1', '-threads', '4']),
        ('svt-options', 'libsvtav1', ['-preset', '8', '-crf', '24', '-svtav1-params', 'lp=4']),
    ]:
        def parity(name=name, encoder=encoder, settings=settings):
            src = source('aom')
            out, control = folder/(name+'.mp4'), folder/(name+'-ffmpeg.mp4')
            report = out.with_suffix('.json')
            cmd = [exe, '-hide_banner', '-loglevel', 'error', '-i', src['path'], '-an', '-sn',
                   '-c:v', encoder, *settings, '--keep-frames', '13:227', '--report', report, out]
            reference = [FF, '-v', 'error', '-i', src['path'], '-ss', '0.52', '-to', '0.96',
                         '-an', '-sn', '-c:v', encoder, *settings, control]
            run(cmd)
            run(reference)
            s, p = probe(out)
            _, q = probe(control)
            coded = lambda packets: [b''.join(o[2] for o in obus(data) if o[0] in (3, 4, 6, 7)) for data in packets]
            assert coded(packets_bytes(out, p)[:11]) == coded(packets_bytes(control, q))
            if name == 'aom-intra':
                assert all(packet_info(data)['sync'] for data in packets_bytes(out, p)[:11])
            return {'input_sha256': src['sha256'], 'output_sha256': sha(out), 'reference_sha256': sha(control),
                    'command': list(map(str, cmd)), 'reference_command': list(map(str, reference)),
                    'coded_boundary_payload_equal': True, 'plan': json.loads(report.read_text(encoding='utf-8'))}
        emit('ffmpeg-parity-'+name, parity)
    def changing_input():
        path = folder/'seek-rav1e.mp4'
        out = folder/'reject-changing-input.mp4'
        cmd = [exe, '-i', path, '-c:v', 'copy', '--keep-frames', '0:100', '-an', '-sn', out]
        p = run(cmd, check=False)
        message = p.stderr.decode(errors='replace')
        assert p.returncode != 0 and 'unsupported' in message and not out.exists(), message
        return {'input_sha256': sha(path), 'command': list(map(str, cmd)), 'stderr': message}
    emit('reject-dynamic-input', changing_input)
    captions = folder/'captions.srt'
    captions.write_text('1\n00:00:00,200 --> 00:00:01,400\nFirst\n\n2\n00:00:02,000 --> 00:00:04,500\nSecond\n', encoding='utf-8')
    def auxiliary(path):
        data = json.loads(run([FP, '-v', 'error', '-show_streams', '-show_packets', '-show_data_hash', 'sha256', '-of', 'json', path]).stdout)
        streams = []
        for s in data['streams']:
            if s['codec_type'] not in ('audio', 'subtitle'):
                continue
            row = {k: s.get(k) for k in ('codec_type', 'codec_name', 'sample_rate', 'channels', 'time_base', 'disposition')}
            row['language'] = s.get('tags', {}).get('language')
            row['packets'] = [{k: p.get(k) for k in ('pts', 'dts', 'duration', 'data_hash', 'side_data_list')}
                              for p in data.get('packets', []) if p['stream_index'] == s['index']]
            if s['codec_type'] == 'audio':
                pcm = run([FF, '-v', 'error', '-i', path, '-map', f"0:{s['index']}", '-f', 'f32le', '-']).stdout
                row['pcm_sha256'] = hashlib.sha256(pcm).hexdigest()
                row['pcm_bytes'] = len(pcm)
            streams.append(row)
        return streams
    for name in ('aom', 'vfr'):
        src = source(name)
        avs = folder/f'source-{name}-avs.mp4'
        cmd = [FF, '-v', 'error', '-i', src['path'], '-f', 'lavfi', '-i', 'sine=frequency=997:sample_rate=48000:duration=13',
               '-f', 'lavfi', '-i', 'sine=frequency=431:sample_rate=48000:duration=13', '-i', captions,
               '-map', '0:v', '-map', '1:a', '-map', '2:a', '-map', '3:s', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '128k', '-c:s', 'mov_text', avs]
        run(cmd)
        result['generated_fixtures'].append({'path': str(avs), 'sha256': sha(avs), 'command': list(map(str, cmd)), 'video_source_sha256': src['sha256']})
        for mode in ('copy', 'encode', 'audio-first'):
            def audio(name=name, src=src, avs=avs, mode=mode):
                output, control = folder/f'{name}-{mode}.mp4', folder/f'{name}-{mode}-ffmpeg.mp4'
                cut = ['-ss', '0.52', '-to', '9.08'] if name == 'aom' else ['-ss', '0.68', '-to', '12.08']
                mapping = ['-map', '0:v', '-map', '0:a', '-map', '0:s'] if mode != 'audio-first' else ['-map', '0:a:1', '-map', '0:v', '-map', '0:s', '-map', '0:a:0']
                auxmap = ['-map', '0:a', '-map', '0:s'] if mode != 'audio-first' else ['-map', '0:a:1', '-map', '0:s', '-map', '0:a:0']
                encoding = ['-c:a', 'copy', '-c:s', 'copy'] if mode == 'copy' else ['-c:a', 'aac', '-b:a', '192k', '-c:s', 'mov_text']
                product = [exe, '-hide_banner', '-loglevel', 'error', '-i', avs, *cut, *mapping,
                           '-c:v:0', 'libaom-av1', '-cpu-used', '8', '-crf', '18', '-threads', '4', *encoding, output]
                reference = [FF, '-v', 'error', '-i', avs, *cut, *auxmap, *encoding, control]
                run(product)
                run(reference)
                actual, expected = auxiliary(output), auxiliary(control)
                assert actual == expected, [(i, [k for k in a if a[k] != b.get(k)]) for i, (a, b) in enumerate(zip(actual, expected)) if a != b]
                validation = verify(src, output, [(13, 227)])
                assert validation['passed'], validation['issues']
                return {'input_sha256': sha(avs), 'command': list(map(str, product)), 'reference_command': list(map(str, reference)),
                        'auxiliary': actual, 'video_validation': validation}
            emit(f'audio-subtitles-{name}-{mode}', audio)
    if not args.skip_hardware:
        raw = rgb_frames(height=128)
        for name, pix, enc in [('aom', 'yuv420p', AOM), ('main10', 'yuv420p10le', AOM),
                               ('nvenc', 'yuv420p', ['-c:v', 'av1_nvenc', '-preset', 'p4', '-rc', 'constqp', '-qp', '18', '-g', '24'])]:
            def hardware(name=name, pix=pix, enc=enc):
                path = folder/f'hw-source-{name}.mp4'
                cmd = [FF, '-v', 'error', '-f', 'rawvideo', '-pixel_format', 'rgb24', '-video_size', '192x128', '-framerate', '25', '-i', '-', '-an', *enc, '-pix_fmt', pix, path]
                run(cmd, raw)
                src = source_data(path)
                out, plan, cut = export('hw-'+name, src, ['-an', '-sn', '--keep-frames', '13:227'], 'libsvtav1' if name != 'nvenc' else 'libaom-av1')
                validation = check_product(src, out, [(13, 227)], plan)
                assert validation['passed'], validation['issues']
                s, _ = probe(out)
                assert np.array_equal(decode(out, s), decode(out, s, 'av1_cuvid'))
                return {'input_sha256': src['sha256'], 'raw_rgb_sha256': hashlib.sha256(raw).hexdigest(),
                        'generation_command': list(map(str, cmd)), 'command': cut, 'validation': validation, 'hardware_pixels_equal': True}
            emit('nvdec-'+name, hardware)
    print(folder, result['summary'], flush=True)
    return 0 if all(r['passed'] for r in result['cases']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
