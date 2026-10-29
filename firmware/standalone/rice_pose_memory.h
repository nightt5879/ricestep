#ifndef RICESTEP_POSE_MEMORY_H
#define RICESTEP_POSE_MEMORY_H
#include <stddef.h>
/* Pinned SDK adapter: PSRAM allocation that returns NULL on exhaustion without
 * invoking the SDK's global fatal allocation hook. No global policy changes. */
void *rice_pose_alloc(size_t alignment, size_t bytes);
void rice_pose_free(void *pointer);
#endif
