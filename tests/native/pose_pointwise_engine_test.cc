// Reuse the complete pinned-model oracle harness, additionally proving that
// every 1x1 operator took the project path and the 3x3 stem stayed reference.
// Test-only counters are absent from the normal native firmware build.
#include "pose_pointwise.h"
#define main reference_harness_main
#include "pose_engine_test.cc"
#undef main

int main(int argc, char** argv) {
    int result = reference_harness_main(argc, argv);
    unsigned long long fast = 0, fallback = 0;
    ricestep::pointwise::AuditCounts(&fast, &fallback);
    const auto samples = static_cast<unsigned long long>(argc - 2);
    CHECK(fast == 49 * samples && fallback == samples);
    std::printf("Pointwise fast=%llu fallback=%llu; pinned 49+1 routing passed.\n",
                fast, fallback);
    return result;
}
