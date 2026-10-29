#ifndef MOCK_QUEUE_H
#define MOCK_QUEUE_H
#include "FreeRTOS.h"
typedef struct mock_queue *QueueHandle_t;
QueueHandle_t xQueueCreate(unsigned count, size_t bytes);
void vQueueDelete(QueueHandle_t queue);
BaseType_t xQueueReceive(QueueHandle_t queue, void *out, TickType_t wait);
BaseType_t xQueueOverwrite(QueueHandle_t queue, const void *value);
#endif
