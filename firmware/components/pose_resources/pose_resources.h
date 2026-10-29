#ifndef RICESTEP_POSE_RESOURCES_H
#define RICESTEP_POSE_RESOURCES_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

#define RP_HEADER_BYTES UINT32_C(4096)
#define RP_MODEL_BYTES UINT32_C(2891896)
#define RP_WINDOW_BYTES UINT32_C(3145728)
#define RP_FLASH_WINDOW_OFFSET UINT32_C(0x180000)
#define RP_MODEL_FLASH_ADDRESS UINT32_C(0x30181000)
#define RP_PROFILE_VERSION UINT32_C(1)
#define RP_DECODER_CONTRACT_VERSION UINT32_C(1)
#define RP_INPUT_CONTRACT_VERSION UINT32_C(1)

typedef enum {
    RP_OK = 0, RP_BAD_ARGUMENT, RP_BAD_SIZE, RP_BAD_ALIGNMENT,
    RP_BAD_HEADER, RP_BAD_MODEL, RP_HASH_FAILED, RP_HASH_MISMATCH
} rp_status;
typedef struct { const uint8_t *data; size_t bytes; } rp_model_view;
/* Return zero after writing the SHA-256 digest, otherwise fail closed. */
typedef int (*rp_sha256_fn)(void *context, const uint8_t *data, size_t bytes, uint8_t digest[32]);

/* Validate a caller-supplied, 16-byte-aligned readable 3 MiB span. No Flash,
 * hardware or allocation API is used. Header fields and hashes are pinned to
 * v1; full model bytes are verified through the required callback. Does not
 * validate all FlatBuffer/schema/ops: the inference engine does that next.
 * The view is cleared on failure. Keep the validated span immutable/alive for
 * the whole interpreter lifetime; out must not overlap that span. Trailing FF
 * padding is a packaging check, not required for model execution. */
rp_status rp_model_validate(const uint8_t *window, size_t length,
                            rp_sha256_fn sha256, void *context, rp_model_view *out);
#ifdef __cplusplus
}
#endif
#endif
