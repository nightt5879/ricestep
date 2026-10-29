#include "pose_session.h"
#include <math.h>
#include <stddef.h>
#include <string.h>

#ifdef __FAST_MATH__
#error "pose_session requires finite checks; disable fast-math."
#endif

static const uint32_t HALF_RANGE = UINT32_C(0x80000000);

static ml_exercise_result motion_snapshot(const ps_session *s, ml_reason reason) {
    ml_exercise_result r = {0, 0, false, ML_WAIT_TOP, reason, -1};
    if (s) {
        r.session_id = s->motion.session_id;
        r.total_reps = s->motion.total_reps;
        r.phase = s->motion.phase;
    }
    return r;
}

static void lose_track(ps_session *s) {
    /* Public motion API resets partial work and selected side while retaining
     * earned totals and the refractory history of completed repetitions. */
    ml_exercise_set_paused(&s->motion, true);
    ml_exercise_set_paused(&s->motion, false);
    s->have_track = false;
    s->continuity_token = 0;
    s->side = -1;
}

static ps_result rejected(ps_session *s, ps_result r, ps_reason reason, ml_reason detail) {
    lose_track(s);
    r.reason = reason;
    r.continuity_token = 0;
    r.exercise = motion_snapshot(s, detail);
    return r;
}

bool ps_start(ps_session *s, ml_exercise_kind kind, uint32_t session_id) {
    ml_exercise candidate;
    if (!s || !ml_exercise_start(&candidate, kind, session_id, NULL)) return false;
    memset(s, 0, sizeof(*s));
    s->motion = candidate;
    s->side = -1;
    return true;
}

void ps_stop(ps_session *s) {
    if (!s) return;
    lose_track(s);
    ml_exercise_stop(&s->motion);
}

static float usable_side(const ml_pose *pose, ml_exercise_kind kind, int side) {
    const int indices[] = {5 + side, 7 + side, 9 + side, 11 + side, 13 + side, 15 + side};
    float score = 1;
    for (unsigned i = 0; i < sizeof(indices) / sizeof(indices[0]); ++i) {
        if (kind == ML_SQUAT && (i == 1 || i == 2)) continue;
        float confidence = pose->points[indices[i]].confidence;
        if (confidence == 0) return -1;
        if (confidence < score) score = confidence;
    }
    return score;
}

ps_result ps_update(ps_session *s,
                    const pose_movenet_landmark landmarks[POSE_MOVENET_JOINTS],
                    const pi_transform *transform, uint32_t capture_ms, uint32_t processed_ms) {
    ps_result r;
    memset(&r, 0, sizeof(r));
    r.reason = PS_NOT_STARTED;
    r.capture_ms = capture_ms;
    r.processed_ms = processed_ms;
    r.frame_age_ms = processed_ms - capture_ms;
    r.exercise = motion_snapshot(s, ML_NOT_STARTED);
    if (!s || !s->motion.started) return r;
    r.active = true;

    if (s->have_processed && processed_ms - s->last_processed_ms >= HALF_RANGE)
        return rejected(s, r, PS_OUT_OF_ORDER, ML_REVERSED_TIME);
    s->last_processed_ms = processed_ms;
    s->have_processed = true;
    if (r.frame_age_ms >= HALF_RANGE)
        return rejected(s, r, PS_INVALID_TIME, ML_REVERSED_TIME);

    bool gap = false;
    if (s->have_capture) {
        uint32_t delta = capture_ms - s->last_capture_ms;
        if (!delta) return rejected(s, r, PS_DUPLICATE_FRAME, ML_DUPLICATE_TIME);
        if (delta >= HALF_RANGE) return rejected(s, r, PS_OUT_OF_ORDER, ML_REVERSED_TIME);
        gap = delta > s->motion.config.max_frame_gap_ms;
    }
    s->last_capture_ms = capture_ms;
    s->have_capture = true;
    if (r.frame_age_ms > s->motion.config.max_frame_gap_ms)
        return rejected(s, r, PS_TOO_SLOW, ML_FRAME_GAP);
    if (gap) return rejected(s, r, PS_INSUFFICIENT_SAMPLING, ML_FRAME_GAP);
    if (!landmarks) return rejected(s, r, PS_LOW_CONFIDENCE, ML_LOW_CONFIDENCE);

    // The model-frame midpoint is inside every canonical fitted letterbox.
    // This validates the transform even if every landmark itself is invalid.
    pi_source_point middle;
    if (pi_unletterbox(transform, .5f, .5f, &middle) != PI_OK)
        return rejected(s, r, PS_INVALID_TRANSFORM, ML_BAD_GEOMETRY);
    r.image_aspect = middle.image_aspect;
    if (!isfinite(r.image_aspect) || r.image_aspect < .2f || r.image_aspect > 5)
        return rejected(s, r, PS_BAD_GEOMETRY, ML_BAD_GEOMETRY);
    ml_pose pose;
    memset(&pose, 0, sizeof(pose));
    pose.image_aspect = r.image_aspect;
    for (unsigned i = 0; i < POSE_MOVENET_JOINTS; ++i) {
        pi_source_point p;
        if (!isfinite(landmarks[i].score) || landmarks[i].score < s->motion.config.confidence ||
            landmarks[i].score > 1 || pi_unletterbox(transform, landmarks[i].x, landmarks[i].y, &p) != PI_OK)
            continue;
        pose.points[i] = (ml_keypoint){p.x, p.y, landmarks[i].score};
        r.points[i] = pose.points[i];
        r.valid_points |= UINT32_C(1) << i;
    }
    const float left = usable_side(&pose, s->motion.kind, 0);
    const float right = usable_side(&pose, s->motion.kind, 1);
    if (left < 0 && right < 0)
        return rejected(s, r, PS_LOW_CONFIDENCE, ML_LOW_CONFIDENCE);
    int side = s->have_track ? s->side : (left >= right ? 0 : 1);
    if ((side == 0 ? left : right) < 0)
        return rejected(s, r, PS_DISCONTINUITY, ML_SIDE_CHANGED);

    ml_keypoint shoulder = pose.points[5 + side], hip = pose.points[11 + side];
    const float dx = (shoulder.x - hip.x) * pose.image_aspect;
    const float dy = shoulder.y - hip.y;
    const float scale = sqrtf(dx * dx + dy * dy);
    const float center_x = (shoulder.x + hip.x) * .5f * pose.image_aspect;
    const float center_y = (shoulder.y + hip.y) * .5f;
    if (!isfinite(scale) || scale < .1f)
        return rejected(s, r, PS_BAD_GEOMETRY, ML_BAD_GEOMETRY);
    if (s->have_track) {
        const float move_x = center_x - s->center_x, move_y = center_y - s->center_y;
        const float max_move = .75f * s->torso_scale;
        const float ratio = scale / s->torso_scale;
        if (pose.image_aspect != s->image_aspect || ratio < 1.0f / 1.4f || ratio > 1.4f ||
            move_x * move_x + move_y * move_y > max_move * max_move)
            return rejected(s, r, PS_DISCONTINUITY, ML_PERSON_CHANGED);
    } else {
        if (s->token_serial == UINT32_MAX)
            return rejected(s, r, PS_TOKEN_EXHAUSTED, ML_PERSON_CHANGED);
        s->continuity_token = ++s->token_serial;
        s->have_track = true;
        s->side = side;
    }
    s->center_x = center_x;
    s->center_y = center_y;
    s->torso_scale = scale;
    s->image_aspect = pose.image_aspect;
    // motion_logic's subject_id slot is used only as a continuity discriminator.
    // No identity inference, constant made-up identity, or multi-person tracking.
    pose.subject_id = s->continuity_token;
    r.exercise = ml_exercise_update(&s->motion, &pose, capture_ms);
    r.continuity_token = s->continuity_token;
    r.reason = r.exercise.reason == ML_OK ? PS_OK : PS_MOTION_REJECTED;
    if (r.exercise.reason == ML_LOW_CONFIDENCE)
        return rejected(s, r, PS_LOW_CONFIDENCE, ML_LOW_CONFIDENCE);
    if (r.exercise.reason == ML_BAD_GEOMETRY)
        return rejected(s, r, PS_BAD_GEOMETRY, ML_BAD_GEOMETRY);
    if (r.exercise.reason == ML_PERSON_CHANGED || r.exercise.reason == ML_SIDE_CHANGED)
        return rejected(s, r, PS_DISCONTINUITY, r.exercise.reason);
    return r;
}

const char *ps_reason_string(ps_reason reason) {
    switch (reason) {
    case PS_OK: return "ok";
    case PS_NOT_STARTED: return "not_started";
    case PS_INVALID_ARGUMENT: return "invalid_argument";
    case PS_INVALID_TRANSFORM: return "invalid_transform";
    case PS_LOW_CONFIDENCE: return "low_confidence";
    case PS_BAD_GEOMETRY: return "bad_geometry";
    case PS_DISCONTINUITY: return "continuity_lost";
    case PS_DUPLICATE_FRAME: return "duplicate_frame";
    case PS_OUT_OF_ORDER: return "out_of_order";
    case PS_INVALID_TIME: return "invalid_time";
    case PS_TOO_SLOW: return "too_slow";
    case PS_INSUFFICIENT_SAMPLING: return "insufficient_sampling";
    case PS_MOTION_REJECTED: return "motion_rejected";
    case PS_TOKEN_EXHAUSTED: return "continuity_token_exhausted";
    default: return "unknown";
    }
}
