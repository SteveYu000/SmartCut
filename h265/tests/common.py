"""Independent output oracle: container samples, decoded pixels, and rational time.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
from collections import Counter
from fractions import Fraction
import numpy as np

W, H, N = 192, 112, 240
ROOT = Path(__file__).resolve().parents[1]
_candidates = list((ROOT/'deps').glob('ffmpeg-*-win64-gpl-shared-8.1'))
_candidates += list((ROOT.parent/'h264/deps').glob('ffmpeg-*-win64-gpl-shared-8.1'))
# Legacy workspace location is a convenience, never required for a fresh clone.
_candidates += list((ROOT.parents[1]/'h264-smart-render/deps').glob('ffmpeg-*-win64-gpl-shared-8.1'))
SDK = Path(os.environ['FFMPEG_ROOT']) if 'FFMPEG_ROOT' in os.environ else (_candidates[0] if _candidates else ROOT/'deps/SDK-not-installed')
FFMPEG, FFPROBE = SDK / 'bin/ffmpeg.exe', SDK / 'bin/ffprobe.exe'

def process(cmd, data=None, ok=True, **kw):
    r = subprocess.run([str(x) for x in cmd], input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180, **kw)
    if ok and r.returncode:
        raise RuntimeError(f'Exit {r.returncode}: {cmd}\n{r.stderr.decode(errors="replace")[-4000:]}')
    return r

def probe(path):
    d = json.loads(process([FFPROBE, '-v', 'error', '-show_streams', '-show_packets', '-of', 'json', path]).stdout)
    s = next(s for s in d['streams'] if s['codec_type'] == 'video')
    return s, [p for p in d['packets'] if p['stream_index'] == s['index']]

def decode(path, stream, strict=True, before=()):
    pix = 'yuv420p10le' if '10' in stream['pix_fmt'] else 'yuv420p'
    cmd = [FFMPEG, '-v', 'error'] + (['-xerror', '-err_detect', 'explode'] if strict else [])
    # Pixel extraction has its own synthetic clock. Real decoded timestamps are
    # checked separately, so a rawvideo mux warning is never called a codec error.
    r = process(cmd + list(before) + ['-i', path, '-map', '0:v:0', '-vf', 'setpts=N/25/TB', '-enc_time_base', '1/25', '-fps_mode', 'passthrough', '-pix_fmt', pix, '-f', 'rawvideo', '-'], ok=False)
    if strict and (r.returncode or r.stderr.strip()):
        raise AssertionError('Strict decode: ' + r.stderr.decode(errors='replace')[-2000:])
    size = int(stream['width']) * int(stream['height']) * 3 // 2
    a = np.frombuffer(r.stdout, dtype=np.uint16 if '10' in pix else np.uint8)
    if len(a) % size:
        raise AssertionError('Partial decoded frame')
    return a.reshape((-1, size)), r

def boxes(raw, start=0, end=None):
    end = len(raw) if end is None else end
    while start + 8 <= end:
        size, kind = struct.unpack_from('>I4s', raw, start); header = 8
        if size == 1:
            size = struct.unpack_from('>Q', raw, start+8)[0]; header = 16
        elif size == 0:
            size = end-start
        if size < header or start+size > end:
            raise AssertionError('Invalid MP4 box')
        yield kind, start+header, start+size
        start += size

def configs(path):
    raw = Path(path).read_bytes()
    def descend(a, b):
        for kind, begin, end in boxes(raw, a, b):
            if kind == b'stsd':
                for tag, v, e in boxes(raw, begin+8, end):
                    if tag in (b'hev1', b'hvc1'):
                        for k, c, ce in boxes(raw, v+78, e):
                            if k == b'hvcC':
                                yield tag.decode(), raw[c:ce]
            elif kind in (b'moov', b'trak', b'mdia', b'minf', b'stbl'):
                yield from descend(begin, end)
    return list(descend(0, len(raw)))

def nals(raw, length=4):
    pos = 0
    while pos < len(raw):
        n = int.from_bytes(raw[pos:pos+length], 'big'); pos += length
        if n < 2 or pos+n > len(raw):
            raise AssertionError('Invalid NAL framing')
        yield pos, raw[pos:pos+n]
        pos += n

def samples(path, packets):
    raw = Path(path).read_bytes(); length = (configs(path)[0][1][21] & 3)+1
    result = []
    for p in packets:
        part = raw[int(p['pos']):int(p['pos'])+int(p['size'])]
        vcl, normalized, types = b'', b'', []
        for off, nal in nals(part, length):
            typ = (nal[0] >> 1) & 63
            if typ < 32:
                types.append(typ); vcl += struct.pack('>I', len(nal))+nal
                # Only CRA->BLA_N_LP is allowed to change in our splice path.
                norm = bytes([(nal[0] & 0x81) | (21 << 1)])+nal[1:] if typ == 18 else nal
                normalized += struct.pack('>I', len(norm))+norm
        result.append({'hash': hashlib.sha256(vcl).hexdigest(), 'normalized': hashlib.sha256(normalized).hexdigest(), 'types': types})
    return result

def rgb_frames(count=N):
    y, x = np.mgrid[:H, :W]; frames=[]
    for i in range(count):
        im=np.stack(((x*3+y+i*5)%256, (y*5+i*3)%256, ((x^y)*2+i*7)%256), axis=2).astype(np.uint8)
        im[40:64, (i*3)%(W-25):(i*3)%(W-25)+25]=[255,30,180]
        for bit in range(10):
            im[2:18, 4+bit*16:18+bit*16] = 224 if (i>>bit)&1 else 32
        frames.append(im.tobytes())
    return b''.join(frames)

BASE='keyint=24:min-keyint=24:scenecut=0:bframes=3:b-adapt=0:ref=4:pools=4:frame-threads=2:log-level=error'
SPECS={
    'closed': ('open-gop=0', [], 'yuv420p', '25'),
    'open': ('open-gop=1', [], 'yuv420p', '25'),
    'main10': ('open-gop=1', [], 'yuv420p10le', '25'),
    'vfr': ('open-gop=1', ['-vf','setpts=(N+floor(N/3))/(25*TB)', '-fps_mode','vfr'], 'yuv420p', '25'),
    'ntsc': ('open-gop=1', [], 'yuv420p', '30000/1001'),
    'offset': ('open-gop=1', ['-output_ts_offset','2'], 'yuv420p', '25'),
    'fragmented': ('open-gop=1', ['-movflags','+frag_keyframe+empty_moov+default_base_moof'], 'yuv420p', '25'),
    'multislice': ('open-gop=1:slices=2', [], 'yuv420p', '25'),
    'color_sar': ('open-gop=1', ['-vf','setsar=4/3','-color_range','pc','-colorspace','bt709','-color_primaries','bt709','-color_trc','bt709'], 'yuv420p', '25'),
    'radl': ('open-gop=0:radl=2', [], 'yuv420p', '25'),
    'nvenc': ('open-gop=0', ['-c:v','hevc_nvenc','-preset','p4','-rc','constqp','-qp','18','-g','24','-bf','3'], 'yuv420p', '25'),
}

def fixture(folder, name, count=N):
    params, extra, pix, rate = SPECS[name]
    path = folder / ('source-'+name+'.mp4')
    process([FFMPEG,'-v','error','-f','rawvideo','-pixel_format','rgb24','-video_size',f'{W}x{H}', '-framerate',rate,'-i','-', '-an','-c:v','libx265','-preset','medium','-crf','14','-pix_fmt',pix,'-x265-params',BASE+':'+params,'-tag:v','hvc1',*extra,path], rgb_frames(count))
    return path

def source_data(path):
    s,p=probe(path); decoded,_=decode(path,s)
    order=sorted(range(len(p)), key=lambda i:int(p[i]['pts']))
    times=[int(p[i]['pts']) for i in order]
    times.append(times[-1]+int(p[order[-1]]['duration']))
    hashes=samples(path,p)
    return s,p,decoded,times,[hashes[i] for i in order]

def verify(source, output, ranges, allow_patch=False):
    ss,sp,expected,times,hs = source
    os_,op=probe(output); actual,r=decode(output,os_,strict=False)
    wanted=[i for a,b in ranges for i in range(a,b)]
    issues=[]; detail={'expected_frames':len(wanted),'decoded_frames':len(actual),'packets':len(op), 'exact_vcl':0, 'normalized_vcl':0, 'copy_pixel_mismatches':0, 'pixel_identical':0}
    if r.returncode or r.stderr.strip(): issues.append('decoder_error'); detail['decoder_log']=r.stderr.decode(errors='replace')[-2000:]
    if len(actual)!=len(wanted) or len(op)!=len(wanted): issues.append('frame_count')
    expected_pts=[]; cursor=Fraction(0); tb=Fraction(ss['time_base'])
    for a,b in ranges:
        expected_pts += [cursor+(times[i]-times[a])*tb for i in range(a,b)]
        cursor += (times[b]-times[a])*tb
    outpts=[int(p['pts'])*Fraction(os_['time_base']) for p in sorted(op,key=lambda p:int(p['pts']))]
    if outpts != expected_pts: issues.append('pts'); detail['first_pts']=str(outpts[0]); detail['pts_max_error']=float(max((abs(a-b) for a,b in zip(outpts,expected_pts)),default=0))
    fd=json.loads(process([FFPROBE,'-v','error','-select_streams','v:0','-show_frames','-show_entries','frame=pts,best_effort_timestamp','-of','json',output]).stdout)['frames']
    framepts=[int(f.get('pts',f.get('best_effort_timestamp',-1)))*Fraction(os_['time_base']) for f in fd]
    if framepts!=expected_pts:
        issues.append('decoded_pts'); detail['decoded_pts_mismatches']=sum(a!=b for a,b in zip(framepts,expected_pts))
    dts=[int(p['dts']) for p in op]
    if not all(a<b for a,b in zip(dts,dts[1:])) or not all(int(p['dts'])<=int(p['pts']) for p in op): issues.append('dts')
    if abs(Fraction(os_['duration'])-cursor)>Fraction(1,1000000): issues.append('duration')
    oh=samples(output,op); oh=[oh[i] for i in sorted(range(len(op)),key=lambda i:int(op[i]['pts']))]
    detail['vcl_multiset_preserved']=sum((Counter(x['hash'] for x in oh) & Counter(hs[i]['hash'] for i in wanted)).values())
    psnrs=[]; barcode_errors=[]; peak=1023 if expected.dtype==np.uint16 else 255
    for j,i in enumerate(wanted[:len(actual)]):
        identical=np.array_equal(actual[j],expected[i]); detail['pixel_identical']+=int(identical)
        exact = j<len(oh) and oh[j]['hash']==hs[i]['hash']; norm = j<len(oh) and oh[j]['normalized']==hs[i]['normalized']
        detail['exact_vcl']+=int(exact); detail['normalized_vcl']+=int(norm)
        if (exact or allow_patch and norm) and not identical: detail['copy_pixel_mismatches']+=1
        im=actual[j,:W*H].reshape(H,W)
        barcode=sum((int(im[4:16,6+bit*16:16+bit*16].mean()>peak/2)<<bit) for bit in range(10))
        if barcode != i: barcode_errors.append([j,i,barcode])
        if not identical:
            mse=np.square(actual[j].astype(float)-expected[i].astype(float)).mean()
            psnrs.append(10*np.log10(peak*peak/mse))
    detail['barcode_errors']=barcode_errors[:12]; detail['min_psnr']=min(psnrs,default=None)
    if barcode_errors: issues.append('frame_identity')
    if detail['copy_pixel_mismatches']: issues.append('copied_pixels')
    if psnrs and min(psnrs)<30: issues.append('quality_below_30dB')
    detail['issues']=issues; detail['passed']=not issues
    return detail
