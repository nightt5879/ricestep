#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include "../../firmware/standalone/rice_pose_memory.c"
static unsigned calls, frees;
static bool fail;
static unsigned char storage[32];
void *heap_caps_aligned_alloc_base(size_t alignment, size_t bytes, uint32_t caps)
{
    assert(alignment >= sizeof(void *) && alignment <= 4096 && bytes > 0 && bytes <= 4194304);
    assert(caps == (MALLOC_CAP_DEFAULT | MALLOC_CAP_SPIRAM));
    ++calls; return fail ? NULL : storage;
}
void heap_caps_free(void *ptr) { assert(ptr == storage); ++frees; }
/* There is deliberately no public allocator or hook symbol in this executable.
 * Linking therefore also proves the adapter never calls either fatal wrapper. */
int main(void)
{
    assert(rice_pose_alloc(16, 32) == storage && calls == 1);
    fail = true; assert(rice_pose_alloc(32, 2097152) == NULL && calls == 2);
    assert(rice_pose_alloc(0, 32) == NULL);
    assert(rice_pose_alloc(3, 32) == NULL);
    assert(rice_pose_alloc(8192, 32) == NULL);
    assert(rice_pose_alloc(16, 0) == NULL);
    assert(rice_pose_alloc(16, 4194305) == NULL);
    assert(calls == 2);
    rice_pose_free(storage); rice_pose_free(NULL); assert(frees == 1);
    puts("pose memory: bounded private allocator and nonfatal OOM checks passed.");
    return 0;
}
