#ifndef RICESTEP_NATIVE_POSE_H
#define RICESTEP_NATIVE_POSE_H
#include <stdbool.h>
#include <stdint.h>
#include "motion_logic.h"

typedef enum {
    RICE_POSE_IDLE, RICE_POSE_STARTING, RICE_POSE_RUNNING,
    RICE_POSE_STOPPING, RICE_POSE_ERROR
} rice_pose_state_t;

typedef struct {
    rice_pose_state_t state;
    ml_exercise_kind kind;
    uint32_t session_id, total_reps;
    const char *reason; /* Static literal, never heap or a mutable buffer. */
    uint32_t capture_ms, inference_ms, frame_age_ms, valid_points;
    ml_keypoint points[ML_KEYPOINT_COUNT];
    float image_aspect; /* Zero until actual camera dimensions are known. */
    bool have_frame;
} rice_pose_snapshot_t;

/* Task-context only. Activation alone allocates nothing and opens no device.
 * Start is accepted asynchronously, only when active and idle/error. It never
 * reports a working camera/model before the worker has prepared both. */
void rice_pose_set_active(bool active);
bool rice_pose_start(ml_exercise_kind kind, uint32_t session_id);
void rice_pose_stop(void);
void rice_pose_snapshot(rice_pose_snapshot_t *out);
const char *rice_pose_state_string(rice_pose_state_t state);

#endif
