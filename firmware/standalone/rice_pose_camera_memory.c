#include "rice_pose_camera_memory.h"
#include "rice_pose_memory.h"
#include "FreeRTOS.h"
#include "queue.h"
#include <stddef.h>

#if configSUPPORT_STATIC_ALLOCATION != 1
#error "Pose camera queues require caller-owned FreeRTOS storage"
#endif

/* The pinned camera driver has MAX_BUF_NUM=5 and queues only FB pointers.
 * The public lisa_queue_t prefix keeps stock push/pop/ISR code unchanged. */
typedef struct {
    lisa_queue_t lisa;
    StaticQueue_t rtos;
    _Alignas(16) uint8_t data[5 * sizeof(void *)];
} rice_camera_queue;
_Static_assert(offsetof(rice_camera_queue, lisa) == 0, "queue prefix layout");

void *rice_pose_camera_fb_alloc(uint32_t alignment, uint32_t bytes)
{
    return rice_pose_alloc(alignment, bytes);
}
void rice_pose_camera_fb_free(void *pointer) { rice_pose_free(pointer); }

lisa_queue_t *rice_pose_camera_queue_create(uint32_t count, uint8_t *name, uint32_t item_bytes)
{
    (void)name;
    if (count == 0 || count > 5 || item_bytes != sizeof(void *)) return NULL;
    rice_camera_queue *queue = rice_pose_alloc(16, sizeof(*queue));
    if (!queue) return NULL;
    QueueHandle_t handle = xQueueCreateStatic(count, item_bytes, queue->data, &queue->rtos);
    if (!handle) { rice_pose_free(queue); return NULL; }
    queue->lisa.handle = (lisa_queuehandle_t)handle;
    queue->lisa.count = count;
    queue->lisa.item_size = item_bytes;
    return &queue->lisa;
}

lisa_err_t rice_pose_camera_queue_delete(lisa_queue_t *queue)
{
    if (queue) {
        if (queue->handle) vQueueDelete((QueueHandle_t)queue->handle);
        /* vQueueDelete sees a caller-owned StaticQueue_t and frees nothing.
         * Free the single owning allocation only after its handle is retired. */
        rice_pose_free((rice_camera_queue *)queue);
    }
    return LISA_OK;
}
