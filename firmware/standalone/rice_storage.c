#include "rice_standalone.h"
#include "miniapp_storage.h"
#include "lisa_kv.h"
#include "easyflash.h"
#include "FreeRTOS.h"
#include "task.h"
#include <string.h>

#define RECORD_MAGIC 0x52504331u
typedef struct {
    uint32_t magic, version, generation, length, checksum;
    char data[MINIAPP_STORAGE_MAX_BYTES + 1];
} rice_record;
static const char *keys[] = {"rice.pet.a", "rice.pet.b"};
static TickType_t s_written_at;
static bool s_written;

static uint32_t checksum(const rice_record *record)
{
    uint32_t crc = 0xffffffffu;
    /* Include generation/length as well as bytes. Struct has five uint32_t
     * headers and byte data; no compiler padding is included in the checksum. */
    const uint32_t words[] = {record->magic, record->version, record->generation, record->length};
    for (size_t i = 0; i < sizeof(words) + record->length; ++i) {
        uint8_t byte = i < sizeof(words) ?
            (uint8_t)(words[i / 4] >> ((i % 4) * 8)) : (uint8_t)record->data[i - sizeof(words)];
        crc ^= byte;
        for (unsigned bit = 0; bit < 8; ++bit) crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1u)));
    }
    return ~crc;
}

static bool read_slot(unsigned slot, rice_record *record)
{
    size_t length = 0;
    ef_get_env_blob(keys[slot], NULL, 0, &length);
    if (length != sizeof(*record)) return false;
    if (ef_get_env_blob(keys[slot], record, sizeof(*record), NULL) != sizeof(*record)) return false;
    return record->magic == RECORD_MAGIC && record->version == 1 &&
        record->length <= MINIAPP_STORAGE_MAX_BYTES &&
        record->data[record->length] == 0 && record->checksum == checksum(record);
}

static int latest(rice_record *result)
{
    rice_record second;
    bool a = read_slot(0, result), b = read_slot(1, &second);
    if (b && (!a || (int32_t)(second.generation - result->generation) > 0)) {
        *result = second;
        return 1;
    }
    return a ? 0 : -1;
}

bool rice_storage_owns(const char *id) { return id && strcmp(id, RICE_APP_ID) == 0; }

static uint32_t legacy_u32(const uint8_t *bytes)
{
    return (uint32_t)bytes[0] | ((uint32_t)bytes[1] << 8) |
        ((uint32_t)bytes[2] << 16) | ((uint32_t)bytes[3] << 24);
}

static int import_legacy(char *data, size_t *size)
{
    /* Presence, not record validity, gates this one-time migration. A corrupt,
     * unreadable or empty native KV object must never revive legacy progress. */
    struct env_node_obj object;
    if (ef_get_env_obj(keys[0], &object) || ef_get_env_obj(keys[1], &object)) return 0;
    static const char *legacy_keys[] = {
        "miniapp.save.0", "miniapp.save.1", "miniapp.save.2", "miniapp.save.3"
    };
    static const char legacy_id[] = "local:ricestep";
    enum { LEGACY_BYTES = 1176, LEGACY_ID_OFFSET = 16, LEGACY_DATA_OFFSET = 144 };
    _Alignas(4) uint8_t record[LEGACY_BYTES];
    for (unsigned slot = 0; slot < sizeof(legacy_keys) / sizeof(legacy_keys[0]); ++slot) {
        size_t stored_size = 0;
        ef_get_env_blob(legacy_keys[slot], NULL, 0, &stored_size);
        if (stored_size != sizeof(record) ||
            ef_get_env_blob(legacy_keys[slot], record, sizeof(record), NULL) != sizeof(record)) continue;
        uint32_t length = legacy_u32(record + 4);
        uint64_t expires = (uint64_t)legacy_u32(record + 8) | ((uint64_t)legacy_u32(record + 12) << 32);
        /* Stock wire layout is little endian: magic/size, signed int64 expiry,
         * 128-byte ID, 1025-byte JSON buffer, then ABI tail padding. Validate a
         * positive expiry, but offline boot cannot enforce elapsed wall time. */
        if (legacy_u32(record) != UINT32_C(0x4d415031) || !length || length > MINIAPP_STORAGE_MAX_BYTES ||
            !expires || expires > INT64_MAX ||
            memcmp(record + LEGACY_ID_OFFSET, legacy_id, sizeof(legacy_id)) != 0 ||
            record[LEGACY_DATA_OFFSET + length] != 0 || memchr(record + LEGACY_DATA_OFFSET, 0, length)) continue;
        /* Do not expose uncommitted data or alter/delete any legacy key. */
        if (rice_storage_save((const char *)record + LEGACY_DATA_OFFSET, length)) return 0;
        memcpy(data, record + LEGACY_DATA_OFFSET, length + 1u);
        *size = length;
        return 1;
    }
    return 0;
}

int rice_storage_load(char *data, size_t *size)
{
    if (!data || !size) return 0;
    rice_record record;
    if (latest(&record) < 0) return import_legacy(data, size);
    if (!record.length) return 0;
    memcpy(data, record.data, record.length + 1);
    *size = record.length;
    return 1;
}

const char *rice_storage_save(const char *data, size_t size)
{
    if (!data || !size || size > MINIAPP_STORAGE_MAX_BYTES || memchr(data, 0, size)) return "invalid_data";
    rice_record record;
    int previous = latest(&record);
    if (previous >= 0 && record.length == size && memcmp(record.data, data, size) == 0) return NULL;
    TickType_t now = xTaskGetTickCount();
    if (s_written && now - s_written_at < pdMS_TO_TICKS(MINIAPP_STORAGE_WRITE_INTERVAL_MS)) return "rate_limited";
    uint32_t generation = previous >= 0 ? record.generation + 1u : 1u;
    memset(&record, 0, sizeof(record));
    record.magic = RECORD_MAGIC;
    record.version = 1;
    record.generation = generation;
    record.length = (uint32_t)size;
    memcpy(record.data, data, size);
    record.checksum = checksum(&record);
    unsigned next = previous == 0 ? 1 : 0;
    if (lisa_kv_set_blob(keys[next], (uint8_t *)&record, sizeof(record)) != 0) return "io_error";
    s_written = true;
    s_written_at = now;
    return NULL;
}

const char *rice_storage_clear(void)
{
    /* Write a newer empty tombstone instead of deleting both slots. A power
     * failure during erase must not resurrect an older save on next boot. */
    rice_record record;
    int previous = latest(&record);
    uint32_t generation = previous >= 0 ? record.generation + 1u : 1u;
    memset(&record, 0, sizeof(record));
    record.magic = RECORD_MAGIC;
    record.version = 1;
    record.generation = generation;
    record.checksum = checksum(&record);
    unsigned next = previous == 0 ? 1 : 0;
    if (lisa_kv_set_blob(keys[next], (uint8_t *)&record, sizeof(record)) != 0) return "io_error";
    /* A successful clear must survive loss of either slot: retire the previous
     * data only after the first tombstone is committed. If this second write
     * fails, report an I/O error instead of promising durable erasure. */
    if (lisa_kv_set_blob(keys[1u - next], (uint8_t *)&record, sizeof(record)) != 0) return "io_error";
    s_written = true;
    s_written_at = xTaskGetTickCount();
    return NULL;
}
