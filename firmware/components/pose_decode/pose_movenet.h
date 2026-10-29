#ifndef RICESTEP_POSE_MOVENET_H
#define RICESTEP_POSE_MOVENET_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define POSE_MOVENET_MODEL_SHA256 "cd7cc22fa946e5d146a7b98d496853e1923e22828d3972d579973f27f91bb105"
#define POSE_MOVENET_GRID 48u
#define POSE_MOVENET_JOINTS 17u
#define POSE_MOVENET_CENTER_BYTES (48u * 48u)
#define POSE_MOVENET_HEATMAP_BYTES (48u * 48u * 17u)
#define POSE_MOVENET_VECTOR_BYTES (48u * 48u * 34u)

typedef struct { const int8_t *data; size_t bytes; } pose_movenet_span;
typedef struct {
    pose_movenet_span center;      /* t259: scale .2822169065475464, zero 114 */
    pose_movenet_span heatmap;     /* t276: scale .28092747926712036, zero 115 */
    pose_movenet_span offsets;     /* t282: scale .19978104531764984, zero -9 */
    pose_movenet_span regressions; /* t285: scale .6884398460388184, zero -21 */
} pose_movenet_heads;

/* COCO order: nose, L/R eye, L/R ear, L/R shoulder, L/R elbow,
 * L/R wrist, L/R hip, L/R knee, L/R ankle. Model output order is y,x,score.
 * Coordinates are in the complete 192x192 model frame, including any padding.
 * Preserve the original graph's quantization; do not clamp coordinates to 0..1. */
typedef struct { float y, x, score; } pose_movenet_landmark;
typedef struct {
    uint16_t center_index;
    uint16_t joint_index[POSE_MOVENET_JOINTS];
    int8_t weighted_score[POSE_MOVENET_JOINTS]; /* tensor303 at each argmax */
} pose_movenet_trace;

enum { POSE_MOVENET_OK = 0, POSE_MOVENET_INVALID_ARGUMENT = -1 };

/* All four read-only spans must be contiguous INT8 NHWC [1,48,48,C], with
 * exactly the byte counts above and the pinned model's quantization. Vector
 * channels are interleaved y0,x0,...,y16,x16. Input is raw logits, not sigmoid.
 * Caller checks the model SHA before inference; this function cannot hash a
 * model it never receives. Caller owns all buffers and must not overlap them.
 * out holds 17 landmarks; trace may be NULL. Invalid input leaves outputs
 * untouched. No allocation, global mutable state, camera, or device access.
 * Use IEEE-754 float32 and do not compile with -ffast-math. */
int pose_movenet_decode(const pose_movenet_heads *heads,
                       pose_movenet_landmark out[POSE_MOVENET_JOINTS],
                       pose_movenet_trace *trace);

#ifdef __cplusplus
}
#endif
#endif
