#ifndef RICESTEP_TEST_EASYFLASH_H
#define RICESTEP_TEST_EASYFLASH_H
#include <stddef.h>
#include <stdbool.h>
/* Opaque to the code under test; only object existence is queried. */
struct env_node_obj { unsigned unused; };
typedef struct env_node_obj *env_node_obj_t;
bool ef_get_env_obj(const char *key, env_node_obj_t object);
size_t ef_get_env_blob(const char *key, void *buffer, size_t capacity, size_t *stored_size);
#endif
