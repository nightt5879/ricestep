/* Compile the real implementation, with only its KV and tick dependencies mocked.
 * Direct access to its static BSS is used solely to simulate a cold reboot. */
#include <stdlib.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include "../../firmware/standalone/rice_storage.c"

typedef struct { uint8_t bytes[2048]; size_t size; bool present; } mock_blob;
static mock_blob flash_slots[6]; /* native A/B, then fixed legacy slots 0..3 */
static TickType_t tick;
static unsigned checks, writes;
static int read_failure = -1, short_read = -1;
static unsigned fail_write_number;
static bool truncate_failed_write;
#define CHECK(condition) do { ++checks; if (!(condition)) { \
    fprintf(stderr, "Check failed: %s, line %d (KV writes=%u)\n", #condition, __LINE__, writes); exit(1); \
} } while (0)

static int slot_for(const char *key)
{
    if (!strcmp(key, "rice.pet.a")) return 0;
    if (!strcmp(key, "rice.pet.b")) return 1;
    if (!strcmp(key, "miniapp.save.0")) return 2;
    if (!strcmp(key, "miniapp.save.1")) return 3;
    if (!strcmp(key, "miniapp.save.2")) return 4;
    if (!strcmp(key, "miniapp.save.3")) return 5;
    CHECK(false); return -1;
}
TickType_t xTaskGetTickCount(void) { return tick; }
bool ef_get_env_obj(const char *key, env_node_obj_t object)
{
    int slot = slot_for(key);
    memset(object, 0, sizeof(*object));
    return flash_slots[slot].present || flash_slots[slot].size > 0;
}
size_t ef_get_env_blob(const char *key, void *buffer, size_t capacity, size_t *stored_size)
{
    int slot = slot_for(key);
    size_t available = slot == read_failure ? 0 : flash_slots[slot].size;
    if (stored_size) *stored_size = available;
    if (!buffer || !capacity) return 0;
    size_t amount = available < capacity ? available : capacity;
    if (slot == short_read && amount) amount--;
    memcpy(buffer, flash_slots[slot].bytes, amount);
    return amount;
}
int lisa_kv_set_blob(const char *key, uint8_t *data, int len)
{
    int slot = slot_for(key);
    CHECK(slot < 2); /* Legacy keys are never written by migration or normal use. */
    CHECK(len > 0 && (size_t)len <= sizeof(flash_slots[slot].bytes));
    writes++;
    if (writes == fail_write_number) {
        if (truncate_failed_write) {
            flash_slots[slot].present = true;
            flash_slots[slot].size = (size_t)len / 2;
            memcpy(flash_slots[slot].bytes, data, flash_slots[slot].size);
        }
        return -1;
    }
    flash_slots[slot].size = (size_t)len;
    flash_slots[slot].present = true;
    memcpy(flash_slots[slot].bytes, data, (size_t)len);
    return 0;
}

static void reboot_at(TickType_t now)
{
    /* Simulate reset of implementation BSS, while flash contents survive. */
    s_written = false; s_written_at = 0; tick = now;
    read_failure = short_read = -1; fail_write_number = 0; truncate_failed_write = false;
}
static void fresh(void)
{
    memset(flash_slots, 0, sizeof(flash_slots)); writes = 0; reboot_at(0);
}
static void advance_interval(void) { tick += pdMS_TO_TICKS(MINIAPP_STORAGE_WRITE_INTERVAL_MS); }
static void status(const char *actual, const char *expected)
{
    CHECK(expected ? actual && strcmp(actual, expected) == 0 : actual == NULL);
}
static void expect_data(const char *expected)
{
    char out[MINIAPP_STORAGE_MAX_BYTES + 1]; size_t size = 0;
    int found = rice_storage_load(out, &size);
    CHECK(found == (expected != NULL));
    if (expected) { CHECK(size == strlen(expected)); CHECK(strcmp(out, expected) == 0); }
}
static int newest_slot(void)
{
    rice_record record;
    int slot = latest(&record); CHECK(slot >= 0); return slot;
}
static void corrupt_slot(int slot)
{
    CHECK(flash_slots[slot].size > 16);
    flash_slots[slot].bytes[16] ^= 0x40; /* CRC field, not a JSON parser approximation. */
}

static void save_reboot_and_rate_tests(void)
{
    fresh(); expect_data(NULL);
    CHECK(rice_storage_owns("ricestep"));
    CHECK(!rice_storage_owns(NULL)); CHECK(!rice_storage_owns("RiceStep")); CHECK(!rice_storage_owns("ricestep/other"));
    status(rice_storage_save("{\"rice\":3}", 10), NULL); expect_data("{\"rice\":3}");
    CHECK(writes == 1);
    status(rice_storage_save("{\"rice\":3}", 10), NULL); CHECK(writes == 1);
    tick = pdMS_TO_TICKS(MINIAPP_STORAGE_WRITE_INTERVAL_MS) - 1;
    status(rice_storage_save("{\"rice\":4}", 10), "rate_limited"); CHECK(writes == 1);
    tick++;
    status(rice_storage_save("{\"rice\":4}", 10), NULL); CHECK(writes == 2);
    reboot_at(0); expect_data("{\"rice\":4}");
    status(rice_storage_save("{\"rice\":4}", 10), NULL); CHECK(writes == 2);
    /* No wall-clock API is linked. A boot with ticks=0 still loads/saves. */
    status(rice_storage_save("{\"rice\":5}", 10), NULL);
    reboot_at(UINT32_MAX - 400u); expect_data("{\"rice\":5}");
    status(rice_storage_save("{\"rice\":6}", 10), NULL);
    tick += pdMS_TO_TICKS(MINIAPP_STORAGE_WRITE_INTERVAL_MS) - 1;
    status(rice_storage_save("{\"rice\":7}", 10), "rate_limited");
    tick++; status(rice_storage_save("{\"rice\":7}", 10), NULL); expect_data("{\"rice\":7}");
}

static void corruption_and_interruption_tests(void)
{
    fresh(); status(rice_storage_save("older", 5), NULL); advance_interval();
    status(rice_storage_save("newest", 6), NULL);
    int newest = newest_slot();
    mock_blob pristine = flash_slots[newest];
    corrupt_slot(newest); reboot_at(0); expect_data("older");
    flash_slots[newest] = pristine; read_failure = newest; expect_data("older");
    read_failure = -1; short_read = newest; expect_data("older");
    short_read = -1; expect_data("newest");

    for (unsigned truncate = 0; truncate < 2; ++truncate) {
        fresh(); status(rice_storage_save("committed", 9), NULL); advance_interval();
        fail_write_number = writes + 1; truncate_failed_write = truncate != 0;
        status(rice_storage_save("interrupted", 11), "io_error");
        reboot_at(0); expect_data("committed");
        status(rice_storage_save("retry", 5), NULL); expect_data("retry");
    }
    fresh(); status(rice_storage_save("first", 5), NULL);
    rice_record record; memcpy(&record, flash_slots[0].bytes, sizeof(record));
    record.generation = UINT32_MAX; record.checksum = checksum(&record);
    memcpy(flash_slots[0].bytes, &record, sizeof(record));
    advance_interval(); status(rice_storage_save("wrapped", 7), NULL);
    reboot_at(0); expect_data("wrapped"); /* Generation UINT32_MAX -> 0. */
}

static void clear_tests(void)
{
    fresh(); status(rice_storage_save("private progress", 16), NULL);
    status(rice_storage_clear(), NULL); reboot_at(0); expect_data(NULL);
    /* A successful clear must not resurrect saved progress after either slot's
     * read failure/corruption. This specifically catches a lone tombstone. */
    mock_blob after_clear[2]; memcpy(after_clear, flash_slots, sizeof(after_clear));
    for (int slot = 0; slot < 2; ++slot) {
        memcpy(flash_slots, after_clear, sizeof(after_clear));
        corrupt_slot(slot); reboot_at(0); expect_data(NULL);
        memcpy(flash_slots, after_clear, sizeof(after_clear));
        read_failure = slot; expect_data(NULL); read_failure = -1;
    }
    memcpy(flash_slots, after_clear, sizeof(after_clear)); reboot_at(0);
    fail_write_number = writes + 1; truncate_failed_write = true;
    status(rice_storage_save("new after clear", 15), "io_error");
    reboot_at(0); expect_data(NULL);

    fresh(); status(rice_storage_save("keep on failed clear", 20), NULL);
    fail_write_number = writes + 1; truncate_failed_write = true;
    status(rice_storage_clear(), "io_error"); reboot_at(0); expect_data("keep on failed clear");

    fresh(); status(rice_storage_save("old private progress", 20), NULL);
    /* Power loss after first tombstone, while retiring the previous data slot. */
    fail_write_number = writes + 2; truncate_failed_write = true;
    status(rice_storage_clear(), "io_error"); reboot_at(0); expect_data(NULL);
    status(rice_storage_clear(), NULL); reboot_at(0); expect_data(NULL);
    status(rice_storage_save("restarted", 9), NULL); expect_data("restarted");
}

static void limits_tests(void)
{
    fresh();
    char input[MINIAPP_STORAGE_MAX_BYTES + 1]; memset(input, 'x', sizeof(input));
    status(rice_storage_save(NULL, 10), "invalid_data");
    status(rice_storage_save(input, 0), "invalid_data");
    status(rice_storage_save(input, sizeof(input)), "invalid_data");
    status(rice_storage_save(input, SIZE_MAX), "invalid_data");
    input[10] = 0; status(rice_storage_save(input, 100), "invalid_data"); input[10] = 'x';
    CHECK(writes == 0);
    status(rice_storage_save(input, MINIAPP_STORAGE_MAX_BYTES), NULL);
    char output[MINIAPP_STORAGE_MAX_BYTES + 2]; memset(output, 0x5a, sizeof(output)); size_t size = 0;
    CHECK(rice_storage_load(output, &size) == 1); CHECK(size == MINIAPP_STORAGE_MAX_BYTES);
    CHECK(memcmp(input, output, size) == 0); CHECK(output[size] == 0); CHECK(output[size + 1] == 0x5a);
    CHECK(rice_storage_load(NULL, &size) == 0); CHECK(rice_storage_load(output, NULL) == 0);
    reboot_at(0); CHECK(rice_storage_load(output, &size) == 1); CHECK(size == MINIAPP_STORAGE_MAX_BYTES);
}

static void wire_u32(uint8_t *out, uint32_t value)
{
    for (unsigned i = 0; i < 4; ++i) out[i] = (uint8_t)(value >> (8u * i));
}
static mock_blob *legacy_fixture(unsigned slot, const char *id, const char *data, size_t length)
{
    CHECK(slot < 4 && strlen(id) < 128 && length <= 1024);
    mock_blob *blob = &flash_slots[slot + 2];
    memset(blob, 0, sizeof(*blob)); blob->present = true; blob->size = 1176;
    wire_u32(blob->bytes, UINT32_C(0x4d415031)); wire_u32(blob->bytes + 4, (uint32_t)length);
    wire_u32(blob->bytes + 8, 1); /* Positive but ancient: no wall-clock needed. */
    memcpy(blob->bytes + 16, id, strlen(id) + 1);
    memcpy(blob->bytes + 144, data, length);
    return blob;
}

static void legacy_import_tests(void)
{
    for (unsigned slot = 0; slot < 4; ++slot) {
        fresh();
        mock_blob *legacy = legacy_fixture(slot, "local:ricestep", "{\"rice\":8}", 10);
        mock_blob untouched = *legacy;
        expect_data("{\"rice\":8}"); CHECK(writes == 1);
        CHECK(memcmp(legacy, &untouched, sizeof(untouched)) == 0);
        reboot_at(0); expect_data("{\"rice\":8}"); CHECK(writes == 1);
        legacy_fixture(slot, "local:ricestep", "older changed", 13);
        expect_data("{\"rice\":8}"); CHECK(writes == 1);
        status(rice_storage_clear(), NULL); reboot_at(0); expect_data(NULL);
        corrupt_slot(0); corrupt_slot(1); reboot_at(0); expect_data(NULL);
    }
    /* Neither a zero-length KV object nor unreadable/corrupt native data is absence. */
    for (unsigned slot = 0; slot < 2; ++slot) {
        for (unsigned length = 0; length < 2; ++length) {
            fresh(); legacy_fixture(0, "local:ricestep", "old", 3);
            flash_slots[slot].present = true; flash_slots[slot].size = length;
            expect_data(NULL); CHECK(writes == 0);
            read_failure = (int)slot; expect_data(NULL); CHECK(writes == 0);
        }
    }
    fresh(); legacy_fixture(0, "other-app", "wrong", 5); legacy_fixture(3, "local:ricestep", "right", 5);
    expect_data("right"); CHECK(writes == 1);

    for (unsigned kind = 0; kind < 14; ++kind) {
        fresh(); mock_blob *legacy = legacy_fixture(0, "local:ricestep", "payload", 7);
        switch (kind) {
        case 0: legacy->size = 1175; break;
        case 1: legacy->size = 1177; break;
        case 2: legacy->bytes[0] ^= 1; break;
        case 3: wire_u32(legacy->bytes + 4, 0); break;
        case 4: wire_u32(legacy->bytes + 4, 1025); break;
        case 5: wire_u32(legacy->bytes + 4, UINT32_MAX); break;
        case 6: memset(legacy->bytes + 8, 0, 8); break;
        case 7: legacy->bytes[15] = 0x80; break;
        case 8: legacy->bytes[16 + strlen("local:ricestep")] = 'x'; break;
        case 9: memset(legacy->bytes + 16, 'x', 128); break;
        case 10: legacy->bytes[144 + 7] = 'x'; break;
        case 11: legacy->bytes[144 + 3] = 0; break;
        case 12: short_read = 2; break;
        case 13: legacy_fixture(0, "ricestep", "wrong namespace", 15); break;
        }
        mock_blob untouched = *legacy;
        expect_data(NULL); CHECK(writes == 0);
        CHECK(memcmp(legacy, &untouched, sizeof(untouched)) == 0);
    }

    for (unsigned truncated = 0; truncated < 2; ++truncated) {
        fresh(); mock_blob *legacy = legacy_fixture(0, "local:ricestep", "carry progress", 14);
        mock_blob untouched = *legacy;
        fail_write_number = 1; truncate_failed_write = truncated != 0;
        char out[MINIAPP_STORAGE_MAX_BYTES + 1] = "unchanged"; size_t size = 42;
        CHECK(rice_storage_load(out, &size) == 0);
        CHECK(strcmp(out, "unchanged") == 0 && size == 42); CHECK(writes == 1);
        CHECK(memcmp(legacy, &untouched, sizeof(untouched)) == 0);
        reboot_at(0);
        /* A failed write leaving no native object can retry. A partial native
         * object blocks import after reboot, rather than reviving legacy data. */
        expect_data(truncated ? NULL : "carry progress"); CHECK(writes == (truncated ? 1u : 2u));
    }
    fresh();
    char maximum[MINIAPP_STORAGE_MAX_BYTES + 1]; memset(maximum, 'm', sizeof(maximum)); maximum[1024] = 0;
    legacy_fixture(0, "local:ricestep", maximum, 1024);
    expect_data(maximum); CHECK(writes == 1);
}

int main(void)
{
    save_reboot_and_rate_tests(); corruption_and_interruption_tests(); clear_tests(); limits_tests(); legacy_import_tests();
    printf("Standalone storage: %u assertions passed against real rice_storage.c (mock KV/ticks).\n", checks);
    return 0;
}
