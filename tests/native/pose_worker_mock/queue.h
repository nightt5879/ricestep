#ifndef MOCK_QUEUE_H
#define MOCK_QUEUE_H
#include "FreeRTOS.h"
typedef struct mock_queue *QueueHandle_t;
QueueHandle_t xQueueCreateStatic(unsigned count, size_t bytes, uint8_t *storage, StaticQueue_t *queue);
void vQueueDelete(QueueHandle_t queue);
BaseType_t xQueueReceive(QueueHandle_t queue, void *out, TickType_t wait);
BaseType_t xQueueOverwrite(QueueHandle_t queue, const void *value);
#endif
