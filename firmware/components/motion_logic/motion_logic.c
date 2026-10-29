#include "motion_logic.h"

#include <math.h>
#include <stddef.h>
#include <string.h>
#include <limits.h>

enum { ZONE_MIDDLE = 0, ZONE_TOP = 1, ZONE_BOTTOM = 2 };
enum { LEFT = 0, RIGHT = 1 };
static const uint32_t HALF_RANGE = UINT32_C(0x80000000);

static uint32_t saturating_add(uint32_t a, uint32_t b) {
    return UINT32_MAX - a < b ? UINT32_MAX : a + b;
}
static void reset_cycle(ml_exercise *s) {
    s->phase = ML_WAIT_TOP;
    s->zone = ZONE_MIDDLE;
    s->zone_since_ms = s->last_ms;
    s->cycle_since_ms = s->last_ms;
}
ml_exercise_config ml_exercise_defaults(void) {
    ml_exercise_config c = {0.60f, 180, 400, 900, 12000, 500};
    return c;
}
static bool config_valid(const ml_exercise_config *c) {
    return isfinite(c->confidence) && c->confidence >= 0.1f && c->confidence <= 1.0f &&
        c->dwell_ms >= 50 && c->dwell_ms <= 2000 && c->max_frame_gap_ms >= 50 &&
        c->max_frame_gap_ms <= 2000 && c->min_rep_ms >= c->dwell_ms * 2 &&
        c->min_rep_ms <= 10000 && c->max_rep_ms > c->min_rep_ms &&
        c->max_rep_ms <= 60000 && c->refractory_ms <= 5000;
}
bool ml_exercise_start(ml_exercise *s, ml_exercise_kind kind,
                       uint32_t id, const ml_exercise_config *config) {
    ml_exercise_config c = config ? *config : ml_exercise_defaults();
    if (!s || !id || (kind != ML_PUSHUP && kind != ML_SQUAT) || !config_valid(&c)) return false;
    memset(s, 0, sizeof(*s));
    s->config = c; s->kind = kind; s->session_id = id;
    s->started = true; s->side = -1;
    return true;
}
void ml_exercise_set_paused(ml_exercise *s, bool paused) {
    if (!s || !s->started || s->paused == paused) return;
    s->paused = paused; s->have_time = false; s->have_subject = false; s->side = -1;
    reset_cycle(s);
}
void ml_exercise_stop(ml_exercise *s) {
    if (s) { s->started = false; reset_cycle(s); }
}
static bool point_valid(ml_keypoint p, float confidence) {
    return isfinite(p.x) && isfinite(p.y) && isfinite(p.confidence) &&
        p.x >= 0 && p.x <= 1 && p.y >= 0 && p.y <= 1 &&
        p.confidence >= confidence && p.confidence <= 1;
}
static float angle(ml_keypoint a, ml_keypoint b, ml_keypoint c, float aspect) {
    float ax = (a.x - b.x) * aspect, ay = a.y - b.y;
    float cx = (c.x - b.x) * aspect, cy = c.y - b.y;
    float length = sqrtf((ax * ax + ay * ay) * (cx * cx + cy * cy));
    if (length < 0.0004f) return -1;
    float cosine = (ax * cx + ay * cy) / length;
    if (cosine > 1) cosine = 1;
    if (cosine < -1) cosine = -1;
    return acosf(cosine) * 57.2957795131f;
}
static float side_confidence(const ml_pose *p, ml_exercise_kind kind, int side, float minimum) {
    const int ids[] = {5 + side, 7 + side, 9 + side, 11 + side, 13 + side, 15 + side};
    float score = 1;
    for (size_t i = 0; i < sizeof(ids) / sizeof(ids[0]); ++i) {
        if (kind == ML_SQUAT && (i == 1 || i == 2)) continue;
        ml_keypoint point = p->points[ids[i]];
        if (!point_valid(point, minimum)) return -1;
        if (point.confidence < score) score = point.confidence;
    }
    return score;
}
static float joint_angle(const ml_pose *p, ml_exercise_kind kind, int side) {
    ml_keypoint shoulder = p->points[5 + side], hip = p->points[11 + side];
    ml_keypoint knee = p->points[13 + side], ankle = p->points[15 + side];
    float dx = fabsf(shoulder.x - hip.x) * p->image_aspect;
    float dy = fabsf(shoulder.y - hip.y);
    if (dx * dx + dy * dy < 0.01f) return -1;
    if (kind == ML_PUSHUP) {
        if (dy > dx * 0.75f || angle(shoulder, hip, ankle, p->image_aspect) < 150 ||
            angle(hip, knee, ankle, p->image_aspect) < 150) return -1;
        return angle(shoulder, p->points[7 + side], p->points[9 + side], p->image_aspect);
    }
    /* A side-on standing/squat body, not a horizontal leg curl. */
    if (dy < dx * 0.30f || knee.y >= ankle.y) return -1;
    return angle(hip, knee, ankle, p->image_aspect);
}
static ml_exercise_result result_for(const ml_exercise *s, ml_reason reason, float degrees, bool rep) {
    ml_exercise_result r = {0, 0, false, ML_WAIT_TOP, reason, degrees};
    if (s) {
        r.session_id = s->session_id; r.total_reps = s->total_reps;
        r.phase = s->phase; r.rep_completed = rep;
    }
    return r;
}
ml_exercise_result ml_exercise_update(ml_exercise *s, const ml_pose *p, uint32_t now) {
    if (!s || !s->started) return result_for(s, ML_NOT_STARTED, -1, false);
    if (s->paused) return result_for(s, ML_PAUSED, -1, false);
    ml_reason reason = ML_OK;
    if (s->have_time) {
        uint32_t delta = now - s->last_ms;
        if (!delta) return result_for(s, ML_DUPLICATE_TIME, -1, false);
        if (delta >= HALF_RANGE) { reset_cycle(s); return result_for(s, ML_REVERSED_TIME, -1, false); }
        if (delta > s->config.max_frame_gap_ms) { reset_cycle(s); reason = ML_FRAME_GAP; }
    }
    s->last_ms = now; s->have_time = true;
    if (!p || !isfinite(p->image_aspect) || p->image_aspect < 0.2f || p->image_aspect > 5) {
        reset_cycle(s); return result_for(s, p ? ML_BAD_GEOMETRY : ML_LOW_CONFIDENCE, -1, false);
    }
    float left = side_confidence(p, s->kind, LEFT, s->config.confidence);
    float right = side_confidence(p, s->kind, RIGHT, s->config.confidence);
    if (left < 0 && right < 0) { reset_cycle(s); return result_for(s, ML_LOW_CONFIDENCE, -1, false); }
    /* Keep a usable side for the entire repetition; do not switch for small score changes. */
    int side = s->side;
    if (side < 0 || (side == LEFT ? left : right) < 0) side = left >= right ? LEFT : RIGHT;
    if (s->have_subject && p->subject_id != s->subject_id) { reset_cycle(s); reason = ML_PERSON_CHANGED; }
    if (s->side >= 0 && side != s->side) { reset_cycle(s); reason = ML_SIDE_CHANGED; }
    s->have_subject = true; s->subject_id = p->subject_id; s->side = side;
    float degrees = joint_angle(p, s->kind, side);
    if (degrees < 0) { reset_cycle(s); return result_for(s, ML_BAD_GEOMETRY, -1, false); }
    int zone = degrees >= 155 ? ZONE_TOP : (degrees <= 100 ? ZONE_BOTTOM : ZONE_MIDDLE);
    if (s->zone == ZONE_TOP && degrees >= 145) zone = ZONE_TOP;
    if (s->zone == ZONE_BOTTOM && degrees <= 110) zone = ZONE_BOTTOM;
    if (zone != s->zone) { s->zone = zone; s->zone_since_ms = now; }
    bool held = now - s->zone_since_ms >= s->config.dwell_ms;
    if ((s->phase == ML_NEED_BOTTOM || s->phase == ML_NEED_TOP) &&
        now - s->cycle_since_ms > s->config.max_rep_ms) {
        reset_cycle(s); return result_for(s, ML_TOO_SLOW, degrees, false);
    }
    if (s->phase == ML_WAIT_TOP) {
        if (zone == ZONE_TOP && held) s->phase = ML_READY;
    } else if (s->phase == ML_READY) {
        if (zone != ZONE_TOP) { s->phase = ML_NEED_BOTTOM; s->cycle_since_ms = now; }
    } else if (s->phase == ML_NEED_BOTTOM) {
        if (zone == ZONE_BOTTOM && held) s->phase = ML_NEED_TOP;
        else if (zone == ZONE_TOP && held) s->phase = ML_READY;
    } else if (zone == ZONE_TOP && held) {
        uint32_t duration = now - s->cycle_since_ms;
        s->phase = ML_READY;
        if (duration < s->config.min_rep_ms) return result_for(s, ML_TOO_FAST, degrees, false);
        if (s->have_rep && now - s->last_rep_ms < s->config.refractory_ms)
            return result_for(s, ML_REFRACTORY, degrees, false);
        if (s->total_reps < UINT32_MAX) s->total_reps++;
        else return result_for(s, ML_REFRACTORY, degrees, false);
        s->last_rep_ms = now; s->have_rep = true;
        return result_for(s, reason, degrees, true);
    }
    return result_for(s, reason, degrees, false);
}

ml_desk_config ml_desk_defaults(void) {
    ml_desk_config c = {1800000, 300000}; return c;
}
bool ml_desk_start(ml_desk_timer *s, uint32_t now, const ml_desk_config *config) {
    ml_desk_config c = config ? *config : ml_desk_defaults();
    if (!s || c.interval_ms < 1000 || c.interval_ms > 86400000 ||
        c.snooze_ms < 1000 || c.snooze_ms > 3600000) return false;
    memset(s, 0, sizeof(*s)); s->config = c; s->active = true; s->last_ms = now;
    return true;
}
void ml_desk_stop(ml_desk_timer *s) { if (s) s->active = false; }
ml_desk_result ml_desk_update(ml_desk_timer *s, uint32_t now) {
    ml_desk_result r = {false, false, false, false, 0};
    if (!s || !s->active) return r;
    r.active = true;
    uint32_t delta = now - s->last_ms;
    if (delta >= HALF_RANGE) { r.time_rejected = true; delta = 0; }
    else s->last_ms = now;
    s->elapsed_ms = saturating_add(s->elapsed_ms, delta);
    if (s->snoozed) {
        s->snooze_elapsed_ms = saturating_add(s->snooze_elapsed_ms, delta);
        if (s->snooze_elapsed_ms >= s->config.snooze_ms) {
            s->snoozed = false; s->due = true; r.new_reminder = true;
        }
    } else if (!s->due && s->elapsed_ms >= s->config.interval_ms) {
        s->due = true; r.new_reminder = true;
    }
    r.reminder_due = s->due;
    if (s->snoozed) r.remaining_ms = s->config.snooze_ms - s->snooze_elapsed_ms;
    else if (!s->due) r.remaining_ms = s->config.interval_ms - s->elapsed_ms;
    return r;
}
bool ml_desk_snooze(ml_desk_timer *s, uint32_t now) {
    if (!s || !s->active) return false;
    ml_desk_result r = ml_desk_update(s, now);
    if (r.time_rejected || !s->due) return false;
    s->due = false; s->snoozed = true; s->snooze_elapsed_ms = 0;
    return true;
}
bool ml_desk_user_break(ml_desk_timer *s, uint32_t now) {
    if (!s || !s->active || now - s->last_ms >= HALF_RANGE) return false;
    s->elapsed_ms = 0; s->snooze_elapsed_ms = 0; s->last_ms = now;
    s->due = false; s->snoozed = false;
    return true;
}
bool ml_desk_on_rep(ml_desk_timer *s, uint32_t now, const ml_exercise_result *r) {
    if (!s || !s->active || !r || !r->rep_completed || !r->session_id || !r->total_reps ||
        r->session_id < s->last_activity_session ||
        (r->session_id == s->last_activity_session && r->total_reps <= s->last_activity_total)) return false;
    if (!ml_desk_user_break(s, now)) return false;
    s->last_activity_session = r->session_id; s->last_activity_total = r->total_reps;
    return true;
}
