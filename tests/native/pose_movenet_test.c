#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "pose_movenet.h"

static int8_t center[POSE_MOVENET_CENTER_BYTES];
static int8_t heatmap[POSE_MOVENET_HEATMAP_BYTES];
static int8_t offsets[POSE_MOVENET_VECTOR_BYTES];
static int8_t regressions[POSE_MOVENET_VECTOR_BYTES];
static unsigned checks;
#define CHECK(condition) do { ++checks; if (!(condition)) { \
    fprintf(stderr, "Check failed: %s at line %d\n", #condition, __LINE__); exit(1); \
} } while (0)

static pose_movenet_heads input(void)
{
    const pose_movenet_heads heads = {
        { center, sizeof(center) }, { heatmap, sizeof(heatmap) },
        { offsets, sizeof(offsets) }, { regressions, sizeof(regressions) }
    };
    return heads;
}

static void fill(int8_t value)
{
    memset(center, (unsigned char)value, sizeof(center));
    memset(heatmap, (unsigned char)value, sizeof(heatmap));
    memset(offsets, (unsigned char)value, sizeof(offsets));
    memset(regressions, (unsigned char)value, sizeof(regressions));
}

static uint32_t checksum(const int8_t *data, size_t bytes)
{
    uint32_t hash = UINT32_C(2166136261);
    for (size_t i = 0; i < bytes; ++i) hash = (hash ^ (uint8_t)data[i]) * UINT32_C(16777619);
    return hash;
}

static void invalid_inputs(void)
{
    pose_movenet_heads heads = input();
    pose_movenet_landmark out[17], before[17];
    pose_movenet_trace trace, trace_before;
    memset(out, 0x39, sizeof(out)); memcpy(before, out, sizeof(out));
    memset(&trace, 0x57, sizeof(trace)); memcpy(&trace_before, &trace, sizeof(trace));
    CHECK(pose_movenet_decode(NULL, out, &trace) == POSE_MOVENET_INVALID_ARGUMENT);
    CHECK(pose_movenet_decode(&heads, NULL, &trace) == POSE_MOVENET_INVALID_ARGUMENT);
    pose_movenet_span *spans[] = { &heads.center, &heads.heatmap, &heads.offsets, &heads.regressions };
    for (unsigned i = 0; i < 4; ++i) {
        const pose_movenet_span original = *spans[i];
        spans[i]->data = NULL;
        CHECK(pose_movenet_decode(&heads, out, &trace) == POSE_MOVENET_INVALID_ARGUMENT);
        *spans[i] = original; spans[i]->bytes--;
        CHECK(pose_movenet_decode(&heads, out, &trace) == POSE_MOVENET_INVALID_ARGUMENT);
        *spans[i] = original; spans[i]->bytes++;
        CHECK(pose_movenet_decode(&heads, out, &trace) == POSE_MOVENET_INVALID_ARGUMENT);
        *spans[i] = original;
    }
    CHECK(!memcmp(out, before, sizeof(out)));
    CHECK(!memcmp(&trace, &trace_before, sizeof(trace)));
}

static void official_constant_vectors(void)
{
    /* Frozen outputs from ai-edge-litert 2.3.0 BUILTIN_REF, original graph
     * slices, no delegates: heads-constant-{value}.npz. These are not values
     * computed by this C implementation. Hex literals preserve float bits. */
    const int8_t values[] = { -128, -1, 0, 127 };
    const pose_movenet_landmark expected[] = {
        { 0.0f, 0.0f, 0.0f },
        { 0x1.0c7dcep-5f, 0x1.0c7dcep-5f, 0.0f },
        { 0x1.2e0d88p-5f, 0x1.2e0d88p-5f, 0.0f },
        { 0x1.0b7150p+0f, 0x1.0b7150p+0f, 0x1.ef07f4p-1f }
    };
    pose_movenet_heads heads = input();
    for (unsigned test = 0; test < 4; ++test) {
        fill(values[test]);
        const uint32_t hashes[] = { checksum(center, sizeof(center)), checksum(heatmap, sizeof(heatmap)),
                                   checksum(offsets, sizeof(offsets)), checksum(regressions, sizeof(regressions)) };
        pose_movenet_landmark out[17], without_trace[17];
        pose_movenet_trace trace;
        CHECK(pose_movenet_decode(&heads, out, &trace) == POSE_MOVENET_OK);
        CHECK(pose_movenet_decode(&heads, without_trace, NULL) == POSE_MOVENET_OK);
        CHECK(!memcmp(out, without_trace, sizeof(out)));
        for (unsigned k = 0; k < 17; ++k) {
            CHECK(out[k].y == expected[test].y);
            CHECK(out[k].x == expected[test].x);
            CHECK(out[k].score == expected[test].score);
            if (test != 3) CHECK(trace.joint_index[k] == 0);
        }
        if (test != 3) CHECK(trace.center_index == 0);
        CHECK(checksum(center, sizeof(center)) == hashes[0]);
        CHECK(checksum(heatmap, sizeof(heatmap)) == hashes[1]);
        CHECK(checksum(offsets, sizeof(offsets)) == hashes[2]);
        CHECK(checksum(regressions, sizeof(regressions)) == hashes[3]);
    }
}

static void center_prior_and_distance_weighting(void)
{
    pose_movenet_heads heads = input();
    pose_movenet_landmark out[17];
    pose_movenet_trace trace;
    fill(-128);
    center[0] = 127; center[24 * 48 + 24] = 120;
    CHECK(pose_movenet_decode(&heads, out, &trace) == POSE_MOVENET_OK);
    CHECK(trace.center_index == 24 * 48 + 24); /* Lower logit wins after center prior. */

    fill(-128); center[20 * 48 + 20] = 127;
    memset(regressions, (unsigned char)-21, sizeof(regressions)); /* zero regression */
    memset(offsets, (unsigned char)-9, sizeof(offsets));          /* zero local offset */
    for (unsigned k = 0; k < 17; ++k) {
        heatmap[(20 * 48 + 20) * 17 + k] = 120;
        heatmap[(40 * 48 + 40) * 17 + k] = 127;
    }
    CHECK(pose_movenet_decode(&heads, out, &trace) == POSE_MOVENET_OK);
    CHECK(trace.center_index == 20 * 48 + 20);
    for (unsigned k = 0; k < 17; ++k) {
        CHECK(trace.joint_index[k] == 20 * 48 + 20); /* Not global heatmap argmax. */
        CHECK(fabsf(out[k].y - 20.0f / 48.0f) < 0.0083f);
        CHECK(fabsf(out[k].x - 20.0f / 48.0f) < 0.0083f);
        CHECK(out[k].score > 0.7f && out[k].score < 0.9f);
    }
}

static void distinct_joints_and_interleaved_offsets(void)
{
    pose_movenet_heads heads = input();
    pose_movenet_landmark out[17];
    pose_movenet_trace trace;
    fill(-128); center[20 * 48 + 20] = 127;
    memset(regressions, (unsigned char)-21, sizeof(regressions));
    memset(offsets, 127, sizeof(offsets)); /* Wrong diagonal/channel reads become conspicuous. */
    for (unsigned k = 0; k < 17; ++k) {
        const unsigned y = 4 + k, x = 40 - k, pixel = y * 48 + x;
        heatmap[pixel * 17 + k] = 127;
        offsets[pixel * 34 + 2 * k] = -9;     /* y offset = 0 */
        offsets[pixel * 34 + 2 * k + 1] = 1; /* x offset = 1.99781045 */
    }
    CHECK(pose_movenet_decode(&heads, out, &trace) == POSE_MOVENET_OK);
    for (unsigned k = 0; k < 17; ++k) {
        const unsigned y = 4 + k, x = 40 - k;
        CHECK(trace.joint_index[k] == y * 48 + x);
        CHECK(fabsf(out[k].y - (float)y / 48.0f) < 0.0083f);
        CHECK(fabsf(out[k].x - ((float)x + 1.99781045f) / 48.0f) < 0.0083f);
        CHECK(out[k].score == 0x1.ef07f4p-1f);
    }
}

int main(void)
{
    invalid_inputs();
    official_constant_vectors();
    center_prior_and_distance_weighting();
    distinct_joints_and_interleaved_offsets();
    printf("MoveNet decoder: %u checks passed (offline synthetic/oracle tests, no camera).\n", checks);
    return 0;
}
