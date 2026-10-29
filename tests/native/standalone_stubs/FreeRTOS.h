#ifndef RICESTEP_TEST_FREERTOS_H
#define RICESTEP_TEST_FREERTOS_H
#include <stdint.h>
typedef uint32_t TickType_t;
/* Deliberately not a 1 kHz tick, to catch millisecond/tick unit confusion. */
#define configTICK_RATE_HZ 100u
#define pdMS_TO_TICKS(ms) ((TickType_t)(((uint64_t)(ms) * configTICK_RATE_HZ) / 1000u))
#endif
