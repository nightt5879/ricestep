#include "rice_standalone.h"
#include "voice_pack.h"
#include "app_player.h"
#include "voice_player_comm.h"
#include "FreeRTOS.h"
#include "queue.h"
#include "task.h"
#include <stdio.h>
#include <string.h>

typedef enum { RICE_IDLE, RICE_LOADING, RICE_PLAYING, RICE_FAILED } rice_audio_status;
typedef struct { const rice_voice_clip *clip; uint32_t generation; } rice_audio_command;
static QueueHandle_t s_queue;
static volatile uint32_t s_generation;
static bool s_active;
static rice_audio_status s_status;

static int stop_player(void)
{
    /* IDLE/STOPPED decoder handles reject stop_sync, but a pending focus URL
     * must still be cancelled before either state is considered harmless. */
    (void)app_player_cancel_pending(miniapp_player);
    app_player_state_t state = app_player_get_state(miniapp_player);
    if (state == APP_PLAYER_STATE_IDLE || state == APP_PLAYER_STATE_STOPPED) return APP_PLAYER_OK;
    if (state == APP_PLAYER_STATE_ERROR) return app_player_reset(miniapp_player);
    return app_player_stop(miniapp_player);
}

static bool current(uint32_t generation)
{
    bool value;
    taskENTER_CRITICAL();
    value = generation == s_generation;
    taskEXIT_CRITICAL();
    return value;
}

static void publish(uint32_t generation, rice_audio_status status)
{
    taskENTER_CRITICAL();
    if (generation == s_generation) s_status = status;
    taskEXIT_CRITICAL();
}

static void audio_worker(void *unused)
{
    (void)unused;
    rice_audio_command command;
    /* Some decoder layers may retain the URL during asynchronous cleanup. */
    char url[64];
    for (;;) {
        if (xQueueReceive(s_queue, &command, portMAX_DELAY) != pdTRUE) continue;
        /* Only this worker calls the potentially blocking player. Stop is
         * complete before a new URL is submitted; old decoder data stays in ROM. */
        if (stop_player() != APP_PLAYER_OK) {
            publish(command.generation, RICE_FAILED);
            continue;
        }
        if (!current(command.generation)) continue;
        if (!command.clip) { publish(command.generation, RICE_IDLE); continue; }
        int count = snprintf(url, sizeof(url), "mem://addr=%usize=%u",
                             (unsigned)(uintptr_t)command.clip->data,
                             (unsigned)command.clip->size);
        app_player_play_opt_t options = {
            .url = url, .throw_time_ms = 0,
            .request_tag = command.generation, .cancel_token = &s_generation,
        };
        if (count < 0 || (size_t)count >= sizeof(url) ||
            app_player_play_ex(miniapp_player, &options) != APP_PLAYER_OK) {
            publish(command.generation, RICE_FAILED);
            (void)stop_player();
            continue;
        }
        TickType_t started = xTaskGetTickCount();
        bool played = false;
        for (;;) {
            if (!current(command.generation)) break;
            app_player_state_t state = app_player_get_state(miniapp_player);
            TickType_t elapsed = xTaskGetTickCount() - started;
            if (state == APP_PLAYER_STATE_PLAYING) {
                played = true;
                publish(command.generation, RICE_PLAYING);
            } else if (state == APP_PLAYER_STATE_ERROR) {
                publish(command.generation, RICE_FAILED);
                break;
            } else if (played && (state == APP_PLAYER_STATE_IDLE || state == APP_PLAYER_STATE_STOPPED)) {
                publish(command.generation, RICE_IDLE);
                break;
            }
            if ((!played && elapsed >= pdMS_TO_TICKS(8000)) || elapsed >= pdMS_TO_TICKS(15000)) {
                publish(command.generation, RICE_FAILED);
                break;
            }
            vTaskDelay(pdMS_TO_TICKS(20));
        }
        /* Cancellations, focus loss and timeout cannot leave an old pending
         * player request capable of starting after the Lua owner is retired. */
        (void)stop_player();
    }
}

int rice_audio_init(void)
{
    if (s_queue) return 0;
    if (!miniapp_player) return -1;
    s_queue = xQueueCreate(1, sizeof(rice_audio_command));
    if (!s_queue) return -1;
    if (xTaskCreate(audio_worker, "rice.audio", 2048, NULL, 4, NULL) != pdPASS) {
        vQueueDelete(s_queue);
        s_queue = NULL;
        return -1;
    }
    return 0;
}

bool rice_audio_play_url(const char *url, size_t length)
{
    static const char prefix[] = "builtin://ricestep/";
    static const char suffix[] = ".mp3";
    const size_t prefix_length = sizeof(prefix) - 1, suffix_length = sizeof(suffix) - 1;
    if (!url || length <= prefix_length + suffix_length ||
        length > prefix_length + RICE_VOICE_MAX_KEY_LENGTH + suffix_length ||
        memcmp(url, prefix, prefix_length) ||
        memcmp(url + length - suffix_length, suffix, suffix_length)) return false;
    const rice_voice_clip *clip = rice_voice_find(url + prefix_length,
                                                 length - prefix_length - suffix_length);
    if (!clip || !s_queue) return false;
    bool accepted = false;
    taskENTER_CRITICAL();
    if (s_active && s_status == RICE_IDLE) {
        rice_audio_command command = {.clip = clip, .generation = ++s_generation};
        s_status = RICE_LOADING;
        accepted = xQueueOverwrite(s_queue, &command) == pdPASS;
        if (!accepted) s_status = RICE_IDLE;
    }
    taskEXIT_CRITICAL();
    return accepted;
}

void rice_audio_stop(void)
{
    if (!s_queue) return;
    taskENTER_CRITICAL();
    rice_audio_command command = {.clip = NULL, .generation = ++s_generation};
    /* Keep pending speech waiting until the worker has stopped the player. */
    s_status = RICE_LOADING;
    (void)xQueueOverwrite(s_queue, &command);
    taskEXIT_CRITICAL();
}

void rice_audio_set_active(bool active)
{
    taskENTER_CRITICAL();
    s_active = active;
    taskEXIT_CRITICAL();
    rice_audio_stop();
}

const char *rice_audio_state(void)
{
    static const char *names[] = {"idle", "loading", "playing", "failed"};
    taskENTER_CRITICAL();
    rice_audio_status state = s_status;
    /* Failure is observable once, then permits a later independent utterance. */
    if (state == RICE_FAILED) s_status = RICE_IDLE;
    taskEXIT_CRITICAL();
    return names[state];
}
