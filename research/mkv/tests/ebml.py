"""Read-only, independent EBML audit (not a production muxer).
SPDX-License-Identifier: GPL-3.0-or-later
"""
from pathlib import Path
import hashlib
import struct


def vint(data, pos, identifier=False):
    first = data[pos]
    if not first:
        raise ValueError('zero EBML VINT')
    n = 1
    while not first & (128 >> (n - 1)):
        n += 1
    if n > 8 or pos + n > len(data):
        raise ValueError('invalid EBML VINT')
    value = int.from_bytes(data[pos:pos+n], 'big')
    if not identifier:
        value &= (1 << (7*n))-1
    return value, n


def elements(data, start=0, end=None):
    end = len(data) if end is None else end
    while start < end:
        ident, a = vint(data, start, True)
        size, b = vint(data, start+a)
        content = start+a+b
        stop = end if size == (1 << (7*b))-1 else content+size
        if stop > end or stop < content:
            raise ValueError('invalid EBML element extent')
        yield ident, content, stop, start
        start = stop


def audit(path):
    data = Path(path).read_bytes()
    result = {'timestamp_scale_ns':1000000, 'codec_states':0, 'cues':[], 'tracks':[],
              'blocks':[], 'cluster_count':0, 'block_durations':0, 'discard_padding':[],
              'attachments':0, 'chapters':0, 'segment_count':0}
    for ident, seg, end, _ in elements(data):
        if ident != 0x18538067:
            continue
        result['segment_count'] += 1
        for kind, a, b, _ in elements(data, seg, end):
            if kind == 0x1549A966:
                for k, c, d, _ in elements(data, a, b):
                    if k == 0x2AD7B1:
                        result['timestamp_scale_ns'] = int.from_bytes(data[c:d], 'big')
                    if k == 0x4489:
                        result['duration_ticks'] = struct.unpack('>d' if d-c==8 else '>f', data[c:d])[0]
            elif kind == 0x1654AE6B:
                for k, c, d, _ in elements(data, a, b):
                    if k != 0xAE: continue
                    track = {}
                    for k2, x, y, _ in elements(data, c, d):
                        if k2 in (0xD7,0x83,0x23E383,0x56AA,0x56BB):
                            track[{0xD7:'number',0x83:'type',0x23E383:'default_duration_ns',
                                   0x56AA:'codec_delay_ns',0x56BB:'seek_preroll_ns'}[k2]] = int.from_bytes(data[x:y],'big')
                        elif k2 == 0x86:
                            track['codec_id'] = data[x:y].decode()
                        elif k2 == 0x63A2:
                            track['codec_private'] = data[x:y]
                    result['tracks'].append(track)
            elif kind == 0x1C53BB6B:
                for k, c, d, _ in elements(data, a, b):
                    if k != 0xBB: continue
                    cue = {}
                    for k2, x, y, _ in elements(data, c, d):
                        if k2 == 0xB3: cue['time'] = int.from_bytes(data[x:y],'big')
                        if k2 == 0xB7:
                            for k3, xx, yy, _ in elements(data,x,y):
                                if k3 in (0xF1,0xF0,0xEA):
                                    cue[{0xF1:'cluster',0xF0:'relative',0xEA:'codec_state'}[k3]] = int.from_bytes(data[xx:yy],'big')
                    result['cues'].append(cue)
            elif kind == 0x1F43B675:
                result['cluster_count'] += 1
                cluster_time = 0
                children = list(elements(data,a,b))
                for k,c,d,_ in children:
                    if k == 0xE7: cluster_time = int.from_bytes(data[c:d],'big')
                for k,c,d,_ in children:
                    block = None; duration = None; references = []
                    if k == 0xA3: block = (c,d)
                    if k == 0xA0:
                        for k2,x,y,_ in elements(data,c,d):
                            if k2 == 0xA1: block = (x,y)
                            elif k2 == 0x9B:
                                duration = int.from_bytes(data[x:y],'big'); result['block_durations'] += 1
                            elif k2 == 0xFB: references.append(int.from_bytes(data[x:y],'big',signed=True))
                            elif k2 == 0xA4: result['codec_states'] += 1
                            elif k2 == 0x75A2: result['discard_padding'].append(int.from_bytes(data[x:y],'big',signed=True))
                    if block:
                        x,y = block; track,n = vint(data,x); x += n
                        ts = cluster_time + int.from_bytes(data[x:x+2],'big',signed=True)
                        flags = data[x+2]
                        result['blocks'].append(dict(track=track,pts=ts,duration=duration,
                            key=bool(flags&128) if k==0xA3 else not references,
                            lacing=(flags>>1)&3,payload=data[x+3:y]))
            elif kind == 0x1941A469: result['attachments'] += 1
            elif kind == 0x1043A770: result['chapters'] += 1
    return result


def summary(value):
    out = {k:v for k,v in value.items() if k not in ('blocks','tracks','cues')}
    out['cue_count'] = len(value['cues'])
    out['block_count'] = len(value['blocks'])
    out['laced_blocks'] = sum(bool(x['lacing']) for x in value['blocks'])
    out['tracks'] = [{k:v for k,v in t.items() if k!='codec_private'} |
                     {'codec_private_sha256':hashlib.sha256(t.get('codec_private',b'')).hexdigest()}
                     for t in value['tracks']]
    return out
