#ifndef RICESTEP_MOTION_LOGIC_H
#define RICESTEP_MOTION_LOGIC_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

enum { ML_KEYPOINT_COUNT = 17 };
typedef enum { ML_PUSHUP, ML_SQUAT } ml_exercise_kind;
typedef enum { ML_WAIT_TOP, ML_READY, ML_NEED_BOTTOM, ML_NEED_TOP } ml_phase;
typedef enum {
    ML_OK, ML_NOT_STARTED, ML_PAUSED, ML_LOW_CONFIDENCE, ML_BAD_GEOMETRY,
    ML_FRAME_GAP, ML_DUPLICATE_TIME, ML_REVERSED_TIME, ML_PERSON_CHANGED,
    ML_SIDE_CHANGED, ML_TOO_FAST, ML_TOO_SLOW, ML_REFRACTORY
} ml_reason;

typedef struct { float x, y, confidence; } ml_keypoint;
/* COCO order. x/y refer to the uncropped camera image, in [0,1]. */
typedef struct {
    ml_keypoint points[ML_KEYPOINT_COUNT];
    float image_aspect; /* width / height; required for correct joint angles */
    uint32_t subject_id; /* Stable selected-person ID supplied by the tracker. */
} ml_pose;

typedef struct {
    float confidence;
    uint32_t dwell_ms, max_frame_gap_ms;
    uint32_t min_rep_ms, max_rep_ms, refractory_ms;
} ml_exercise_config;

typedef struct {
    uint32_t session_id, total_reps;
    bool rep_completed;
    ml_phase phase;
    ml_reason reason;
    float angle_degrees; /* -1 when no usable geometry. Not a form/health score. */
} ml_exercise_result;

typedef struct {
    ml_exercise_config config;
    ml_exercise_kind kind;
    ml_phase phase;
    uint32_t session_id, total_reps, last_ms, zone_since_ms;
    uint32_t cycle_since_ms, last_rep_ms, subject_id;
    int side, zone;
    bool started, paused, have_time, have_subject, have_rep;
} ml_exercise;

ml_exercise_config ml_exercise_defaults(void);
/* Return false for invalid settings/kind/session ID; leave object untouched. */
bool ml_exercise_start(ml_exercise *state, ml_exercise_kind kind,
                       uint32_t session_id, const ml_exercise_config *config);
void ml_exercise_set_paused(ml_exercise *state, bool paused);
void ml_exercise_stop(ml_exercise *state);
ml_exercise_result ml_exercise_update(ml_exercise *state, const ml_pose *pose,
                                      uint32_t monotonic_ms);

typedef struct {
    uint32_t interval_ms, snooze_ms;
} ml_desk_config;
typedef struct {
    ml_desk_config config;
    uint32_t elapsed_ms, snooze_elapsed_ms, last_ms;
    uint32_t last_activity_session, last_activity_total;
    bool active, due, snoozed;
} ml_desk_timer;
typedef struct {
    bool active, reminder_due, new_reminder, time_rejected;
    uint32_t remaining_ms;
} ml_desk_result;

ml_desk_config ml_desk_defaults(void);
bool ml_desk_start(ml_desk_timer *timer, uint32_t monotonic_ms,
                   const ml_desk_config *config);
void ml_desk_stop(ml_desk_timer *timer);
ml_desk_result ml_desk_update(ml_desk_timer *timer, uint32_t monotonic_ms);
/* Snooze postpones an outstanding reminder; it does not count as a break. */
bool ml_desk_snooze(ml_desk_timer *timer, uint32_t monotonic_ms);
/* Explicit user acknowledgment of taking a break. */
bool ml_desk_user_break(ml_desk_timer *timer, uint32_t monotonic_ms);
/* Consume a completed rep. IDs must be persistent increasing session IDs.
 * Replaying the same/older cumulative result cannot keep resetting the timer. */
bool ml_desk_on_rep(ml_desk_timer *timer, uint32_t monotonic_ms,
                    const ml_exercise_result *result);

#ifdef __cplusplus
}
#endif
#endif
