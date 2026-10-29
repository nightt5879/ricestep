#include "rice_pose_memory.h"
#include "esp_heap_caps.h"
#include "heap_private.h"

void *rice_pose_alloc(size_t alignment, size_t bytes)
{
    /* heap_caps_aligned_alloc_base is deliberately private upstream: its
     * caller must handle NULL. The SDK is pinned; unlike the public wrapper,
     * this path never invokes heap_caps_alloc_failed/__builtin_trap. Its own
     * multi_heap allocator still performs the normal locking/bookkeeping.
     * This adapter is only for bounded RiceStep-owned buffers. */
    if (alignment < sizeof(void *) || alignment > 4096 ||
            (alignment & (alignment - 1)) != 0 || bytes == 0 ||
            bytes > 4u * 1024u * 1024u) return NULL;
    return heap_caps_aligned_alloc_base(alignment, bytes,
                                       MALLOC_CAP_DEFAULT | MALLOC_CAP_SPIRAM);
}

void rice_pose_free(void *pointer)
{
    if (pointer) heap_caps_free(pointer);
}
