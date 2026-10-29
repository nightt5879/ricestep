/* Real allocation adapters + unmodified pinned SDK FB lifecycle functions.
 * Queue scheduling and heap storage are doubles; no camera/HAL is invoked. */
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../../firmware/standalone/rice_pose_camera_memory.c"
#include "../../firmware/standalone/rice_pose_memory.c"
static unsigned checks, calls, fail_at, live, queues, deleted;
static size_t live_bytes, peak_bytes;
static bool fail_queue;
static struct { void *pointer; size_t bytes; } allocations[32];
#define CHECK(x) do { ++checks; if (!(x)) { fprintf(stderr, "%s:%d: %s\n", __FILE__, __LINE__, #x); abort(); } } while (0)
struct mock_queue { uint8_t *storage; unsigned count, bytes, head, filled; bool retired; };
void *heap_caps_aligned_alloc_base(size_t alignment, size_t bytes, uint32_t caps)
{
    CHECK(alignment >= sizeof(void *) && caps == (MALLOC_CAP_DEFAULT | MALLOC_CAP_SPIRAM));
    if (++calls == fail_at) return NULL;
    void *pointer = malloc(bytes); CHECK(pointer);
    for (unsigned i = 0; i < 32; ++i) if (!allocations[i].pointer) {
        allocations[i].pointer = pointer; allocations[i].bytes = bytes;
        ++live; live_bytes += bytes; if (peak_bytes < live_bytes) peak_bytes = live_bytes;
        return pointer;
    }
    CHECK(false); return NULL;
}
void heap_caps_free(void *pointer)
{
    for (unsigned i = 0; i < 32; ++i) if (allocations[i].pointer == pointer) {
        CHECK(live > 0); --live; live_bytes -= allocations[i].bytes;
        allocations[i].pointer = NULL; free(pointer); return;
    }
    CHECK(false); /* Detect double/free of caller-owned queue control. */
}
QueueHandle_t xQueueCreateStatic(unsigned count, size_t bytes, uint8_t *storage, StaticQueue_t *control)
{
    CHECK(count >= 1 && count <= 5 && bytes == sizeof(void *) && storage && control);
    CHECK(sizeof(struct mock_queue) <= sizeof(*control));
    if (fail_queue) return NULL;
    struct mock_queue *queue = (struct mock_queue *)control;
    *queue = (struct mock_queue){.storage = storage, .count = count, .bytes = (unsigned)bytes};
    ++queues; return queue;
}
void vQueueDelete(QueueHandle_t queue)
{ CHECK(queue && !queue->retired); queue->retired = true; ++deleted; }
static lisa_err_t lisa_queue_push(lisa_queue_t *value, void *item, uint32_t bytes, int32_t wait)
{
    (void)wait; struct mock_queue *queue = value->handle;
    CHECK(queue && !queue->retired && bytes == queue->bytes);
    if (queue->filled == queue->count) return LISA_FAIL;
    memcpy(queue->storage + ((queue->head + queue->filled) % queue->count) * bytes, item, bytes);
    ++queue->filled; return LISA_OK;
}
static lisa_err_t lisa_queue_pop(lisa_queue_t *value, void *item, uint32_t bytes, int32_t wait)
{
    (void)wait; struct mock_queue *queue = value->handle;
    CHECK(queue && !queue->retired && bytes == queue->bytes);
    if (!queue->filled) return LISA_FAIL;
    memcpy(item, queue->storage + queue->head * bytes, bytes);
    queue->head = (queue->head + 1) % queue->count; --queue->filled; return LISA_OK;
}
#define lisa_mem_align_alloc rice_pose_camera_fb_alloc
#define lisa_mem_free rice_pose_camera_fb_free
#define lisa_queue_create rice_pose_camera_queue_create
#define lisa_queue_delete rice_pose_camera_queue_delete
#define MAX_BUF_NUM 5
#define LOGE(...) ((void)0)
#define LOGI(...) ((void)0)
#define LOGD(...) ((void)0)
#define LISA_NO_WAIT 0
#define LISA_DEVICE_OK 0
#define LISA_DEVICE_ERR_NO_MEM -1
#define LISA_DEVICE_ERR_INVALID -2
#define LISA_CAMERA_PIXFMT_GRAY 1
typedef struct {
    uint8_t *buf; uint32_t len, width, height, format, timestamp;
} lisa_camera_fb_t;
typedef struct {
    struct { uint8_t fb_count; void *mem_pool; uint32_t mem_pool_size; } config;
    lisa_queue_t *queue_free, *queue_filled;
    uint8_t *fb_buf[MAX_BUF_NUM]; lisa_camera_fb_t fb_list[MAX_BUF_NUM];
    uint8_t fb_count; uint32_t fb_slot_size, frame_width, frame_height, pixel_format;
} camera_priv_t;
#include "fixtures/lisa_camera_fb_pinned.inc"

static camera_priv_t fresh(void)
{
    CHECK(!live && !live_bytes && queues == deleted);
    calls = fail_at = queues = deleted = 0; peak_bytes = 0; fail_queue = false;
    camera_priv_t camera = {0}; camera.config.fb_count = 2; camera.frame_width = 640; camera.frame_height = 480;
    return camera;
}
int main(void)
{
    for (unsigned fail = 1; fail <= 4; ++fail) {
        camera_priv_t camera = fresh(); fail_at = fail;
        CHECK(lisa_camera_fb_init(&camera, 640 * 480 * 2) == LISA_DEVICE_ERR_NO_MEM);
        if (fail == 2) CHECK(camera.queue_free && !camera.queue_filled);
        lisa_camera_fb_deinit(&camera); CHECK(!live && !live_bytes && queues == deleted);
    }
    for (unsigned fail = 1; fail <= 2; ++fail) {
        camera_priv_t camera = fresh(); CHECK(lisa_camera_fb_init(&camera, 640 * 480 * 2) == 0);
        CHECK(live == 4 && camera.fb_count == 2);
        camera.frame_width = 320; camera.frame_height = 240; fail_at = calls + fail;
        CHECK(lisa_camera_fb_reinit(&camera) == LISA_DEVICE_ERR_NO_MEM);
        lisa_camera_fb_deinit(&camera); CHECK(!live && !live_bytes && queues == deleted);
    }
    for (unsigned cycle = 0; cycle < 3; ++cycle) {
        camera_priv_t camera = fresh(); CHECK(lisa_camera_fb_init(&camera, 640 * 480 * 2) == 0);
        CHECK(lisa_camera_refresh_fb_metadata(&camera, 640 * 480 * 2) == 0);
        camera.frame_width = 320; camera.frame_height = 240;
        CHECK(lisa_camera_fb_reinit(&camera) == 0);
        CHECK(camera.fb_slot_size == 153600 && live_bytes == 307200 + 2 * sizeof(rice_camera_queue));
        CHECK(peak_bytes == 1228800 + 2 * sizeof(rice_camera_queue));
        lisa_camera_fb_deinit(&camera); CHECK(!live && !live_bytes && queues == deleted);
    }
    camera_priv_t camera = fresh(); fail_queue = true;
    CHECK(lisa_camera_fb_init(&camera, 640 * 480 * 2) == LISA_DEVICE_ERR_NO_MEM);
    lisa_camera_fb_deinit(&camera); CHECK(!live);
    CHECK(!rice_pose_camera_queue_create(0, NULL, sizeof(void *)));
    CHECK(!rice_pose_camera_queue_create(6, NULL, sizeof(void *)));
    CHECK(!rice_pose_camera_queue_create(2, NULL, 1));
    CHECK(rice_pose_camera_queue_delete(NULL) == LISA_OK);
    printf("pose camera allocations: %u checks passed (actual pinned FB functions + adapters; no hardware).\n", checks);
    return 0;
}
