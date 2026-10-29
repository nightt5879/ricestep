#include "rice_pose.h"
#include "rice_pose_camera.h"
#include "rice_pose_profile.h"
#include "pose_engine.h"
#include "pose_image.h"
#include "pose_resources.h"
#include "pose_session.h"
#include "FreeRTOS.h"
#include "task.h"
#include "queue.h"
#include "sysheap.h"
#include "mbedtls/version.h"
#include "mbedtls/sha256.h"
#include <string.h>

typedef struct {
    uint64_t generation;
    ml_exercise_kind kind;
    uint32_t session_id;
} rice_pose_command;

static QueueHandle_t s_queue;
static uint64_t s_generation;
static bool s_active, s_creating, s_cleanup_failed;
static rice_pose_snapshot_t s_snapshot;

static bool current(uint64_t generation)
{
    bool result;
    taskENTER_CRITICAL();
    result = s_active && generation == s_generation;
    taskEXIT_CRITICAL();
    return result;
}

static void publish(uint64_t generation, const rice_pose_snapshot_t *value)
{
    taskENTER_CRITICAL();
    if (generation == s_generation) s_snapshot = *value;
    taskEXIT_CRITICAL();
}

static uint32_t now_ms(void)
{
    return (uint32_t)((uint64_t)xTaskGetTickCount() * 1000u / configTICK_RATE_HZ);
}

static int hash_model(void *unused, const uint8_t *data, size_t length, uint8_t out[32])
{
    (void)unused;
#if MBEDTLS_VERSION_MAJOR >= 3
    return mbedtls_sha256(data, length, out, 0);
#else
    return mbedtls_sha256_ret(data, length, out, 0);
#endif
}

static void run_session(const rice_pose_command *command)
{
    rice_pose_snapshot_t result = {.state = RICE_POSE_STARTING,
        .kind = command->kind, .session_id = command->session_id, .reason = "starting"};
    rp_model_view model;
    pose_engine *engine = NULL;
    uint8_t *arena = NULL, *frame_bytes = NULL;
    void *context = NULL;
    bool leased = false;
    uint16_t width = 0, height = 0;
    ps_session session;
    const char *failure = NULL;
    if (!current(command->generation)) return;
    publish(command->generation, &result);

    /* Full header and real SHA-256 validation precede allocation/camera power.
     * The pinned immutable Flash payload remains readable for engine lifetime. */
    if (rp_model_validate((const uint8_t *)(uintptr_t)RICE_POSE_WINDOW_ADDRESS,
            RICE_POSE_WINDOW_BYTES, hash_model, NULL, &model) != RP_OK) {
        failure = "model_invalid"; goto cleanup;
    }
    if (!current(command->generation)) goto cleanup;
    arena = exram_malloc(POSE_ENGINE_BUFFER_ALIGNMENT, POSE_ENGINE_MIN_ARENA_BYTES);
    context = exram_malloc(pose_engine_context_alignment(), pose_engine_context_bytes());
    frame_bytes = exram_malloc(32, 320u * 240u * 2u);
    if (!arena || !context || !frame_bytes) { failure = "out_of_memory"; goto cleanup; }
    if (!current(command->generation)) goto cleanup;
    if (pose_engine_prepare(context, pose_engine_context_bytes(), model.data,
            model.bytes, arena, POSE_ENGINE_MIN_ARENA_BYTES, &engine) != PE_OK) {
        failure = "engine_prepare_failed"; goto cleanup;
    }
    if (!current(command->generation)) goto cleanup;
    int camera_status = service_camera_pose_claim();
    if (camera_status) {
        failure = camera_status == RICE_CAMERA_QUARANTINED ? "camera_quarantined" : "camera_busy";
        goto cleanup;
    }
    leased = true;
    if (!current(command->generation)) goto cleanup;
    if (service_camera_pose_init() != 0) { failure = "camera_init_failed"; goto cleanup; }
    if (!current(command->generation)) goto cleanup;
    if (service_camera_pose_get_framesize(&width, &height) != 0 || width != 320 || height != 240) {
        failure = "camera_dimensions"; goto cleanup;
    }
    if (!ps_start(&session, command->kind, command->session_id)) {
        failure = "session_invalid"; goto cleanup;
    }
    result.state = RICE_POSE_RUNNING;
    result.reason = "waiting_for_frame";
    result.image_aspect = (float)width / height;
    publish(command->generation, &result);
    while (current(command->generation)) {
        /* The service drops two queued frames, but exposes no exposure time.
         * This is an acquisition LOWER BOUND, deliberately including capture
         * blocking in frame age so slow/stale observations cannot count. */
        uint32_t capture_ms = now_ms();
        if (service_camera_pose_capture(frame_bytes, 320u * 240u * 2u) != 0) {
            failure = "camera_capture_failed"; break;
        }
        if (!current(command->generation)) break;
        int8_t *input = NULL;
        size_t input_bytes = 0;
        pi_transform transform;
        pi_frame frame = {.data = frame_bytes, .data_size = (size_t)width * height * 2u,
            .stride_bytes = (size_t)width * 2u, .width = width, .height = height,
            .format = PI_RGB565_BE};
        if (pose_engine_input(engine, &input, &input_bytes) != PE_OK ||
                pi_prepare(&frame, input, input_bytes, &transform) != PI_OK) {
            failure = "preprocess_failed"; break;
        }
        if (!current(command->generation)) break;
        uint32_t inference_start = now_ms();
        pose_engine_status status = pose_engine_invoke(engine);
        uint32_t inference_ms = now_ms() - inference_start;
        if (!current(command->generation)) break;
        if (status != PE_OK) { failure = "inference_failed"; break; }
        pose_movenet_landmark landmarks[POSE_MOVENET_JOINTS];
        if (pose_engine_decode(engine, landmarks, NULL) != PE_OK) {
            failure = "decode_failed"; break;
        }
        if (!current(command->generation)) break;
        ps_result observation = ps_update(&session, landmarks, &transform, capture_ms, now_ms());
        result.reason = ps_reason_string(observation.reason);
        result.total_reps = observation.exercise.total_reps;
        result.capture_ms = capture_ms;
        result.inference_ms = inference_ms;
        result.frame_age_ms = observation.frame_age_ms;
        result.valid_points = observation.valid_points;
        result.image_aspect = observation.image_aspect;
        result.have_frame = true;
        memcpy(result.points, observation.points, sizeof(result.points));
        publish(command->generation, &result);
        /* Give the UI/audio tasks a scheduling opportunity, even on errors or
         * very fast frames. No weakened thresholds when this CPU is too slow. */
        vTaskDelay(pdMS_TO_TICKS(10) ? pdMS_TO_TICKS(10) : 1);
    }
    ps_stop(&session);
cleanup:
    /* Stop/release runs ONLY here on the owning worker, after a blocking capture
     * or inference has returned. Lua cancellation never frees live buffers. */
    if (leased && service_camera_pose_release() != 0) {
        failure = "camera_cleanup_failed";
        /* Cleanup faults belong to the device, not a retired Lua generation.
         * A pending stop must never acknowledge IDLE while shutdown failed. */
        taskENTER_CRITICAL();
        s_cleanup_failed = true;
        s_snapshot.state = RICE_POSE_ERROR;
        s_snapshot.reason = failure;
        s_snapshot.have_frame = false;
        s_snapshot.valid_points = 0;
        memset(s_snapshot.points, 0, sizeof(s_snapshot.points));
        taskEXIT_CRITICAL();
    }
    if (engine) pose_engine_release(engine);
    if (frame_bytes) exram_free(frame_bytes);
    if (context) exram_free(context);
    if (arena) exram_free(arena);
    result.state = failure ? RICE_POSE_ERROR : RICE_POSE_IDLE;
    result.reason = failure ? failure : "stopped";
    result.have_frame = false;
    result.valid_points = 0;
    memset(result.points, 0, sizeof(result.points));
    publish(command->generation, &result);
}

static void pose_worker(void *argument)
{
    QueueHandle_t queue = (QueueHandle_t)argument;
    rice_pose_command command;
    for (;;) {
        if (xQueueReceive(queue, &command, portMAX_DELAY) != pdTRUE) continue;
        if (command.session_id) {
            run_session(&command);
        } else {
            /* A stop command is consumed only after run_session has cleaned up.
             * Keep totals for the previous session; no late frame is published. */
            taskENTER_CRITICAL();
            if (command.generation == s_generation) {
                s_snapshot.state = s_cleanup_failed ? RICE_POSE_ERROR : RICE_POSE_IDLE;
                s_snapshot.reason = s_cleanup_failed ? "camera_cleanup_failed" : "stopped";
                s_snapshot.have_frame = false;
                s_snapshot.valid_points = 0;
                memset(s_snapshot.points, 0, sizeof(s_snapshot.points));
            }
            taskEXIT_CRITICAL();
        }
    }
}

bool rice_pose_start(ml_exercise_kind kind, uint32_t session_id)
{
    if (!session_id || (kind != ML_PUSHUP && kind != ML_SQUAT)) return false;
    rice_pose_command command;
    QueueHandle_t queue;
    taskENTER_CRITICAL();
    if (!s_active || s_creating || s_cleanup_failed ||
            (s_snapshot.state != RICE_POSE_IDLE && s_snapshot.state != RICE_POSE_ERROR)) {
        taskEXIT_CRITICAL(); return false;
    }
    command = (rice_pose_command){.generation = ++s_generation, .kind = kind, .session_id = session_id};
    s_snapshot = (rice_pose_snapshot_t){.state = RICE_POSE_STARTING, .kind = kind,
        .session_id = session_id, .reason = "starting"};
    queue = s_queue;
    if (!queue) s_creating = true;
    taskEXIT_CRITICAL();
    if (!queue) {
        queue = xQueueCreate(1, sizeof(command));
        if (queue && xTaskCreate(pose_worker, "rice.pose", 4096, queue,
                tskIDLE_PRIORITY + 1, NULL) != pdPASS) {
            vQueueDelete(queue); queue = NULL;
        }
        taskENTER_CRITICAL();
        s_creating = false;
        s_queue = queue;
        if (!queue && command.generation == s_generation) {
            s_snapshot.state = RICE_POSE_ERROR;
            s_snapshot.reason = "worker_unavailable";
        }
        /* A stop during task creation cannot enqueue yet. The fresh worker has
         * not received a start, so it is safe to acknowledge that cancellation. */
        if (command.generation != s_generation && s_snapshot.state == RICE_POSE_STOPPING) {
            s_snapshot.state = RICE_POSE_IDLE;
            s_snapshot.reason = "stopped";
        }
        taskEXIT_CRITICAL();
    }
    bool accepted = false;
    taskENTER_CRITICAL();
    if (queue && s_active && command.generation == s_generation) {
        accepted = xQueueOverwrite(queue, &command) == pdPASS;
        if (!accepted) {
            s_snapshot.state = RICE_POSE_ERROR;
            s_snapshot.reason = "worker_unavailable";
        }
    }
    taskEXIT_CRITICAL();
    return accepted;
}

static void stop_locked(void)
{
    rice_pose_command command = {.generation = ++s_generation};
    if (s_queue) {
        s_snapshot.state = RICE_POSE_STOPPING;
        s_snapshot.reason = "stopping";
        (void)xQueueOverwrite(s_queue, &command);
    } else if (s_creating) {
        s_snapshot.state = RICE_POSE_STOPPING;
        s_snapshot.reason = "stopping";
    } else {
        s_snapshot.state = RICE_POSE_IDLE;
        s_snapshot.reason = "stopped";
    }
    s_snapshot.have_frame = false;
    s_snapshot.valid_points = 0;
    memset(s_snapshot.points, 0, sizeof(s_snapshot.points));
    if (s_cleanup_failed) {
        s_snapshot.state = RICE_POSE_ERROR;
        s_snapshot.reason = "camera_cleanup_failed";
    }
}

void rice_pose_stop(void)
{
    taskENTER_CRITICAL(); stop_locked(); taskEXIT_CRITICAL();
}

void rice_pose_set_active(bool active)
{
    taskENTER_CRITICAL();
    s_active = active;
    if (!active) stop_locked();
    taskEXIT_CRITICAL();
}

void rice_pose_snapshot(rice_pose_snapshot_t *out)
{
    if (!out) return;
    taskENTER_CRITICAL(); *out = s_snapshot; taskEXIT_CRITICAL();
    if (!out->reason) out->reason = "idle";
}

const char *rice_pose_state_string(rice_pose_state_t state)
{
    static const char *names[] = {"idle", "starting", "running", "stopping", "error"};
    return (unsigned)state < sizeof(names) / sizeof(names[0]) ? names[state] : "error";
}
