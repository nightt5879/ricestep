#ifndef MOCK_TASK_H
#define MOCK_TASK_H
#include "FreeRTOS.h"
TaskHandle_t xTaskCreateStatic(void (*entry)(void *), const char *name, unsigned depth,
                               void *argument, unsigned priority, StackType_t *stack,
                               StaticTask_t *task);
TickType_t xTaskGetTickCount(void);
void vTaskDelay(TickType_t ticks);
#endif
