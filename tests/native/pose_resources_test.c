#define _CRT_SECURE_NO_WARNINGS
#include "pose_resources.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static unsigned assertions, calls;
static const uint8_t *reference;
static int callback_failure, wrong_digest;
#define CHECK(condition) do { ++assertions; if (!(condition)) { fprintf(stderr, "line %d: %s\n", __LINE__, #condition); exit(1); } } while (0)

/* Test double for the hash provider: verify exact bytes against a separately
 * loaded JS-verified package, then supply its pinned digest. Production must
 * inject a real SHA-256 implementation. The packager tests exercise real SHA. */
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
int main(int argc, char **argv)
{
    CHECK(argc == 2);
    FILE *f = fopen(argv[1], "rb"); CHECK(f != NULL);
    uint8_t *raw = malloc(RP_WINDOW_BYTES + 16u);
    uint8_t *golden = malloc(RP_WINDOW_BYTES);
    CHECK(raw != NULL && golden != NULL);
    uint8_t *window = (uint8_t *)(((uintptr_t)raw + 15u) & ~(uintptr_t)15u);
    CHECK(fread(golden, 1, RP_WINDOW_BYTES, f) == RP_WINDOW_BYTES);
    CHECK(fgetc(f) == EOF); fclose(f);
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
    printf("pose resource validator: %u assertions passed (injected hash test double).\n", assertions);
    return 0;
}
