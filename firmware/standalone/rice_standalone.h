#ifndef RICESTEP_STANDALONE_H
#define RICESTEP_STANDALONE_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define RICE_APP_ID "ricestep"
int rice_standalone_start(void);
void rice_standalone_poll(void);
int rice_audio_init(void);
void rice_audio_set_active(bool active);
bool rice_audio_play_url(const char *url, size_t length);
void rice_audio_stop(void);
const char *rice_audio_state(void);

/* These functions are called only by the Lua task, never concurrently. */
bool rice_storage_owns(const char *id);
int rice_storage_load(char *data, size_t *size);
const char *rice_storage_save(const char *data, size_t size);
const char *rice_storage_clear(void);
#endif
