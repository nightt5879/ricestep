/* Actual worker C under deterministic scheduling and hardware/engine doubles.
 * This tests ownership/cancellation, not model math, SHA or physical DMA. */
#include <assert.h>
#include <setjmp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../../firmware/standalone/rice_pose.c"

static unsigned checks;
#define CHECK(x) do { ++checks; if (!(x)) { fprintf(stderr, "%s:%d: %s\n", __FILE__, __LINE__, #x); abort(); } } while (0)
enum trigger { NONE, CREATE, HASH, ALLOCATE, PREPARE, CLAIM, INIT, CAPTURE, INPUT, PREPROCESS, INVOKE, DECODE, OBSERVE };
struct mock_queue { bool pending; size_t size; unsigned char data[64]; };
static struct {
    unsigned critical, queues, tasks, allocated, freed, live, hash, prepare, claim,
        init, capture, invoke, decode, release_camera, release_engine, observations;
    unsigned fail_alloc;
    bool fail_queue, fail_task, fail_hash, fail_model, fail_prepare, fail_init,
        fail_capture, fail_input, fail_preprocess, fail_invoke, fail_decode, fail_release;
    int claim_status;
    uint16_t width;
    enum trigger cancel_at;
    bool retire;
    uint32_t tick, capture_elapsed, inference_elapsed;
    void (*entry)(void *);
    void *argument;
} m;
static jmp_buf blocked;
static uint8_t model_byte;
static int8_t input_tensor[POSE_ENGINE_INPUT_BYTES];

static void event(enum trigger at)
{
    CHECK(m.critical == 0);
    if (m.cancel_at == at) {
        m.cancel_at = NONE;
        if (m.retire) rice_pose_set_active(false); else rice_pose_stop();
    }
}
void mock_enter(void) { ++m.critical; }
void mock_exit(void) { CHECK(m.critical); --m.critical; }
QueueHandle_t xQueueCreate(unsigned count, size_t bytes)
{
    CHECK(!m.critical && count == 1 && bytes <= 64); ++m.queues;
    if (m.fail_queue) return NULL;
    QueueHandle_t queue = calloc(1, sizeof(*queue)); CHECK(queue); queue->size = bytes; return queue;
}
void vQueueDelete(QueueHandle_t queue) { CHECK(!m.critical); free(queue); }
BaseType_t xQueueOverwrite(QueueHandle_t queue, const void *value)
{ CHECK(queue); memcpy(queue->data, value, queue->size); queue->pending = true; return pdPASS; }
BaseType_t xQueueReceive(QueueHandle_t queue, void *out, TickType_t wait)
{
    CHECK(!m.critical && wait == portMAX_DELAY);
    if (!queue->pending) longjmp(blocked, 1);
    memcpy(out, queue->data, queue->size); queue->pending = false; return pdTRUE;
}
BaseType_t xTaskCreate(void (*entry)(void *), const char *name, unsigned stack,
                       void *argument, unsigned priority, void *handle)
{
    (void)name; (void)handle; CHECK(!m.critical && stack >= 4096 && priority == tskIDLE_PRIORITY + 1);
    ++m.tasks; event(CREATE);
    if (m.fail_task) return 0;
    m.entry = entry; m.argument = argument; return pdPASS;
}
TickType_t xTaskGetTickCount(void) { return m.tick; }
void vTaskDelay(TickType_t ticks)
{ CHECK(!m.critical && ticks > 0); m.tick += ticks; rice_pose_stop(); }
void *exram_malloc(size_t alignment, size_t bytes)
{
    CHECK(!m.critical && alignment >= 16); ++m.allocated; event(ALLOCATE);
    if (m.fail_alloc == m.allocated) return NULL;
    void *ptr = malloc(bytes); CHECK(ptr); ++m.live; return ptr;
}
void exram_free(void *ptr) { CHECK(!m.critical && ptr && m.live); --m.live; ++m.freed; free(ptr); }
int mbedtls_sha256_ret(const unsigned char *data, size_t bytes, unsigned char out[32], int is224)
{ CHECK(data == &model_byte && bytes == 1 && is224 == 0); ++m.hash; memset(out, 0, 32); event(HASH); return m.fail_hash ? -1 : 0; }
int mbedtls_sha256(const unsigned char *d, size_t n, unsigned char o[32], int i)
{ return mbedtls_sha256_ret(d, n, o, i); }
rp_status rp_model_validate(const uint8_t *window, size_t length, rp_sha256_fn hash,
                            void *context, rp_model_view *out)
{
    CHECK((uintptr_t)window == RICE_POSE_WINDOW_ADDRESS && length == RICE_POSE_WINDOW_BYTES);
    CHECK(m.init == 0 && m.claim == 0 && m.allocated == 0);
    if (m.fail_model) return RP_BAD_HEADER;
    uint8_t digest[32]; if (hash(context, &model_byte, 1, digest)) return RP_HASH_FAILED;
    *out = (rp_model_view){&model_byte, POSE_ENGINE_MODEL_BYTES}; return RP_OK;
}
size_t pose_engine_context_bytes(void) { return 64; }
size_t pose_engine_context_alignment(void) { return 16; }
pose_engine_status pose_engine_prepare(void *ctx, size_t n, const uint8_t *model, size_t length,
                                      uint8_t *arena, size_t bytes, pose_engine **out)
{
    CHECK(ctx && n == 64 && model == &model_byte && length == POSE_ENGINE_MODEL_BYTES);
    CHECK(arena && bytes == POSE_ENGINE_MIN_ARENA_BYTES && m.hash == 1);
    ++m.prepare; event(PREPARE); *out = m.fail_prepare ? NULL : (pose_engine *)ctx;
    return m.fail_prepare ? PE_ALLOCATION_FAILURE : PE_OK;
}
void pose_engine_release(pose_engine *engine) { CHECK(engine && m.live == 3); ++m.release_engine; }
pose_engine_status pose_engine_input(pose_engine *engine, int8_t **data, size_t *bytes)
{ CHECK(engine); event(INPUT); *data = input_tensor; *bytes = sizeof(input_tensor); return m.fail_input ? PE_NOT_READY : PE_OK; }
pi_status pi_prepare(const pi_frame *frame, int8_t *input, size_t bytes, pi_transform *out)
{
    CHECK(frame->width == 320 && frame->height == 240 && frame->format == PI_RGB565_BE);
    CHECK(frame->stride_bytes == 640 && frame->data_size == 153600 && input == input_tensor && bytes == sizeof(input_tensor));
    event(PREPROCESS); *out = (pi_transform){320,240,192,144,0,24}; return m.fail_preprocess ? PI_INVALID_ARGUMENT : PI_OK;
}
pose_engine_status pose_engine_invoke(pose_engine *engine)
{ CHECK(engine); ++m.invoke; event(INVOKE); m.tick += m.inference_elapsed; return m.fail_invoke ? PE_INVOKE_FAILURE : PE_OK; }
pose_engine_status pose_engine_decode(const pose_engine *engine, pose_movenet_landmark out[17], pose_movenet_trace *trace)
{ CHECK(engine && !trace); ++m.decode; event(DECODE); memset(out, 0, 17 * sizeof(*out)); return m.fail_decode ? PE_NO_RESULT : PE_OK; }
bool ps_start(ps_session *session, ml_exercise_kind kind, uint32_t id)
{ CHECK(session && id && (kind == ML_PUSHUP || kind == ML_SQUAT)); memset(session, 0, sizeof(*session)); return true; }
void ps_stop(ps_session *session) { CHECK(session); }
ps_result ps_update(ps_session *session, const pose_movenet_landmark p[17], const pi_transform *transform,
                    uint32_t captured, uint32_t processed)
{
    CHECK(session && p && transform && captured == 100);
    CHECK(processed == 100 + m.capture_elapsed + m.inference_elapsed);
    ++m.observations; event(OBSERVE);
    ps_result result = {.reason = PS_OK, .capture_ms = captured, .processed_ms = processed,
        .frame_age_ms = processed - captured, .image_aspect = 4.0f/3.0f, .valid_points = 131071};
    result.exercise.total_reps = 5; return result;
}
const char *ps_reason_string(ps_reason reason) { (void)reason; return "ok"; }
int service_camera_pose_claim(void) { ++m.claim; event(CLAIM); return m.claim_status; }
int service_camera_pose_init(void) { ++m.init; event(INIT); return m.fail_init ? -1 : 0; }
int service_camera_pose_get_framesize(uint16_t *w, uint16_t *h) { *w = m.width; *h = 240; return 0; }
int service_camera_pose_capture(uint8_t *buffer, uint32_t bytes)
{ CHECK(buffer && bytes == 153600); ++m.capture; event(CAPTURE); m.tick += m.capture_elapsed; return m.fail_capture ? -1 : 0; }
int service_camera_pose_release(void) { ++m.release_camera; CHECK(m.live == 3); return m.fail_release ? -1 : 0; }

static void reset(void)
{
    CHECK(m.critical == 0 && m.live == 0);
    if (s_queue) free(s_queue);
    s_queue = NULL; s_generation = 0; s_active = s_creating = s_cleanup_failed = false;
    memset(&s_snapshot, 0, sizeof(s_snapshot)); memset(&m, 0, sizeof(m));
    m.width = 320; m.tick = 100; m.capture_elapsed = 80; m.inference_elapsed = 90;
}
static void pump(void)
{ CHECK(m.entry); if (!setjmp(blocked)) m.entry(m.argument); CHECK(m.critical == 0 && m.live == 0); }
static rice_pose_snapshot_t snapshot(void) { rice_pose_snapshot_t s; rice_pose_snapshot(&s); return s; }
static void begin(void) { rice_pose_set_active(true); CHECK(rice_pose_start(ML_SQUAT, 7)); CHECK(snapshot().state == RICE_POSE_STARTING); }
static void expect_error(const char *reason)
{ pump(); CHECK(snapshot().state == RICE_POSE_ERROR); CHECK(strcmp(snapshot().reason, reason) == 0); CHECK(m.live == 0); }
int main(void)
{
    reset(); CHECK(!rice_pose_start(ML_SQUAT, 1)); rice_pose_set_active(true);
    CHECK(!m.queues && !m.tasks && !m.allocated && !m.hash && !m.claim);
    CHECK(!rice_pose_start((ml_exercise_kind)9, 1)); CHECK(!rice_pose_start(ML_SQUAT, 0));
    CHECK(!strcmp(rice_pose_state_string(RICE_POSE_RUNNING), "running"));
    CHECK(!strcmp(rice_pose_state_string((rice_pose_state_t)99), "error"));
    begin(); CHECK(!rice_pose_start(ML_SQUAT, 8)); pump();
    CHECK(snapshot().state == RICE_POSE_IDLE && snapshot().total_reps == 5);
    CHECK(snapshot().capture_ms == 100 && snapshot().inference_ms == 90 && snapshot().frame_age_ms == 170);
    CHECK(m.hash == 1 && m.capture == 1 && m.invoke == 1 && m.release_camera == 1 && m.release_engine == 1);
    CHECK(m.allocated == 3 && m.freed == 3 && !snapshot().have_frame);
    reset(); m.fail_model = true; begin(); expect_error("model_invalid"); CHECK(!m.allocated && !m.claim);
    reset(); m.fail_hash = true; begin(); expect_error("model_invalid"); CHECK(m.hash == 1 && !m.claim);
    for (unsigned i = 1; i <= 3; ++i) { reset(); m.fail_alloc = i; begin(); expect_error("out_of_memory"); CHECK(!m.claim && m.freed == 2); }
    reset(); m.fail_prepare = true; begin(); expect_error("engine_prepare_failed"); CHECK(!m.claim && !m.release_engine);
    reset(); m.claim_status = RICE_CAMERA_BUSY; begin(); expect_error("camera_busy"); CHECK(!m.init && !m.release_camera);
    reset(); m.claim_status = RICE_CAMERA_QUARANTINED; begin(); expect_error("camera_quarantined");
    reset(); m.fail_init = true; begin(); expect_error("camera_init_failed"); CHECK(m.release_camera == 1 && !m.capture);
    reset(); m.width = 240; begin(); expect_error("camera_dimensions"); CHECK(!m.capture);
    reset(); m.fail_capture = true; begin(); expect_error("camera_capture_failed"); CHECK(!m.invoke);
    reset(); m.fail_input = true; begin(); expect_error("preprocess_failed"); CHECK(!m.invoke);
    reset(); m.fail_preprocess = true; begin(); expect_error("preprocess_failed"); CHECK(!m.invoke);
    reset(); m.fail_invoke = true; begin(); expect_error("inference_failed"); CHECK(!m.decode);
    reset(); m.fail_decode = true; begin(); expect_error("decode_failed"); CHECK(!m.observations);
    for (int at = HASH; at <= OBSERVE; ++at) {
        reset(); m.cancel_at = (enum trigger)at; m.retire = (at % 2) != 0; begin(); pump();
        CHECK(snapshot().state == RICE_POSE_IDLE && snapshot().total_reps == 0 && !snapshot().have_frame);
        CHECK(m.live == 0); if (at <= PREPARE) CHECK(!m.claim);
        if (at == CAPTURE) CHECK(!m.invoke); if (at == INVOKE) CHECK(!m.decode);
    }
    reset(); m.cancel_at = CREATE; rice_pose_set_active(true);
    CHECK(!rice_pose_start(ML_PUSHUP, 1)); CHECK(snapshot().state == RICE_POSE_IDLE); pump(); CHECK(!m.hash);
    reset(); m.fail_queue = true; rice_pose_set_active(true); CHECK(!rice_pose_start(ML_PUSHUP, 1)); CHECK(snapshot().state == RICE_POSE_ERROR);
    reset(); m.fail_task = true; rice_pose_set_active(true); CHECK(!rice_pose_start(ML_PUSHUP, 1)); CHECK(!s_queue);
    reset(); begin(); rice_pose_stop(); pump(); CHECK(!m.hash && snapshot().state == RICE_POSE_IDLE);
    for (int at = CAPTURE; at <= INVOKE; ++at) {
        reset(); m.cancel_at = (enum trigger)at; m.fail_release = true; begin(); expect_error("camera_cleanup_failed");
        CHECK(!rice_pose_start(ML_SQUAT, 8)); rice_pose_stop(); pump(); CHECK(snapshot().state == RICE_POSE_ERROR);
    }
    reset(); printf("pose worker: %u checks passed (mock camera/engine/scheduler/SHA; no hardware).\n", checks);
    return 0;
}
