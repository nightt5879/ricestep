#ifndef MOCK_HEAP_CAPS_H
#define MOCK_HEAP_CAPS_H
#include <stddef.h>
#include <stdint.h>
#define MALLOC_CAP_DEFAULT 0x1000u
#define MALLOC_CAP_SPIRAM 0x400u
void heap_caps_free(void *ptr);
#endif
