/* Synthetic image/landmark sequences, not recognition or human-form accuracy. */
#include "pose_session.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

static unsigned checks;
#define CHECK(x) do { ++checks; if (!(x)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); assert(x); } } while (0)
static uint8_t pixels[320 * 240 * 2];
static int8_t tensor[PI_INPUT_BYTES];

static pi_transform transform_for(uint32_t width, uint32_t height) {
    pi_frame frame = {pixels, width * height * 2u, width * 2u, width, height, PI_RGB565_LE};
    pi_transform transform;
    CHECK(pi_prepare(&frame, tensor, sizeof(tensor), &transform) == PI_OK);
    CHECK(tensor[0] == -128 && tensor[sizeof(tensor) - 1] == -128);
    return transform;
}

static void point(pose_movenet_landmark *p, const pi_transform *t, float x, float y) {
    p->x = (x * t->resized_width + t->pad_left) / 192.0f;
    p->y = (y * t->resized_height + t->pad_top) / 192.0f;
    p->score = .95f;
}

static void fixture(ml_exercise_kind kind, float degrees, int side, const pi_transform *t,
                    pose_movenet_landmark out[17]) {
    memset(out, 0, sizeof(pose_movenet_landmark) * 17);
    float aspect = (float)t->source_width / t->source_height;
    float rad = degrees / 57.2957795131f;
    if (kind == ML_PUSHUP) {
        float sx = .25f + .2f * sinf(rad), sy = .65f + .2f * cosf(rad);
        point(&out[5 + side], t, sx / aspect, sy);
        point(&out[7 + side], t, .25f / aspect, .65f);
        point(&out[9 + side], t, .25f / aspect, .85f);
        point(&out[11 + side], t, (sx + .25f) / aspect, sy);
        point(&out[13 + side], t, (sx + .37f) / aspect, sy);
        point(&out[15 + side], t, (sx + .49f) / aspect, sy);
    } else {
        float hx = .5f + .25f * sinf(rad), hy = .68f + .25f * cosf(rad);
        point(&out[5 + side], t, (hx - .03f) / aspect, hy - .25f);
        point(&out[11 + side], t, hx / aspect, hy);
        point(&out[13 + side], t, .5f / aspect, .68f);
        point(&out[15 + side], t, .5f / aspect, .92f);
    }
}

static ps_result frame(ps_session *s, const pi_transform *t, float angle, uint32_t ms) {
    pose_movenet_landmark landmarks[17];
    fixture(s->motion.kind, angle, 0, t, landmarks);
    return ps_update(s, landmarks, t, ms, ms + 20);
}

static void arm(ps_session *s, const pi_transform *t, uint32_t ms) {
    CHECK(frame(s, t, 175, ms).reason == PS_OK);
    CHECK(frame(s, t, 175, ms + 100).reason == PS_OK);
    ps_result r = frame(s, t, 175, ms + 200);
    CHECK(r.reason == PS_OK && r.exercise.phase == ML_READY);
}

static ps_result complete(ps_session *s, const pi_transform *t, uint32_t ms) {
    const float angles[] = {150, 130, 110, 90, 90, 90, 110, 130, 150, 175, 175, 175};
    ps_result result;
    for (unsigned i = 0; i < sizeof(angles) / sizeof(angles[0]); ++i) {
        result = frame(s, t, angles[i], ms + i * 100);
        CHECK(result.reason == PS_OK);
        CHECK(!result.exercise.rep_completed || i == 11);
    }
    return result;
}

static void bottom(ps_session *s, const pi_transform *t) {
    CHECK(ps_start(s, ML_PUSHUP, 8));
    arm(s, t, 0);
    const float angles[] = {150, 130, 110, 90, 90, 90};
    for (unsigned i = 0; i < 6; ++i) CHECK(frame(s, t, angles[i], 300 + i * 100).reason == PS_OK);
    CHECK(s->motion.phase == ML_NEED_TOP);
}

static void coordinate_and_count_tests(void) {
    const uint32_t sizes[][2] = {{320, 240}, {320, 180}, {192, 192}};
    for (unsigned resolution = 0; resolution < 3; ++resolution) {
        pi_transform t = transform_for(sizes[resolution][0], sizes[resolution][1]);
        for (int kind = ML_PUSHUP; kind <= ML_SQUAT; ++kind) {
            ps_session s;
            CHECK(ps_start(&s, (ml_exercise_kind)kind, 100 + resolution * 2u + (unsigned)kind));
            arm(&s, &t, 0);
            uint32_t token = s.continuity_token;
            ps_result r = complete(&s, &t, 300);
            CHECK(r.exercise.rep_completed && r.exercise.total_reps == 1);
            CHECK(r.continuity_token == token && token != 0);
            CHECK(fabsf(r.exercise.angle_degrees - 175) < .01f);
            CHECK(fabsf(r.image_aspect - (float)t.source_width / t.source_height) < .00001f);
            CHECK(fabsf(r.points[15].y - (kind == ML_PUSHUP ? .65f + .2f * cosf(175 / 57.2957795131f) : .92f)) < .00001f);
            CHECK(r.frame_age_ms == 20 && r.capture_ms == 1400 && r.processed_ms == 1420);
            CHECK(complete(&s, &t, 1500).exercise.total_reps == 2);
            // A partial descent returns to ready without earning a third rep.
            frame(&s, &t, 145, 2700); frame(&s, &t, 130, 2800);
            frame(&s, &t, 150, 2900); frame(&s, &t, 175, 3000);
            frame(&s, &t, 175, 3100);
            CHECK(frame(&s, &t, 175, 3200).exercise.total_reps == 2);
        }
    }
}

static void loss_tests(void) {
    pi_transform t = transform_for(320, 240);
    for (unsigned failure = 0; failure < 6; ++failure) {
        ps_session s;
        bottom(&s, &t);
        uint32_t old_token = s.continuity_token;
        pose_movenet_landmark landmarks[17];
        fixture(ML_PUSHUP, 90, 0, &t, landmarks);
        if (failure == 0) landmarks[7].score = .59f;
        if (failure == 1) landmarks[7].score = NAN;
        if (failure == 2) landmarks[7].x = INFINITY;
        if (failure == 3) landmarks[7].y = .01f; /* Letterbox padding. */
        if (failure == 4) landmarks[7].score = 1.01f;
        ps_result r = ps_update(&s, failure == 5 ? NULL : landmarks, &t, 900, 920);
        CHECK(r.reason == PS_LOW_CONFIDENCE && !r.exercise.rep_completed);
        CHECK(r.exercise.phase == ML_WAIT_TOP && r.continuity_token == 0);
        CHECK(!(r.valid_points & (UINT32_C(1) << 7)) && r.points[7].confidence == 0);
        arm(&s, &t, 1000);
        CHECK(s.continuity_token > old_token && s.motion.total_reps == 0);
        CHECK(complete(&s, &t, 1300).exercise.total_reps == 1);
    }
    // An invalid optional face landmark is masked, not invented, while the
    // complete observable exercise-side chain remains usable.
    ps_session s;
    CHECK(ps_start(&s, ML_PUSHUP, 60));
    pose_movenet_landmark landmarks[17];
    fixture(ML_PUSHUP, 175, 0, &t, landmarks);
    landmarks[0].x = NAN; landmarks[0].score = .99f;
    ps_result r = ps_update(&s, landmarks, &t, 0, 20);
    CHECK(r.reason == PS_OK && !(r.valid_points & 1u) && r.points[0].confidence == 0);
    pi_transform invalid = t; invalid.source_width = 0;
    r = ps_update(&s, landmarks, &invalid, 100, 120);
    CHECK(r.reason == PS_INVALID_TRANSFORM && r.continuity_token == 0);
}

static void discontinuity_tests(void) {
    pi_transform t = transform_for(320, 240);
    for (unsigned change = 0; change < 4; ++change) {
        ps_session s;
        bottom(&s, &t);
        pose_movenet_landmark landmarks[17];
        fixture(ML_PUSHUP, 90, change == 2 ? 1 : 0, &t, landmarks);
        pi_transform current = t;
        if (change == 0) { /* Body translation beyond .75 torso lengths. */
            for (unsigned i = 0; i < 17; ++i) if (landmarks[i].score)
                landmarks[i].x += .2f * t.resized_width / 192.0f;
        }
        if (change == 1) { /* Torso scale jumps by 50%, still inside the image. */
            landmarks[5].x -= .125f / (4.0f / 3) * t.resized_width / 192.0f;
        }
        if (change == 3) {
            current = transform_for(320, 180);
            fixture(ML_PUSHUP, 90, 0, &current, landmarks);
        }
        ps_result r = ps_update(&s, landmarks, &current, 900, 920);
        CHECK(r.reason == PS_DISCONTINUITY && r.continuity_token == 0);
        CHECK(r.exercise.phase == ML_WAIT_TOP && r.exercise.total_reps == 0);
        arm(&s, &t, 1000);
        CHECK(s.motion.total_reps == 0);
    }
}

static void timestamp_tests(void) {
    pi_transform t = transform_for(320, 240);
    ps_session s;
    pose_movenet_landmark landmarks[17];
    fixture(ML_PUSHUP, 175, 0, &t, landmarks);
    bottom(&s, &t);
    ps_result r = ps_update(&s, landmarks, &t, 800, 850);
    CHECK(r.reason == PS_DUPLICATE_FRAME && r.exercise.phase == ML_WAIT_TOP);
    CHECK(!r.exercise.rep_completed && r.continuity_token == 0);
    CHECK(ps_update(&s, landmarks, &t, 900, 920).reason == PS_OK);
    CHECK(ps_update(&s, landmarks, &t, 850, 950).reason == PS_OUT_OF_ORDER);
    CHECK(s.last_capture_ms == 900);
    CHECK(ps_update(&s, landmarks, &t, 1100, 1000).reason == PS_INVALID_TIME);
    CHECK(ps_update(&s, landmarks, &t, 1000, 1020).reason == PS_OK);
    CHECK(ps_update(&s, landmarks, &t, 1010, 1010).reason == PS_OUT_OF_ORDER);
    CHECK(ps_update(&s, landmarks, &t, 1401, 1421).reason == PS_INSUFFICIENT_SAMPLING);
    CHECK(ps_update(&s, landmarks, &t, 1501, 1521).reason == PS_OK);
    r = ps_update(&s, landmarks, &t, 1601, 2002);
    CHECK(r.reason == PS_TOO_SLOW && r.frame_age_ms == 401 && r.continuity_token == 0);
    CHECK(s.motion.total_reps == 0);

    // Repeated sparse observations can never accrue a rep or a ready dwell.
    CHECK(ps_start(&s, ML_PUSHUP, 61));
    for (uint32_t ms = 0; ms < 10000; ms += 1000) {
        r = frame(&s, &t, ms % 2000 ? 90 : 175, ms);
        CHECK(!r.exercise.rep_completed && r.exercise.total_reps == 0);
        if (ms) CHECK(r.reason == PS_INSUFFICIENT_SAMPLING);
    }
    // Completion latency cannot lengthen an 800 ms movement into a valid rep.
    CHECK(ps_start(&s, ML_PUSHUP, 62)); arm(&s, &t, 0);
    const float fast[] = {150, 130, 90, 90, 90, 130, 150, 175, 175, 175};
    for (unsigned i = 0; i < 10; ++i) {
        uint32_t capture = 300 + i * 100;
        uint32_t delay = capture > 400 ? (capture - 400) / 4 : 20;
        fixture(ML_PUSHUP, fast[i], 0, &t, landmarks);
        r = ps_update(&s, landmarks, &t, capture, capture + delay);
        CHECK(!r.exercise.rep_completed);
    }
    CHECK(r.reason == PS_MOTION_REJECTED && r.exercise.reason == ML_TOO_FAST);
    CHECK(r.exercise.total_reps == 0);

    uint32_t start = UINT32_MAX - 250;
    CHECK(ps_start(&s, ML_PUSHUP, 63)); arm(&s, &t, start);
    CHECK(complete(&s, &t, start + 300).exercise.rep_completed);
}

static void cancellation_tests(void) {
    pi_transform t = transform_for(320, 240);
    ps_session s;
    CHECK(ps_start(&s, ML_PUSHUP, 90)); arm(&s, &t, 0);
    CHECK(complete(&s, &t, 300).exercise.total_reps == 1);
    ps_stop(&s);
    ps_result r = frame(&s, &t, 175, 1500);
    CHECK(r.reason == PS_NOT_STARTED && !r.active && !r.exercise.rep_completed);
    CHECK(r.exercise.total_reps == 1 && r.continuity_token == 0);
    CHECK(ps_start(&s, ML_PUSHUP, 91));
    CHECK(s.motion.total_reps == 0 && s.token_serial == 0);
    ps_session copy = s;
    CHECK(!ps_start(&s, ML_PUSHUP, 0) && memcmp(&copy, &s, sizeof(s)) == 0);
    CHECK(!ps_start(&s, (ml_exercise_kind)99, 1) && memcmp(&copy, &s, sizeof(s)) == 0);
    s.token_serial = UINT32_MAX;
    CHECK(frame(&s, &t, 175, 0).reason == PS_TOKEN_EXHAUSTED);
    CHECK(s.continuity_token == 0);
    CHECK(ps_update(NULL, NULL, NULL, 0, 0).reason == PS_NOT_STARTED);
    CHECK(strcmp(ps_reason_string(PS_TOO_SLOW), "too_slow") == 0);
    ps_stop(NULL);
}

int main(void) {
    coordinate_and_count_tests(); loss_tests(); discontinuity_tests();
    timestamp_tests(); cancellation_tests();
    printf("pose session: %u checks passed (synthetic images/landmarks; no hardware/identity claim)\n", checks);
    return 0;
}
