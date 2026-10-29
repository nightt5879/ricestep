/* Synthetic landmark fixtures test arithmetic/state transitions, not camera accuracy. */
#include "motion_logic.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <stdint.h>

static unsigned checks = 0;
#define CHECK(x) do { ++checks; assert(x); } while (0)
static ml_pose fixture(ml_exercise_kind kind, float degrees, int side) {
    ml_pose p;
    memset(&p, 0, sizeof(p)); p.image_aspect = 1; p.subject_id = 17;
    float radians = degrees / 57.2957795131f;
    if (kind == ML_PUSHUP) {
        float sx = .25f + .2f * sinf(radians), sy = .65f + .2f * cosf(radians);
        p.points[5 + side] = (ml_keypoint){sx, sy, .95f};
        p.points[7 + side] = (ml_keypoint){.25f, .65f, .95f};
        p.points[9 + side] = (ml_keypoint){.25f, .85f, .95f};
        p.points[11 + side] = (ml_keypoint){sx + .25f, sy, .95f};
        p.points[13 + side] = (ml_keypoint){sx + .37f, sy, .95f};
        p.points[15 + side] = (ml_keypoint){sx + .49f, sy, .95f};
    } else {
        float hx = .5f + .25f * sinf(radians), hy = .68f + .25f * cosf(radians);
        p.points[5 + side] = (ml_keypoint){hx - .03f, hy - .25f, .95f};
        p.points[11 + side] = (ml_keypoint){hx, hy, .95f};
        p.points[13 + side] = (ml_keypoint){.5f, .68f, .95f};
        p.points[15 + side] = (ml_keypoint){.5f, .92f, .95f};
    }
    return p;
}
static ml_exercise_result frame(ml_exercise *s, float angle, uint32_t now) {
    ml_pose p = fixture(s->kind, angle, 0);
    return ml_exercise_update(s, &p, now);
}
static void arm(ml_exercise *s, uint32_t t) {
    frame(s, 175, t); frame(s, 175, t + 100);
    CHECK(frame(s, 175, t + 200).phase == ML_READY);
}
static ml_exercise_result complete_rep(ml_exercise *s, uint32_t t) {
    frame(s, 130, t); frame(s, 130, t + 100);
    frame(s, 90, t + 200); frame(s, 90, t + 300); frame(s, 90, t + 400);
    frame(s, 130, t + 500); frame(s, 130, t + 600);
    frame(s, 175, t + 700); frame(s, 175, t + 800);
    return frame(s, 175, t + 900);
}
static void repetition_tests(void) {
    for (int kind = ML_PUSHUP; kind <= ML_SQUAT; ++kind) {
        ml_exercise s; CHECK(ml_exercise_start(&s, (ml_exercise_kind)kind, 1, NULL));
        CHECK(frame(&s, 90, 0).total_reps == 0);
        frame(&s, 90, 100); frame(&s, 90, 200);
        arm(&s, 300); CHECK(s.total_reps == 0); /* Starting at bottom is not a rep. */
        ml_exercise_result r = complete_rep(&s, 600);
        CHECK(r.rep_completed && r.total_reps == 1 && r.session_id == 1);
        CHECK(fabsf(r.angle_degrees - 175) < .1f);
        for (uint32_t t = 1600; t <= 2100; t += 100) CHECK(!frame(&s, 175, t).rep_completed);
        CHECK(complete_rep(&s, 2200).total_reps == 2);
        /* Partial descent returns to the start without a reward. */
        frame(&s, 130, 3200); frame(&s, 130, 3300);
        frame(&s, 175, 3400); frame(&s, 175, 3500);
        CHECK(frame(&s, 175, 3600).total_reps == 2);
        CHECK(s.phase == ML_READY);
    }
    ml_exercise s; ml_exercise_start(&s, ML_PUSHUP, 3, NULL); arm(&s, 0);
    frame(&s, 90, 300); frame(&s, 90, 400); frame(&s, 90, 500);
    frame(&s, 175, 600); frame(&s, 175, 700);
    ml_exercise_result r = frame(&s, 175, 800);
    CHECK(r.reason == ML_TOO_FAST && r.total_reps == 0);
    /* A transient low angle does not satisfy dwell. */
    frame(&s, 130, 900); frame(&s, 90, 1000); frame(&s, 130, 1100);
    frame(&s, 175, 1200); frame(&s, 175, 1300); frame(&s, 175, 1400);
    CHECK(s.total_reps == 0);
    /* A camera aspect change preserving physical points preserves angles. */
    ml_pose p = fixture(ML_PUSHUP, 90, 0); p.image_aspect = 16.0f / 9;
    for (int i = 0; i < ML_KEYPOINT_COUNT; ++i) p.points[i].x /= p.image_aspect;
    CHECK(fabsf(ml_exercise_update(&s, &p, 1500).angle_degrees - 90) < .1f);

    /* Endpoint jitter within hysteresis does not restart the held position. */
    ml_exercise_start(&s, ML_SQUAT, 20, NULL); arm(&s, 0);
    CHECK(frame(&s, 149, 300).phase == ML_READY);
    frame(&s, 130, 400); frame(&s, 95, 500); frame(&s, 105, 600);
    CHECK(frame(&s, 108, 700).phase == ML_NEED_TOP);
    frame(&s, 120, 800); frame(&s, 130, 900); frame(&s, 130, 1000);
    frame(&s, 158, 1100); frame(&s, 148, 1200);
    CHECK(frame(&s, 150, 1300).rep_completed);
}
static void interruption_tests(void) {
    ml_exercise s; ml_exercise_start(&s, ML_PUSHUP, 4, NULL); arm(&s, 0);
    frame(&s, 90, 300); frame(&s, 90, 400); frame(&s, 90, 500);
    CHECK(s.phase == ML_NEED_TOP);
    ml_exercise_set_paused(&s, false); /* Reapplying a setting is idempotent. */
    CHECK(s.phase == ML_NEED_TOP);
    ml_exercise_set_paused(&s, true);
    CHECK(frame(&s, 175, 600).reason == ML_PAUSED);
    ml_exercise_set_paused(&s, false); arm(&s, 700); CHECK(s.total_reps == 0);
    frame(&s, 90, 1000); frame(&s, 90, 1100); frame(&s, 90, 1200);
    CHECK(ml_exercise_update(&s, NULL, 1300).reason == ML_LOW_CONFIDENCE);
    arm(&s, 1400); CHECK(s.total_reps == 0);
    frame(&s, 90, 1700); frame(&s, 90, 1800); frame(&s, 90, 1900);
    CHECK(frame(&s, 175, 2401).reason == ML_FRAME_GAP);
    frame(&s, 175, 2501); frame(&s, 175, 2601); CHECK(s.total_reps == 0);
    ml_pose p = fixture(ML_PUSHUP, 90, 0); p.points[7].confidence = .1f;
    CHECK(ml_exercise_update(&s, &p, 2701).reason == ML_LOW_CONFIDENCE);
    p = fixture(ML_PUSHUP, 90, 0); p.points[7].x = NAN;
    CHECK(ml_exercise_update(&s, &p, 2801).reason == ML_LOW_CONFIDENCE);
    p = fixture(ML_PUSHUP, 90, 0); p.points[9] = p.points[7];
    CHECK(ml_exercise_update(&s, &p, 2901).reason == ML_BAD_GEOMETRY);
    p = fixture(ML_PUSHUP, 90, 0); p.points[11].y += .3f;
    CHECK(ml_exercise_update(&s, &p, 3001).reason == ML_BAD_GEOMETRY);
    arm(&s, 3101);
    p = fixture(ML_PUSHUP, 90, 0); p.subject_id = 18;
    CHECK(ml_exercise_update(&s, &p, 3401).reason == ML_PERSON_CHANGED);
    CHECK(s.phase == ML_WAIT_TOP);
    p = fixture(ML_PUSHUP, 90, 1); p.subject_id = 18;
    CHECK(ml_exercise_update(&s, &p, 3501).reason == ML_SIDE_CHANGED);
    CHECK(s.total_reps == 0);
    ml_exercise_stop(&s); CHECK(frame(&s, 175, 3601).reason == ML_NOT_STARTED);
}
static void timestamp_and_settings_tests(void) {
    ml_exercise s; CHECK(!ml_exercise_start(&s, ML_PUSHUP, 0, NULL));
    ml_exercise_config c = ml_exercise_defaults(); c.confidence = NAN;
    CHECK(!ml_exercise_start(&s, ML_PUSHUP, 1, &c));
    c = ml_exercise_defaults(); c.min_rep_ms = 100;
    CHECK(!ml_exercise_start(&s, ML_PUSHUP, 1, &c));
    c = ml_exercise_defaults(); c.max_frame_gap_ms = 0;
    CHECK(!ml_exercise_start(&s, ML_PUSHUP, 1, &c));
    ml_exercise_start(&s, ML_PUSHUP, 5, NULL); arm(&s, 1000);
    CHECK(frame(&s, 90, 1200).reason == ML_DUPLICATE_TIME);
    CHECK(s.phase == ML_READY);
    CHECK(frame(&s, 90, 1199).reason == ML_REVERSED_TIME);
    CHECK(s.phase == ML_WAIT_TOP && s.last_ms == 1200);
    uint32_t t = UINT32_MAX - 600;
    ml_exercise_start(&s, ML_PUSHUP, 6, NULL); arm(&s, t);
    CHECK(complete_rep(&s, t + 300).rep_completed); /* Tick wrap is legitimate. */
    c = ml_exercise_defaults(); c.max_rep_ms = 1000;
    ml_exercise_start(&s, ML_PUSHUP, 7, &c); arm(&s, 0);
    for (uint32_t ms = 300; ms <= 1300; ms += 100) frame(&s, 130, ms);
    CHECK(frame(&s, 130, 1400).reason == ML_TOO_SLOW);
    CHECK(s.total_reps == 0);
    c = ml_exercise_defaults(); c.refractory_ms = 2000;
    ml_exercise_start(&s, ML_PUSHUP, 8, &c); arm(&s, 0);
    CHECK(complete_rep(&s, 300).total_reps == 1);
    CHECK(complete_rep(&s, 1300).reason == ML_REFRACTORY);
    CHECK(s.total_reps == 1);
}
static void desk_tests(void) {
    ml_desk_timer t; ml_desk_config c = ml_desk_defaults();
    CHECK(c.interval_ms == 1800000 && c.snooze_ms == 300000);
    CHECK(ml_desk_start(&t, 100, NULL));
    CHECK(ml_desk_update(&t, 1800099).remaining_ms == 1);
    CHECK(ml_desk_update(&t, 1800100).new_reminder);
    CHECK(!ml_desk_update(&t, 1800100).new_reminder);
    CHECK(ml_desk_update(&t, 1800200).reminder_due);
    CHECK(ml_desk_snooze(&t, 1800200));
    CHECK(t.elapsed_ms == 1800100); /* Snoozing is not an activity break. */
    CHECK(ml_desk_update(&t, 2100199).remaining_ms == 1);
    CHECK(ml_desk_update(&t, 2100200).new_reminder);
    CHECK(ml_desk_user_break(&t, 2100300));
    CHECK(ml_desk_update(&t, 2100300).remaining_ms == 1800000);
    CHECK(!ml_desk_snooze(&t, 2100300));
    CHECK(ml_desk_update(&t, 2100299).time_rejected);
    CHECK(!ml_desk_user_break(&t, 2100299));
    ml_exercise_result r = {1, 1, false, ML_READY, ML_OK, 175};
    CHECK(!ml_desk_on_rep(&t, 2100400, &r));
    r.rep_completed = true;
    CHECK(ml_desk_on_rep(&t, 2100400, &r));
    CHECK(!ml_desk_on_rep(&t, 2100500, &r));
    CHECK(ml_desk_update(&t, 2100600).remaining_ms == 1799800);
    r.total_reps = 2; CHECK(ml_desk_on_rep(&t, 2100700, &r));
    r.session_id = 2; r.total_reps = 1; CHECK(ml_desk_on_rep(&t, 2100800, &r));
    r.session_id = 1; r.total_reps = 99; CHECK(!ml_desk_on_rep(&t, 2100900, &r));
    ml_desk_stop(&t); CHECK(!ml_desk_update(&t, 2300000).active);
    CHECK(!ml_desk_user_break(&t, 2300100));
    CHECK(!ml_desk_on_rep(&t, 2300100, &r));
    c.interval_ms = 0; CHECK(!ml_desk_start(&t, 0, &c));
    uint32_t start = UINT32_MAX - 200;
    CHECK(ml_desk_start(&t, start, NULL));
    CHECK(ml_desk_update(&t, start + 1800000).new_reminder);
}
int main(void) {
    repetition_tests(); interruption_tests(); timestamp_and_settings_tests(); desk_tests();
    printf("motion logic: %u assertions passed (synthetic landmarks, no camera claim)\n", checks);
    return 0;
}
