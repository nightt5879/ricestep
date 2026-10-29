/* Compiled against the exact pinned SDK ROMFS, using heap/log host shims only.
 * Compares the original images with the candidate's relocated address windows. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include "romfs.h"
static unsigned files_checked;
static uint8_t *read_file(const char *path, size_t *size)
{
    FILE *f = fopen(path, "rb"); assert(f);
    assert(fseek(f, 0, SEEK_END) == 0); long n = ftell(f); assert(n > 0 && n <= 5242880);
    *size = (size_t)n; rewind(f);
    uint8_t *data = malloc(*size); assert(data);
    assert(fread(data, 1, *size, f) == *size); assert(fclose(f) == 0); return data;
}
static void walk(struct romfs *original, struct romfs *moved, const char *dir, unsigned depth)
{
    assert(depth <= 8);
    struct romfs_dir_iter iter;
    assert(romfs_dir_iter_start(original, dir, &iter) == 0);
    char path[ROMFS_PATH_MAX]; uint8_t *data; uint32_t size; bool is_dir; int rc;
    while ((rc = romfs_dir_iter_next(&iter, path, sizeof(path), &data, &size, &is_dir)) == 0) {
        if (is_dir) { assert(romfs_isdir(moved, path)); walk(original, moved, path, depth + 1); }
        else {
            uint8_t *other = NULL; uint32_t other_size = 0;
            assert(romfs_info_get(moved, path, &other, &other_size) == 0);
            assert(size > 0 && size == other_size && data != other && memcmp(data, other, size) == 0);
            assert(++files_checked <= 4096);
        }
    }
    assert(rc == 1);
}
int main(int argc, char **argv)
{
    assert(argc == 4);
    size_t image_size; uint8_t *image = read_file(argv[3], &image_size);
    assert(image_size == 0x500000);
    for (unsigned i = 0; i < 2; ++i) {
        size_t original_size; uint8_t *original = read_file(argv[i + 1], &original_size);
        struct romfs *a = NULL, *b = NULL;
        const uint32_t offset = i == 0 ? 0x380000 : 0x480000;
        const uint32_t capacity = i == 0 ? 0x100000 : 0x80000;
        assert(original_size < capacity && offset + capacity <= image_size);
        assert(romfs_init(&a, original, (uint32_t)original_size) == 0);
        assert(romfs_init(&b, image + offset, capacity) == 0);
        if (i == 0) {
            assert(romfs_isfile(b, "/font/lv_font_chinese_16.bin"));
            assert(romfs_isfile(b, "/font/lv_font_rubik_bold_64.bin"));
        }
        const unsigned before = files_checked;
        walk(a, b, "/", 0); assert(files_checked > before);
        assert(romfs_deinit(&a) == 0 && romfs_deinit(&b) == 0); free(original);
    }
    free(image);
    printf("Pinned SDK ROMFS: %u original and relocated font/emoji files match; no hardware test.\n", files_checked);
    return 0;
}
