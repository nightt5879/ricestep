#ifndef RICESTEP_POSE_ENGINE_H
#define RICESTEP_POSE_ENGINE_H

#include <stddef.h>
#include <stdint.h>
#include "pose_movenet.h"

#ifdef __cplusplus
extern "C" {
#endif

#define POSE_ENGINE_HEADS_SHA256 "9051a5ceebc1eb5f535c1b1afe58d33ce878bf9358a159cbfc689c1abebf9c68"
#define POSE_ENGINE_MODEL_BYTES 2891896u
#define POSE_ENGINE_INPUT_BYTES (192u * 192u * 3u)
#define POSE_ENGINE_BUFFER_ALIGNMENT 16u
/* Conservative guard against the pinned TFLM allocator's unchecked placement
 * construction on tiny arenas. Capacity still does not guarantee allocation. */
#define POSE_ENGINE_MIN_ARENA_BYTES (2u * 1024u * 1024u)

typedef struct pose_engine pose_engine;
typedef enum {
    PE_OK = 0,
    PE_INVALID_ARGUMENT = -1,
    PE_BAD_ALIGNMENT = -2,
    PE_BAD_MODEL_SIZE = -3,
    PE_INVALID_FLATBUFFER = -4,
    PE_MODEL_PROFILE_MISMATCH = -5,
    PE_RESOLVER_FAILURE = -6,
    PE_ALLOCATION_FAILURE = -7,
    PE_NOT_READY = -8,
    PE_INVOKE_FAILURE = -9,
    PE_NO_RESULT = -10,
    PE_ARENA_TOO_SMALL = -11
} pose_engine_status;

/* Pure queries; no allocation, model access, constructors, or runtime startup. */
size_t pose_engine_context_bytes(void);
size_t pose_engine_context_alignment(void);
const char *pose_engine_status_string(pose_engine_status status);

/* First run rp_model_validate() with a real SHA256 callback (or equivalent
 * whole-file verification) and require POSE_ENGINE_HEADS_SHA256. Keep that
 * exact model immutable for this engine's entire lifetime. This function
 * verifies FlatBuffer bounds and the fixed model profile, NOT the digest.
 *
 * Caller owns three disjoint regions: context, read-only model, tensor arena.
 * Context must have queried size/alignment; model and arena require 16-byte
 * alignment. All must remain alive until release. No malloc/new allocation:
 * C++ objects use placement construction in context; TFLM uses only arena.
 * No operations occur before this explicit preparation call.
 *
 * out must be aligned and outside all three regions. Invalid output-pointer
 * aliases are rejected without writing to out. On other failures *out is NULL
 * and context/arena may be reused immediately. Do not
 * prepare over a live context: release it first. The 2 MiB host probe envelope
 * is not a promise of allocation success on the Mini3 build. */
pose_engine_status pose_engine_prepare(void *context, size_t context_bytes,
                                      const uint8_t *model, size_t model_bytes,
                                      uint8_t *arena, size_t arena_bytes,
                                      pose_engine **out);

/* Releases placement-constructed runtime objects, never frees caller memory.
 * NULL is harmless. Any other pointer must refer to a live prepared engine;
 * after release that pointer and all tensor views become invalid. */
void pose_engine_release(pose_engine *engine);

/* Borrow the internal INT8 NHWC [1,192,192,3] input. Write exactly the returned
 * bytes using uint8 RGB minus128 (scale1,zp-128). This invalidates old results.
 * Only one thread may access an engine or its borrowed buffers at a time. */
pose_engine_status pose_engine_input(pose_engine *engine, int8_t **data, size_t *bytes);

/* Requires input() since preparation/the previous invoke. Runs synchronous
 * CPU inference, no camera/device/audio/worker/scheduler.
 * A failure invalidates output and requires release+prepare to recover. */
pose_engine_status pose_engine_invoke(pose_engine *engine);

/* Views are valid only after a successful invoke, until input/invoke/release.
 * Output spans are never copied. Neither heads() nor decode() runs inference. */
pose_engine_status pose_engine_heads(const pose_engine *engine, pose_movenet_heads *out);
pose_engine_status pose_engine_decode(const pose_engine *engine,
                                     pose_movenet_landmark out[POSE_MOVENET_JOINTS],
                                     pose_movenet_trace *trace);
size_t pose_engine_arena_used_bytes(const pose_engine *engine);

#ifdef __cplusplus
}
#endif
#endif
