"""Read only Sequence Header fields needed to audit the public av1C contract.

Syntax follows AV1 spec 5.5.1/5.5.2 (also cross-checked against FFmpeg CBS).
This independent test parser is not linked into the cutting implementation.
SPDX-License-Identifier: GPL-3.0-or-later
"""
class Bits:
    def __init__(self,data): self.data=data; self.pos=0
    def read(self,n=1):
        if self.pos+n>8*len(self.data): raise ValueError('Truncated sequence header')
        v=0
        for _ in range(n):
            v=v*2+((self.data[self.pos//8]>>(7-self.pos%8))&1); self.pos+=1
        return v
    def uvlc(self):
        n=0
        while not self.read():
            n+=1
            if n>=32: return (1<<32)-1
        return (1<<n)-1+self.read(n)

def sequence_fields(payload):
    b=Bits(payload); profile=b.read(3); still=b.read(); reduced=b.read()
    model=0; delay=0; buffer_bits=0; levels=[]; tiers=[]; ops=[]
    if reduced: levels=[b.read(5)]; tiers=[0]; ops=[0]
    else:
        if b.read():
            b.read(32); b.read(32)
            if b.read(): b.uvlc()
            model=b.read()
            if model:
                buffer_bits=b.read(5)+1; b.read(32); b.read(5); b.read(5)
        delay=b.read(); count=b.read(5)+1
        for _ in range(count):
            ops.append(b.read(12)); level=b.read(5); levels.append(level); tiers.append(b.read() if level>7 else 0)
            if model and b.read(): b.read(buffer_bits*2+1)
            if delay and b.read(): b.read(4)
    wb=b.read(4)+1; hb=b.read(4)+1; width=b.read(wb)+1; height=b.read(hb)+1
    if not reduced and b.read(): b.read(7)
    b.read(3)
    if not reduced:
        b.read(4); order_hint=b.read()
        if order_hint: b.read(2)
        screen=2 if b.read() else b.read()
        if screen and not b.read(): b.read()
        if order_hint: b.read(3)
    b.read(3); high=b.read(); twelve=b.read() if profile==2 and high else 0
    depth=12 if twelve else 10 if high else 8
    mono=0 if profile==1 else b.read()
    prim,trc,matrix=(b.read(8),b.read(8),b.read(8)) if b.read() else (2,2,2)
    position=0
    if mono:
        full=b.read(); sx=sy=1
    elif (prim,trc,matrix)==(1,13,0):
        full=1; sx=sy=0; b.read()
    else:
        full=b.read()
        if profile==0: sx=sy=1
        elif profile==1: sx=sy=0
        elif depth==12:
            sx=b.read(); sy=b.read() if sx else 0
        else: sx,sy=1,0
        if sx and sy: position=b.read(2)
        b.read()
    grain=b.read()
    return {'seq_profile':profile,'seq_level_idx_0':levels[0],'seq_tier_0':tiers[0],
            'high_bitdepth':high,'twelve_bit':twelve,'monochrome':mono,'chroma_subsampling_x':sx,
            'chroma_subsampling_y':sy,'chroma_sample_position':position,'width':width,'height':height,
            'color_primaries':prim,'transfer_characteristics':trc,'matrix_coefficients':matrix,
            'color_range':full,'film_grain_params_present':grain,'operating_points':ops,'still_picture':still}

def config_fields(data):
    if len(data)<4 or data[0]!=0x81: raise ValueError('Invalid av1C')
    return {'seq_profile':data[1]>>5,'seq_level_idx_0':data[1]&31,'seq_tier_0':data[2]>>7,
            'high_bitdepth':(data[2]>>6)&1,'twelve_bit':(data[2]>>5)&1,'monochrome':(data[2]>>4)&1,
            'chroma_subsampling_x':(data[2]>>3)&1,'chroma_subsampling_y':(data[2]>>2)&1,
            'chroma_sample_position':data[2]&3}
