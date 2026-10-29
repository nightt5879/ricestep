#ifndef MOCK_TASK_H
#define MOCK_TASK_H
#include "FreeRTOS.h"
BaseType_t xTaskCreate(void (*entry)(void *), const char *name, unsigned stack,
                       void *argument, unsigned priority, void *handle);
TickType_t xTaskGetTickCount(void);
void vTaskDelay(TickType_t ticks);
#endif
