"""Small read-only MP4 timing box inspector.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import json, struct, sys
from pathlib import Path

def inspect(path):
    data=Path(path).read_bytes(); out={}
    def walk(start,end,prefix=''):
        while start+8<=end:
            size,kind=struct.unpack_from('>I4s',data,start);kind=kind.decode('ascii',errors='replace')
            header=8
            if size==1:size=struct.unpack_from('>Q',data,start+8)[0];header=16
            if size==0:size=end-start
            if size<header or start+size>end:break
            p=start+header;name=prefix+kind
            if kind in ('moov','trak','mdia','minf','stbl','edts'):walk(p,start+size,name+'/')
            if kind in ('mvhd','mdhd'):
                v=data[p];shift=20 if v else 12
                tb=struct.unpack_from('>I',data,p+shift)[0]
                dur=struct.unpack_from('>Q' if v else '>I',data,p+shift+4)[0]
                out[name]={'timescale':tb,'duration':dur,'seconds':dur/tb}
            if kind=='tkhd':
                v=data[p];dur=struct.unpack_from('>Q' if v else '>I',data,p+(28 if v else 20))[0]
                out[name]={'duration':dur}
            if kind=='elst':
                v=data[p];count=struct.unpack_from('>I',data,p+4)[0]
                out[name]={'entries':[struct.unpack_from('>Qq' if v else '>Ii',data,p+8+i*(20 if v else 12)) for i in range(count)]}
            if kind=='stts':
                count=struct.unpack_from('>I',data,p+4)[0]
                vals=[struct.unpack_from('>II',data,p+8+8*i) for i in range(count)]
                out[name]={'decode_duration':sum(n*d for n,d in vals),'last':vals[-3:]}
            start+=size
    walk(0,len(data));return out

if __name__=='__main__':
    for name in sys.argv[1:]:print(json.dumps({'file':name,'boxes':inspect(name)},indent=2))
