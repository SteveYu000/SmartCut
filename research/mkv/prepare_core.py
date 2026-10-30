"""Apply the small MKV input adapter to build-only copies of the existing core.
SPDX-License-Identifier: GPL-3.0-or-later

Exact anchors fail closed when the product changes. Generated sources, unified
diffs and hashes make the reused implementation independently reviewable.
"""
import difflib
import hashlib
import json
from pathlib import Path
import sys

repo, target = map(Path, sys.argv[1:])
target.mkdir(parents=True, exist_ok=True)
manifest = {}
for name in ('video.cpp', 'av1.cpp'):
    original = (repo / 'src' / name).read_text(encoding='utf-8')
    text = original

    def replace(before, after):
        global text
        if text.count(before) != 1:
            raise RuntimeError(f'{name}: research adapter anchor changed: {before!r}')
        text = text.replace(before, after)

    replace('#include "media.hpp"', '#include "mkv_input.hpp"')
    replace('av_read_frame(input.format.value, p.get())', 'research_read_packet(input, p.get())')
    replace('avio_size(input.raw)', 'std::numeric_limits<int64_t>::max()')
    if name == 'video.cpp':
        replace('std::string(format.value->iformat->name).find("mov") != std::string::npos,',
                '(std::string(format.value->iformat->name).find("mov") != std::string::npos ||\n'
                '             std::string(format.value->iformat->name).find("matroska") != std::string::npos),')
        replace('require(avc   ? (p->codec_tag',
                'require(is_matroska(*this) || (avc   ? (p->codec_tag')
        replace("p->codec_tag == MKTAG('h', 'e', 'v', '1')),",
                "p->codec_tag == MKTAG('h', 'e', 'v', '1'))),")
        replace('p->pts != AV_NOPTS_VALUE && p->dts != AV_NOPTS_VALUE,',
                'p->pts != AV_NOPTS_VALUE && (is_matroska(input) || p->dts != AV_NOPTS_VALUE),')
        replace('idx.samples.empty() || p->dts > idx.samples.back().dts,',
                'is_matroska(input) || idx.samples.empty() || p->dts > idx.samples.back().dts,')
    else:
        # Matroska packets have presentation timestamps, not an MP4 DTS table.
        # The spool adapter assigns DTS=PTS only for ordered AV1 temporal units.
        replace('const auto &last = idx.samples.back();\n'
                '                require(p->pts > last.pts && p->pts - last.pts == last.duration,',
                'auto &last = idx.samples.back();\n'
                '                // DefaultDuration is in ns, but packet PTS is quantized to ms.\n'
                '                // A one-tick discrepancy is rounding, not evidence of CFR.\n'
                '                if (is_matroska(input) && p->pts > last.pts &&\n'
                '                    std::abs(p->pts - last.pts - last.duration) <= 1)\n'
                '                    last.duration = p->pts - last.pts;\n'
                '                require(p->pts > last.pts && p->pts - last.pts == last.duration,')
        replace('for (size_t i = epoch.packet_begin; i < epoch.packet_end && sent < unit.end - unit.begin; ++i) {',
                '// Explicit whole-selection fallback can span several source epochs.\n'
                '    size_t last_epoch = unit.epoch;\n'
                '    while (last_epoch + 1 < idx.epochs.size() && idx.epochs[last_epoch].end < unit.end)\n'
                '        ++last_epoch;\n'
                '    for (size_t i = epoch.packet_begin; i < idx.epochs[last_epoch].packet_end && sent < unit.end - unit.begin; ++i) {')
    (target / name).write_text(text, encoding='utf-8')
    (target / (name + '.diff')).write_text(''.join(difflib.unified_diff(
        original.splitlines(True), text.splitlines(True), fromfile='src/'+name,
        tofile='research-build/'+name)), encoding='utf-8')
    manifest[name] = dict(source_lf_sha256=hashlib.sha256(original.encode()).hexdigest(),
                          adapted_lf_sha256=hashlib.sha256(text.encode()).hexdigest())
(target / 'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
