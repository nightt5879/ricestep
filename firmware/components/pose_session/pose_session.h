#ifndef RICESTEP_POSE_SESSION_H
#define RICESTEP_POSE_SESSION_H

#include <stdbool.h>
#include <stdint.h>
#include "motion_logic.h"
#include "pose_image.h"
#include "pose_movenet.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    PS_OK, PS_NOT_STARTED, PS_INVALID_ARGUMENT, PS_INVALID_TRANSFORM,
    PS_LOW_CONFIDENCE, PS_BAD_GEOMETRY, PS_DISCONTINUITY,
    PS_DUPLICATE_FRAME, PS_OUT_OF_ORDER, PS_INVALID_TIME,
    PS_TOO_SLOW, PS_INSUFFICIENT_SAMPLING, PS_MOTION_REJECTED,
    PS_TOKEN_EXHAUSTED
} ps_reason;

typedef struct {
    ml_exercise_result exercise; /* Only rep_completed permits a new reward. */
    ps_reason reason;
    ml_keypoint points[ML_KEYPOINT_COUNT]; /* Original image normalized x/y. */
    uint32_t valid_points; /* Bit i: finite, inside image and >= .60 confidence. */
    uint32_t continuity_token; /* Nonzero only while an observed track is usable. */
    uint32_t capture_ms, processed_ms, frame_age_ms;
    float image_aspect;
    bool active;
} ps_result;

/* Public storage permits static allocation; do not edit fields while active.
 * The token is scoped to a session and describes uninterrupted observations,
 * NOT identity or a multi-person tracker. No pointers/camera buffers retained. */
typedef struct {
    ml_exercise motion;
    uint32_t last_capture_ms, last_processed_ms, token_serial, continuity_token;
    float center_x, center_y, torso_scale, image_aspect;
    int side;
    bool have_capture, have_processed, have_track;
} ps_session;

/* Initializes storage. Fixed ml defaults, including 400 ms max gap; no threshold
 * override to compensate for slow inference. Invalid args leave state untouched.
 * session_id must be nonzero, caller-managed and increasing for reward dedupe. */
bool ps_start(ps_session *session, ml_exercise_kind kind, uint32_t session_id);
void ps_stop(ps_session *session); /* Cancel partial movement, retain total reps. */

/* Both times are uint32 monotonic milliseconds from the SAME clock. processed_ms
 * is a timestamp when results are consumed, NOT an elapsed duration. Timing
 * and dwell always use capture_ms. Reject age >400 ms as PS_TOO_SLOW (including
 * stale queued frames); reject capture gaps >400 ms as INSUFFICIENT_SAMPLING.
 * Forward tick wrap is supported within half the uint32 range. A null landmark
 * array explicitly reports observation loss and resets any partial repetition.
 * transform must be from pi_prepare; rejected points have zero confidence.
 * Caller serializes access and provides nonoverlapping input/state storage.
 * No heap, camera/device/clock/audio I/O, or reward writes occur here. */
ps_result ps_update(ps_session *session,
                    const pose_movenet_landmark landmarks[POSE_MOVENET_JOINTS],
                    const pi_transform *transform,
                    uint32_t capture_ms, uint32_t processed_ms);
const char *ps_reason_string(ps_reason reason);

#ifdef __cplusplus
}
#endif
#endif
