"""Keep stock FFmpeg's audio/subtitle policy and test its second MKV mux step.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import time
from common import *


def inspect(path):
    return json.loads(run([FP,'-v','error','-show_streams','-show_packets','-show_chapters',
                           '-show_data_hash','sha256','-of','json',path]).stdout)


def audio(path,index):
    return run([FF,'-v','error','-i',path,'-map',f'0:{index}','-f','f32le','-']).stdout


def main():
    folder=ROOT/'test-data'/('streams-'+time.strftime('%Y%m%d-%H%M%S'));folder.mkdir(parents=True)
    srt=folder/'captions.srt'
    srt.write_text('1\n00:00:00,200 --> 00:00:01,500\nFirst caption\n\n2\n00:00:03,000 --> 00:00:05,000\nMiddle caption\n\n3\n00:00:08,000 --> 00:00:09,500\nLast caption\n',encoding='utf-8')
    ass=folder/'captions.ass';run([FF,'-v','error','-y','-i',srt,ass])
    chapters=folder/'chapters.txt';chapters.write_text(';FFMETADATA1\ntitle=MKV auxiliary fixture\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=3000\ntitle=Part A\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=3000\nEND=9600\ntitle=Part B\n',encoding='utf-8')
    font=Path('C:/Windows/Fonts/consola.ttf')
    source_video=ROOT/'test-data/fixtures/h264-closed_b3_id0.mkv'
    media=folder/'audio-subtitle-source.mkv'
    command=[FF,'-v','error','-y','-i',source_video,'-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=9.6',
             '-i',srt,'-i',ass,'-i',chapters,'-map','0:v','-map','1:a','-map','1:a','-map','2:s','-map','3:s',
             '-c:v','copy','-c:a:0','aac','-b:a:0','128k','-c:a:1','libopus','-b:a:1','96k','-c:s','copy',
             '-map_metadata','4','-map_chapters','4','-attach',font,'-metadata:s:t','mimetype=application/x-truetype-font',
             '-metadata:s:a:0','language=eng','-metadata:s:a:1','language=jpn','-metadata:s:s:0','language=eng',
             '-metadata:s:s:1','language=chi','-avoid_negative_ts','disabled',media]
    run(command)
    video=folder/'smart-video.mkv'
    vcmd=[EXE,'-v','error','-y','-i',source_video,'--keep-frames','13:227','-c:v','libx264','-crf','18',video]
    run(vcmd)
    result={'ffmpeg':run([FF,'-version']).stdout.decode().splitlines()[0],
            'fixture_command':list(map(str,command)),'fixture_sha256':sha(media),
            'font_sha256':sha(font),'video_command':list(map(str,vcmd)),'prototype_sha256':sha(EXE),'cases':[]}
    for name,track,encoder in [('aac-copy',0,'copy'),('opus-copy',1,'copy'),('aac-encode',0,'aac'),
                                ('opus-encode',1,'libopus'),('flac-encode',0,'flac')]:
        aux=folder/(name+'-aux.mkv');out=folder/(name+'-combined.mkv')
        cmd=[FF,'-v','error','-y','-i',media,'-ss','0.52','-to','9.08','-map',f'0:a:{track}',
             '-map','0:s','-map','0:t','-map_metadata','0','-map_chapters','0','-c:a',encoder,'-c:s','copy','-c:t','copy',
             '-avoid_negative_ts','disabled',aux]
        if encoder=='libopus':cmd[-3:-3]=['-b:a','96k']
        run(cmd)
        combine=[FF,'-v','error','-y','-copyts','-i',video,'-i',aux,'-map','0:v','-map','1:a','-map','1:s','-map','1:t',
                 '-map_metadata','1','-map_chapters','1','-c','copy','-avoid_negative_ts','disabled',out]
        run(combine)
        a,b=inspect(aux),inspect(out);issues=[]
        astreams=[s for s in a['streams'] if s['codec_type']!='video']
        bstreams=[s for s in b['streams'] if s['codec_type']!='video']
        details=[]
        for sa,sb in zip(astreams,bstreams):
            fields=['codec_name','codec_type','sample_rate','channels','time_base','extradata_hash','tags','disposition']
            # DURATION tags are regenerated; compare the user-relevant labels.
            for key in fields:
                if key=='tags':
                    for tag in ('language','filename','mimetype'):
                        if sa.get('tags',{}).get(tag)!=sb.get('tags',{}).get(tag):issues.append('metadata_'+tag)
                elif sa.get(key)!=sb.get(key):issues.append('metadata_'+key)
            pa=[p for p in a['packets'] if p['stream_index']==sa['index']]
            pb=[p for p in b['packets'] if p['stream_index']==sb['index']]
            values=lambda packets:[{k:p.get(k) for k in ('pts','duration','data_hash','side_data_list')} for p in packets]
            equal=values(pa)==values(pb)
            if not equal:issues.append(sa['codec_type']+'_packet_timing_payload_or_side_data')
            detail=dict(codec=sa['codec_name'],packet_count=len(pa),packets_equal=equal)
            if sa['codec_type']=='audio':
                pcm_a,pcm_b=audio(aux,sa['index']),audio(out,sb['index'])
                detail['decoded_pcm_equal']=pcm_a==pcm_b;detail['decoded_samples']=len(pcm_a)//(4*sa['channels'])
                if pcm_a!=pcm_b:issues.append('audio_pcm')
            details.append(detail)
        if len(astreams)!=len(bstreams):issues.append('stream_count')
        if a.get('chapters')!=b.get('chapters'):issues.append('chapters')
        eb_a,eb_b=ebml.audit(aux),ebml.audit(out)
        if eb_a['discard_padding']!=eb_b['discard_padding']:issues.append('discard_padding')
        row=dict(name=name,passed=not issues,issues=sorted(set(issues)),auxiliary_command=list(map(str,cmd)),
                 combine_command=list(map(str,combine)),streams=details,auxiliary_ebml=ebml.summary(eb_a),
                 output_ebml=ebml.summary(eb_b),output_sha256=sha(out))
        result['cases'].append(row);save(folder/'results.json',result)
        print(name,row['passed'],row['issues'],flush=True)
    save(ROOT/'local/streams-latest.json',{'path':str(folder/'results.json')})
    print(folder,flush=True)


if __name__=='__main__':main()
