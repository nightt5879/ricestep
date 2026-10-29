#ifndef RICESTEP_POSE_CAMERA_MEMORY_H
#define RICESTEP_POSE_CAMERA_MEMORY_H
#include <stdint.h>
#include "lisa_queue.h"
/* Only the opt-in camera translation unit uses these replacements. */
void *rice_pose_camera_fb_alloc(uint32_t alignment, uint32_t bytes);
void rice_pose_camera_fb_free(void *pointer);
lisa_queue_t *rice_pose_camera_queue_create(uint32_t count, uint8_t *name, uint32_t item_bytes);
lisa_err_t rice_pose_camera_queue_delete(lisa_queue_t *queue);
#endif
