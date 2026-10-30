"""Remux the pinned, reproducible earlier research videos into native MKV inputs.
SPDX-License-Identifier: GPL-3.0-or-later
"""
from common import *

GROUPS={
    'h264':['closed_b3_id0','closed_b3_id3','open_gop','mixed_open_idr','high10','ntsc','vfr','fullrange_sar'],
    'hevc':['closed','open','main10','vfr','ntsc','radl','color_sar'],
    'av1':['aom','svt','main10','vfr','ntsc','rav1e','color_sar']}


def main():
    folder=ROOT/'test-data/fixtures'; folder.mkdir(parents=True,exist_ok=True)
    original=json.loads((REPO/'research/product-validation/evidence/source-fixtures.json').read_text())
    av1=json.loads((REPO/'research/av1/evidence/fixtures.json').read_text())
    evidence={'ffmpeg_version':run([FF,'-version']).stdout.decode().splitlines()[0],
              'ffmpeg_sha256':sha(FF),'fixtures':{}}
    for codec,names in GROUPS.items():
        for name in names:
            if codec=='h264': path=REPO/'research/h264/test-data/run-20261027-125923'/f'source-{name}.mp4'
            elif codec=='hevc': path=REPO/'research/h265/test-data/fixtures'/f'source-{name}.mp4'
            else: path=REPO/'research/av1/test-data/fixtures'/f'source-{name}.mp4'
            expected=av1['fixtures'][name]['sha256'] if codec=='av1' else next(
                x['sha256'] for x in original['fixtures'] if x['codec']==('h265' if codec=='hevc' else codec) and x['name']==name)
            assert sha(path)==expected, f'Pinned source changed: {path}'
            key=f'{codec}-{name}'; out=folder/(key+'.mkv')
            cmd=[FF,'-v','error','-y','-copyts','-i',path,'-map','0:v:0','-c','copy','-fflags','+bitexact',
                 '-avoid_negative_ts','disabled',out]
            run(cmd)
            src=source(path); mkv=source(out)
            assert len(src['packets'])==len(mkv['packets'])==240
            assert all(a['raw']==b['raw'] for a,b in zip(src['packets'],mkv['packets']))
            assert np.array_equal(src['frames'],mkv['frames'])
            error=max(abs(a-b) for a,b in zip(src['times'][:-1],mkv['times'][:-1]))
            assert error<=Fraction(1,2000)
            evidence['fixtures'][key]={'source_path':path.relative_to(REPO).as_posix(),
                'source_sha256':expected,'path':out.relative_to(ROOT).as_posix(),'sha256':sha(out),
                'remux_command':list(map(str,cmd)),'payload_and_pixels_identical':True,
                'max_remux_pts_error_ms':float(error*1000),'codec':codec,'source_name':name,
                'time_base':mkv['stream']['time_base'],'ebml':ebml.summary(ebml.audit(out)),
                'regeneration':'tests/prepare_fixtures.py' if codec!='av1' else 'research/av1/tests/fixtures.py'}
            save(ROOT/'evidence/fixtures.json',evidence)
            print(key,'payload/pixels exact',float(error*1000),'ms',flush=True)


if __name__=='__main__': main()
