#ifndef MOCK_LISA_QUEUE_H
#define MOCK_LISA_QUEUE_H
#include <stdint.h>
typedef void *lisa_queuehandle_t;
typedef int lisa_err_t;
#define LISA_OK 0
#define LISA_FAIL -1
typedef struct { lisa_queuehandle_t handle; uint32_t count, item_size; } lisa_queue_t;
#endif
