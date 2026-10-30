"""Test whether AV1 permits a mid-epoch end cut without re-encoding its tail.

This is a component experiment (RAP start), not an arbitrary-start smart cutter.
It uses FFmpeg stream copy and verifies every copied sample and displayed pixel.
"""
import time
from common import *
from archive import portable


def main():
    manifest = json.loads((ROOT/'evidence/fixtures.json').read_text(encoding='utf-8'))
    folder = ROOT/'test-data'/('experiments-tail-copy-'+time.strftime('%Y%m%d-%H%M%S'))
    folder.mkdir(parents=True)
    result = {'range': [24, 227], 'cases': []}
    for name, fixture in manifest['fixtures'].items():
        src = source_data(ROOT/fixture['path'])
        assert src['sha256'] == fixture['sha256'] and src['infos'][24]['sync']
        out = folder/(name+'.mp4')
        tb, t = Fraction(src['stream']['time_base']), src['times']
        command = [FF, '-v', 'error', '-i', src['path'],
                   '-ss', f'{float((t[24]-t[0])*tb):.12f}',
                   '-t', f'{float((t[227]-t[24])*tb):.12f}',
                   '-map', '0:v:0', '-c:v', 'copy', out]
        row = {'fixture': name, 'input_sha256': src['sha256'],
               'command': list(map(str, command)), 'output': out.relative_to(ROOT).as_posix()}
        try:
            p = run(command, check=False)
            row['exit_code'] = p.returncode
            if p.returncode:
                raise ValueError(p.stderr.decode(errors='replace'))
            row.update(verify(src, out, [(24, 227)]))
            s, packets = probe(out)
            row['all_samples_equal'] = packets_bytes(out, packets) == src['raw'][24:227]
            row['all_pixels_equal'] = bool(np.array_equal(decode(out, s), src['frames'][24:227]))
            row['passed'] = row['passed'] and row['all_samples_equal'] and row['all_pixels_equal']
        except Exception as e:
            row.update(passed=False, error=str(e))
        result['cases'].append(row)
        print(name, row['passed'], row.get('issues'), flush=True)
    result['summary'] = {'total': len(result['cases']), 'passed': sum(r['passed'] for r in result['cases'])}
    save(folder/'results.json', result)
    save(ROOT/'evidence/tail-copy.json', portable(result))


if __name__ == '__main__':
    main()
