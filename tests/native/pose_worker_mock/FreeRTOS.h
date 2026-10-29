#ifndef MOCK_FREERTOS_H
#define MOCK_FREERTOS_H
#include <stdint.h>
#include <stddef.h>
typedef uint32_t TickType_t;
typedef int BaseType_t;
typedef uint32_t StackType_t;
typedef struct { uintptr_t opaque[32]; } StaticQueue_t;
typedef struct { uintptr_t opaque[32]; } StaticTask_t;
typedef struct { uintptr_t opaque[32]; } StaticSemaphore_t;
typedef void *TaskHandle_t;
#define configSUPPORT_STATIC_ALLOCATION 1
#define pdTRUE 1
#define pdPASS 1
#define portMAX_DELAY UINT32_MAX
#define configTICK_RATE_HZ 1000u
#define tskIDLE_PRIORITY 0u
#define pdMS_TO_TICKS(ms) (ms)
void mock_enter(void);
void mock_exit(void);
#define taskENTER_CRITICAL() mock_enter()
#define taskEXIT_CRITICAL() mock_exit()
#endif
