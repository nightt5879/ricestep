#include "pose_movenet.h"

#include <float.h>
#include <limits.h>
#include <math.h>

#ifdef __FAST_MATH__
#error "MoveNet reference semantics require fast-math to be disabled"
#endif

_Static_assert(CHAR_BIT == 8 && sizeof(float) == 4 && FLT_RADIX == 2 && FLT_MANT_DIG == 24 && FLT_MAX_EXP == 128,
               "MoveNet contract requires 8-bit bytes and IEEE binary32");

typedef struct { int32_t za, zb, zo, ma, sa, mb, sb, mo, so; } mn_add_params;
typedef struct { int32_t za, zb, zo, multiplier, shift; } mn_mul_params;
typedef struct { int32_t zi, zo, multiplier, shift; } mn_requant_params;
#include "movenet_v4_tables.inc"

static int8_t saturate(int32_t value)
{
    return (int8_t)(value < -128 ? -128 : value > 127 ? 127 : value);
}

/* Scalar fixed-point arithmetic follows TFLite's two-rounding contract.
 * Explicit division avoids implementation-defined right-shifts of negatives.
 * All generated multipliers/shifts and 8-bit input bounds keep intermediates
 * within int32_t; products use int64_t. */
static int32_t multiply_quantized(int32_t value, int32_t multiplier, int32_t shift)
{
    const int32_t left = shift > 0 ? shift : 0;
    const int32_t right = shift < 0 ? -shift : 0;
    const int64_t product = (int64_t)value * ((int64_t)1 << left) * multiplier;
    const int64_t nudge = product >= 0 ? ((int64_t)1 << 30) : 1 - ((int64_t)1 << 30);
    int64_t high = (product + nudge) / ((int64_t)1 << 31);
    if (high > INT32_MAX) high = INT32_MAX;
    if (right) {
        const int64_t half = (int64_t)1 << (right - 1);
        const int64_t divisor = (int64_t)1 << right;
        high = high >= 0 ? (high + half) / divisor : -((-high + half) / divisor);
    }
    return (int32_t)high;
}

static int8_t add_sub(int8_t a, int8_t b, const mn_add_params *p, int subtract)
{
    const int32_t av = ((int32_t)a - p->za) * (1 << 20);
    const int32_t bv = ((int32_t)b - p->zb) * (1 << 20);
    const int32_t aa = multiply_quantized(av, p->ma, p->sa);
    const int32_t bb = multiply_quantized(bv, p->mb, p->sb);
    return saturate(multiply_quantized(subtract ? aa - bb : aa + bb, p->mo, p->so) + p->zo);
}

static int8_t multiply(int8_t a, int8_t b, const mn_mul_params *p)
{
    const int32_t value = ((int32_t)a - p->za) * ((int32_t)b - p->zb);
    return saturate(multiply_quantized(value, p->multiplier, p->shift) + p->zo);
}

static int8_t heat_score(int8_t sigmoid)
{
    return saturate(multiply_quantized((int32_t)sigmoid - mn_op104.zi,
                    mn_op104.multiplier, mn_op104.shift) + mn_op104.zo);
}

static int8_t weighted_score(int8_t heat_logit, int8_t distance_squared)
{
    const int8_t sigmoid = mn_heatmap_sigmoid[(int)heat_logit + 128];
    const int8_t denominator = mn_distance_denominator[(int)distance_squared + 128];
    const float numerator_f = ((int)sigmoid - MN_ZERO_277) * MN_SCALE_277;
    const float denominator_f = ((int)denominator - MN_ZERO_300) * MN_SCALE_300;
    const float divided = numerator_f / denominator_f;
    return saturate((int32_t)roundf(divided / MN_SCALE_303) + MN_ZERO_303);
}

int pose_movenet_decode(const pose_movenet_heads *heads,
                       pose_movenet_landmark out[POSE_MOVENET_JOINTS],
                       pose_movenet_trace *trace)
{
    if (!heads || !out || !heads->center.data || !heads->heatmap.data ||
        !heads->offsets.data || !heads->regressions.data ||
        heads->center.bytes != POSE_MOVENET_CENTER_BYTES ||
        heads->heatmap.bytes != POSE_MOVENET_HEATMAP_BYTES ||
        heads->offsets.bytes != POSE_MOVENET_VECTOR_BYTES ||
        heads->regressions.bytes != POSE_MOVENET_VECTOR_BYTES) {
        return POSE_MOVENET_INVALID_ARGUMENT;
    }
    unsigned center = 0;
    int best_center = -129;
    for (unsigned i = 0; i < POSE_MOVENET_CENTER_BYTES; ++i) {
        const int8_t probability = mn_center_sigmoid[(int)heads->center.data[i] + 128];
        const int8_t score = multiply(probability, mn_center_prior[i], &mn_op87);
        if (score > best_center) { best_center = score; center = i; }
    }
    const int8_t center_y = mn_center_y[center / POSE_MOVENET_GRID];
    const int8_t center_x = mn_center_x[center % POSE_MOVENET_GRID];
    if (trace) trace->center_index = (uint16_t)center;

    for (unsigned joint = 0; joint < POSE_MOVENET_JOINTS; ++joint) {
        const unsigned vector_index = center * 34 + joint * 2;
        const int8_t predicted_y = add_sub(center_y, heads->regressions.data[vector_index], &mn_op115, 0);
        const int8_t predicted_x = add_sub(center_x, heads->regressions.data[vector_index + 1], &mn_op118, 0);
        int8_t squared_y[POSE_MOVENET_GRID], squared_x[POSE_MOVENET_GRID];
        for (unsigned coordinate = 0; coordinate < POSE_MOVENET_GRID; ++coordinate) {
            const int8_t dy = add_sub(mn_grid[coordinate], predicted_y, &mn_op116, 1);
            const int8_t dx = add_sub(mn_grid[coordinate], predicted_x, &mn_op119, 1);
            squared_y[coordinate] = multiply(dy, dy, &mn_op117);
            squared_x[coordinate] = multiply(dx, dx, &mn_op120);
        }
        unsigned selected = 0;
        int best = -129;
        for (unsigned y = 0; y < POSE_MOVENET_GRID; ++y) {
            for (unsigned x = 0; x < POSE_MOVENET_GRID; ++x) {
                const unsigned pixel = y * POSE_MOVENET_GRID + x;
                const int8_t distance = add_sub(squared_y[y], squared_x[x], &mn_op121, 0);
                const int8_t score = weighted_score(heads->heatmap.data[pixel * 17 + joint], distance);
                /* TFLite ARG_MAX chooses the first index on quantized ties. */
                if (score > best) { best = score; selected = pixel; }
            }
        }
        const unsigned local_index = selected * 34 + joint * 2;
        const int8_t y = add_sub(mn_grid[selected / POSE_MOVENET_GRID], heads->offsets.data[local_index], &mn_op146, 0);
        /* op148 has the same scales/zero points as op146. */
        const int8_t x = add_sub(mn_grid[selected % POSE_MOVENET_GRID], heads->offsets.data[local_index + 1], &mn_op146, 0);
        const int8_t normalized_y = multiply(y, 127, &mn_op154);
        const int8_t normalized_x = multiply(x, 127, &mn_op154);
        const int8_t score = heat_score(mn_heatmap_sigmoid[(int)heads->heatmap.data[selected * 17 + joint] + 128]);
        out[joint].y = ((int)normalized_y - MN_ZERO_331) * MN_SCALE_331;
        out[joint].x = ((int)normalized_x - MN_ZERO_331) * MN_SCALE_331;
        out[joint].score = ((int)score - MN_ZERO_331) * MN_SCALE_331;
        if (trace) {
            trace->joint_index[joint] = (uint16_t)selected;
            trace->weighted_score[joint] = (int8_t)best;
        }
    }
    return POSE_MOVENET_OK;
}
