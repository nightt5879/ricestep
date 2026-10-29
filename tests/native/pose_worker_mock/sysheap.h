#ifndef MOCK_SYSHEAP_H
#define MOCK_SYSHEAP_H
#include <stddef.h>
void *exram_malloc(size_t alignment, size_t bytes);
void exram_free(void *ptr);
#endif
