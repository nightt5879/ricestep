#include "pose_resources.h"
#include <string.h>

static const uint8_t model_sha256[32] = {
    0x90,0x51,0xa5,0xce,0xeb,0xc1,0xeb,0x5f,0x53,0x5c,0x1b,0x1a,0xfe,0x58,0xd3,0x3c,
    0xe8,0x78,0xbf,0x93,0x58,0xa1,0x59,0xcb,0xfc,0x68,0x9c,0x1a,0xbe,0xbf,0x9c,0x68
};
static const uint8_t source_sha256[32] = {
    0xcd,0x7c,0xc2,0x2f,0xa9,0x46,0xe5,0xd1,0x46,0xa7,0xb9,0x8d,0x49,0x68,0x53,0xe1,
    0x92,0x3e,0x22,0x82,0x8d,0x39,0x72,0xd5,0x79,0x97,0x3f,0x27,0xf9,0x1b,0xb1,0x05
};
static uint32_t read_u32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

rp_status rp_model_validate(const uint8_t *window, size_t length,
                            rp_sha256_fn sha256, void *context, rp_model_view *out)
{
    if (!out) return RP_BAD_ARGUMENT;
    out->data = NULL;
    out->bytes = 0;
    if (!window || !sha256) return RP_BAD_ARGUMENT;
    if (length != RP_WINDOW_BYTES) return RP_BAD_SIZE;
    if ((uintptr_t)window % 16u) return RP_BAD_ALIGNMENT;
    const uint32_t fields[] = {1, RP_HEADER_BYTES, RP_PROFILE_VERSION, 1, RP_HEADER_BYTES,
        RP_MODEL_BYTES, RP_WINDOW_BYTES, RP_DECODER_CONTRACT_VERSION, 3, RP_INPUT_CONTRACT_VERSION};
    if (memcmp(window, "RICEPOSE", 8)) return RP_BAD_HEADER;
    for (size_t i = 0; i < sizeof(fields) / sizeof(fields[0]); ++i) {
        if (read_u32(window + 8u + i * 4u) != fields[i]) return RP_BAD_HEADER;
    }
    for (size_t i = 48; i < 64; ++i) if (window[i]) return RP_BAD_HEADER;
    if (memcmp(window + 64, model_sha256, 32) || memcmp(window + 96, source_sha256, 32)) return RP_BAD_HEADER;
    for (size_t i = 128; i < RP_HEADER_BYTES; ++i) if (window[i]) return RP_BAD_HEADER;
    const uint8_t *model = window + RP_HEADER_BYTES;
    uint32_t root = read_u32(model);
    if (memcmp(model + 4, "TFL3", 4) || root < 8u || root >= RP_MODEL_BYTES - 4u || root % 4u) return RP_BAD_MODEL;
    uint8_t digest[32] = {0};
    if (sha256(context, model, RP_MODEL_BYTES, digest)) return RP_HASH_FAILED;
    if (memcmp(digest, model_sha256, sizeof(digest))) return RP_HASH_MISMATCH;
    out->data = model;
    out->bytes = RP_MODEL_BYTES;
    return RP_OK;
}
