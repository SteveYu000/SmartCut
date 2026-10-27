#pragma once
#include <string>
#include <vector>
#include <cstdint>

namespace sr {
struct Range { int64_t begin{}, end{}; };
struct Options {
    std::string input, output, report, ps_mode = "avc3";
    std::vector<Range> ranges;
    double crf = 18;
    bool analyze = false;
};
void run(const Options& options);
}
