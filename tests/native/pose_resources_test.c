#define _CRT_SECURE_NO_WARNINGS
#include "pose_resources.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static unsigned assertions, calls;
static const uint8_t *reference;
static int callback_failure, wrong_digest;
#define CHECK(condition) do { ++assertions; if (!(condition)) { fprintf(stderr, "line %d: %s\n", __LINE__, #condition); exit(1); } } while (0)

/* Test double for the hash provider: compare exact bytes with the test's
 * reference, then supply its header digest. This is NOT a real SHA-256 check.
 * The default reference is explicitly synthetic; optional file input must be
 * verified with JS validateModelWindow() before invoking this executable. */
static int digest(void *context, const uint8_t *data, size_t bytes, uint8_t out[32])
{
    ++calls;
    CHECK(context == &calls);
    CHECK(bytes == RP_MODEL_BYTES);
    CHECK(data != NULL);
    if (callback_failure) return -1;
    memcpy(out, reference + 64, 32);
    if (wrong_digest || memcmp(data, reference + RP_HEADER_BYTES, bytes)) out[0] ^= 1;
    return 0;
}
static void rejected(uint8_t *window, size_t length, rp_status status, unsigned expected_calls)
{
    rp_model_view view = {window, length};
    calls = 0;
    CHECK(rp_model_validate(window, length, digest, &calls, &view) == status);
    CHECK(view.data == NULL && view.bytes == 0);
    CHECK(calls == expected_calls);
}
static void write_u32(uint8_t *p, uint32_t value)
{
    for (unsigned i = 0; i < 4; ++i) p[i] = (uint8_t)(value >> (i * 8));
}
static void synthetic_reference(uint8_t *bytes)
{
    /* Explicit wire fixture only. This is not a trained/usable TFLite model,
     * and its bytes do not have the model digest written in the header. */
    static const uint8_t model_sha[32] = {
        0x90,0x51,0xa5,0xce,0xeb,0xc1,0xeb,0x5f,0x53,0x5c,0x1b,0x1a,0xfe,0x58,0xd3,0x3c,
        0xe8,0x78,0xbf,0x93,0x58,0xa1,0x59,0xcb,0xfc,0x68,0x9c,0x1a,0xbe,0xbf,0x9c,0x68
    };
    static const uint8_t source_sha[32] = {
        0xcd,0x7c,0xc2,0x2f,0xa9,0x46,0xe5,0xd1,0x46,0xa7,0xb9,0x8d,0x49,0x68,0x53,0xe1,
        0x92,0x3e,0x22,0x82,0x8d,0x39,0x72,0xd5,0x79,0x97,0x3f,0x27,0xf9,0x1b,0xb1,0x05
    };
    const uint32_t fields[] = {1, 4096, 1, 1, 4096, 2891896, 3145728, 1, 3, 1};
    memset(bytes, 0xff, RP_WINDOW_BYTES);
    memset(bytes, 0, RP_HEADER_BYTES);
    memcpy(bytes, "RICEPOSE", 8);
    for (size_t i = 0; i < sizeof(fields) / sizeof(fields[0]); ++i) write_u32(bytes + 8 + i * 4, fields[i]);
    memcpy(bytes + 64, model_sha, sizeof(model_sha));
    memcpy(bytes + 96, source_sha, sizeof(source_sha));
    memset(bytes + RP_HEADER_BYTES, 0xa5, RP_MODEL_BYTES);
    write_u32(bytes + RP_HEADER_BYTES, 16);
    memcpy(bytes + RP_HEADER_BYTES + 4, "TFL3", 4);
}
int main(int argc, char **argv)
{
    CHECK(argc == 1 || argc == 2);
    uint8_t *raw = malloc(RP_WINDOW_BYTES + 16u);
    uint8_t *golden = malloc(RP_WINDOW_BYTES);
    CHECK(raw != NULL && golden != NULL);
    uint8_t *window = (uint8_t *)(((uintptr_t)raw + 15u) & ~(uintptr_t)15u);
    if (argc == 2) {
        FILE *f = fopen(argv[1], "rb"); CHECK(f != NULL);
        CHECK(fread(golden, 1, RP_WINDOW_BYTES, f) == RP_WINDOW_BYTES);
        CHECK(fgetc(f) == EOF); fclose(f);
    } else synthetic_reference(golden);
    reference = golden; memcpy(window, reference, RP_WINDOW_BYTES);
    rp_model_view view;
    CHECK(rp_model_validate(window, RP_WINDOW_BYTES, digest, &calls, &view) == RP_OK);
    CHECK(calls == 1 && view.data == window + RP_HEADER_BYTES && view.bytes == RP_MODEL_BYTES);
    CHECK((uintptr_t)view.data % 16u == 0);

    CHECK(rp_model_validate(window, RP_WINDOW_BYTES, digest, &calls, NULL) == RP_BAD_ARGUMENT);
    CHECK(rp_model_validate(window, RP_WINDOW_BYTES, NULL, &calls, &view) == RP_BAD_ARGUMENT);
    CHECK(view.data == NULL && view.bytes == 0);
    rejected(NULL, RP_WINDOW_BYTES, RP_BAD_ARGUMENT, 0);
    const size_t lengths[] = {0, 4095, RP_HEADER_BYTES, RP_WINDOW_BYTES - 1u, RP_WINDOW_BYTES + 1u, SIZE_MAX};
    for (size_t i = 0; i < sizeof(lengths) / sizeof(lengths[0]); ++i) rejected(window, lengths[i], RP_BAD_SIZE, 0);
    rejected(window + 1, RP_WINDOW_BYTES, RP_BAD_ALIGNMENT, 0);
    const size_t offsets[] = {0,7,8,12,16,20,24,28,32,36,40,44,48,63,64,95,96,127,128,4095};
    for (size_t i = 0; i < sizeof(offsets) / sizeof(offsets[0]); ++i) {
        window[offsets[i]] ^= 1; rejected(window, RP_WINDOW_BYTES, RP_BAD_HEADER, 0); window[offsets[i]] ^= 1;
    }
    window[RP_HEADER_BYTES + 4] ^= 1;
    rejected(window, RP_WINDOW_BYTES, RP_BAD_MODEL, 0);
    window[RP_HEADER_BYTES + 4] ^= 1;
    uint8_t root[4]; memcpy(root, window + RP_HEADER_BYTES, sizeof(root));
    memset(window + RP_HEADER_BYTES, 0xff, 4);
    rejected(window, RP_WINDOW_BYTES, RP_BAD_MODEL, 0);
    memcpy(window + RP_HEADER_BYTES, root, sizeof(root));
    window[RP_HEADER_BYTES + 1000] ^= 1;
    rejected(window, RP_WINDOW_BYTES, RP_HASH_MISMATCH, 1);
    window[RP_HEADER_BYTES + 1000] ^= 1;
    callback_failure = 1; rejected(window, RP_WINDOW_BYTES, RP_HASH_FAILED, 1); callback_failure = 0;
    wrong_digest = 1; rejected(window, RP_WINDOW_BYTES, RP_HASH_MISMATCH, 1); wrong_digest = 0;
    /* Execution ignores erased tail padding; the JS package verifier checks it. */
    window[RP_WINDOW_BYTES - 1u] = 0;
    CHECK(rp_model_validate(window, RP_WINDOW_BYTES, digest, &calls, &view) == RP_OK);
    free(raw); free(golden);
    printf("pose resource validator: %u assertions passed (%s; injected hash test double, no cryptographic or hardware claim).\n",
        assertions, argc == 1 ? "synthetic header/model bytes, no real model" : "explicit prepared model-window bytes");
    return 0;
}
