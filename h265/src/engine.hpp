// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <string>
#include <vector>
#include <cstdint>

namespace sr {
struct Range { int64_t begin{}, end{}; };
struct Options {
    std::string input, output, report, ps_mode = "hev1", access = "cra";
    std::vector<Range> ranges;
    double crf = 18;
    bool analyze = false, lossless = false, bla = true;
};
void run(const Options& options);
}
