/* Synthetic pixels only: checks byte/geometry contracts, NOT model accuracy. */
#include "pose_image.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static unsigned checks;
#define CHECK(condition) do { ++checks; if (!(condition)) { \
    fprintf(stderr, "%s:%d: %s\n", __FILE__, __LINE__, #condition); exit(1); } } while (0)
#define NEAR(a, b) CHECK(fabs((double)(a) - (double)(b)) < 0.000001)

/* Guarded buffers remain outside the component; it never allocates storage. */
static uint8_t source[400000];
static uint8_t copy[400000];
static struct {
    uint32_t before;
    _Alignas(pi_transform) int8_t bytes[PI_INPUT_BYTES];
    uint32_t after;
} output;

static pi_frame frame(uint32_t w, uint32_t h, pi_pixel_format format, size_t stride)
{
    pi_frame f = {source, sizeof(source), stride, w, h, format};
    return f;
}

static void reset_output(void)
{
    output.before = UINT32_C(0x12345678);
    output.after = UINT32_C(0xabcdef01);
    memset(output.bytes, 0x5a, sizeof(output.bytes));
}

static void guards(void)
{
    CHECK(output.before == UINT32_C(0x12345678));
    CHECK(output.after == UINT32_C(0xabcdef01));
}

static void pixel(unsigned x, unsigned y, unsigned r, unsigned g, unsigned b)
{
    size_t i = (y * PI_INPUT_WIDTH + x) * 3U;
    CHECK(output.bytes[i] == (int)r - 128);
    CHECK(output.bytes[i + 1] == (int)g - 128);
    CHECK(output.bytes[i + 2] == (int)b - 128);
}

static void test_rgb888_identity_stride_and_bounds(void)
{
    const size_t stride = 192U * 3U + 11U;
    pi_frame f = frame(192, 192, PI_RGB888, stride);
    pi_transform t;
    unsigned x, y, c;
    memset(source, 0xd7, sizeof(source));
    for (y = 0; y < 192; ++y)
        for (x = 0; x < 192; ++x)
            for (c = 0; c < 3; ++c)
                source[y * stride + x * 3 + c] = (uint8_t)((x * 17 + y * 31 + c * 83) % 256U);
    f.data_size = 191U * stride + 192U * 3U; /* Last-row padding is not required. */
    memcpy(copy, source, sizeof(source));
    reset_output();
    CHECK(pi_prepare(&f, output.bytes, sizeof(output.bytes), &t) == PI_OK);
    CHECK(t.pad_left == 0 && t.pad_top == 0 && t.resized_width == 192 && t.resized_height == 192);
    for (y = 0; y < 192; ++y)
        for (x = 0; x < 192; ++x)
            for (c = 0; c < 3; ++c)
                CHECK(output.bytes[(y * 192 + x) * 3 + c] == (int)source[y * stride + x * 3 + c] - 128);
    CHECK(memcmp(copy, source, sizeof(source)) == 0);
    guards();
}

static void test_rgb565_explicit_byte_order(void)
{
    static const struct { uint16_t value; unsigned r, g, b; } colors[] = {
        {0x0000, 0, 0, 0}, {0xffff, 255, 255, 255}, {0xf800, 255, 0, 0},
        {0x07e0, 0, 255, 0}, {0x001f, 0, 0, 255}, {0x7bef, 123, 125, 123},
        {0x8410, 132, 130, 132}, {0x0821, 8, 4, 8}
    };
    unsigned order;
    size_t i;
    for (order = 0; order < 2; ++order) {
        for (i = 0; i < sizeof(colors) / sizeof(colors[0]); ++i) {
            pi_frame f = frame(1, 1, order == 0 ? PI_RGB565_LE : PI_RGB565_BE, 2);
            pi_transform t;
            source[order] = (uint8_t)colors[i].value;
            source[1U - order] = (uint8_t)(colors[i].value >> 8U);
            f.data_size = 2;
            reset_output();
            CHECK(pi_prepare(&f, output.bytes, sizeof(output.bytes), &t) == PI_OK);
            pixel(0, 0, colors[i].r, colors[i].g, colors[i].b);
            pixel(96, 96, colors[i].r, colors[i].g, colors[i].b);
            pixel(191, 191, colors[i].r, colors[i].g, colors[i].b);
            guards();
        }
    }
    /* Unaligned two-byte input is legal; no uint16_t pointer cast is used. */
    {
        const uint8_t unaligned[] = {0xaa, 0xf8, 0x00};
        pi_frame f = {unaligned + 1, 2, 2, 1, 1, PI_RGB565_BE};
        pi_transform t;
        CHECK(pi_prepare(&f, output.bytes, sizeof(output.bytes), &t) == PI_OK);
        pixel(11, 17, 255, 0, 0);
    }
}

static void test_extra_output_capacity_untouched(void)
{
    static int8_t larger[PI_INPUT_BYTES + 17];
    pi_frame f = frame(1, 1, PI_RGB888, 3);
    pi_transform t;
    size_t i;
    source[0] = 0; source[1] = 128; source[2] = 255;
    memset(larger, 0x57, sizeof(larger));
    CHECK(pi_prepare(&f, larger, sizeof(larger), &t) == PI_OK);
    CHECK(larger[0] == -128 && larger[1] == 0 && larger[2] == 127);
    for (i = PI_INPUT_BYTES; i < sizeof(larger); ++i) CHECK(larger[i] == 0x57);
}

static void fill_coordinates(unsigned width, unsigned height)
{
    unsigned x, y;
    for (y = 0; y < height; ++y)
        for (x = 0; x < width; ++x) {
            size_t i = (y * width + x) * 3U;
            source[i] = (uint8_t)x;
            source[i + 1] = (uint8_t)y;
            source[i + 2] = (uint8_t)(x + y);
        }
}

static void test_mini3_letterbox_and_center_sampling(void)
{
    pi_frame f = frame(320, 240, PI_RGB888, 320U * 3U);
    pi_transform t;
    unsigned x, y;
    fill_coordinates(320, 240);
    f.data_size = 320U * 240U * 3U;
    reset_output();
    CHECK(pi_prepare(&f, output.bytes, sizeof(output.bytes), &t) == PI_OK);
    CHECK(t.source_width == 320 && t.source_height == 240);
    CHECK(t.resized_width == 192 && t.resized_height == 144 && t.pad_left == 0 && t.pad_top == 24);
    for (y = 0; y < 192; ++y)
        if (y < 24 || y >= 168)
            for (x = 0; x < 192; ++x) pixel(x, y, 0, 0, 0);
    /* Independent golden samples: floor((d+.5)*320/192), likewise for y. */
    pixel(0, 24, 0, 0, 0);
    pixel(1, 25, 2, 2, 4);
    pixel(95, 95, 159, 119, 22);
    pixel(96, 96, 160, 120, 24);
    pixel(190, 166, 61, 237, 42);
    pixel(191, 167, 63, 239, 46);
    guards();
}

static void test_portrait_odd_and_extreme_aspect(void)
{
    pi_transform t;
    pi_frame f = frame(240, 320, PI_RGB888, 720);
    fill_coordinates(240, 320);
    reset_output();
    CHECK(pi_prepare(&f, output.bytes, sizeof(output.bytes), &t) == PI_OK);
    CHECK(t.resized_width == 144 && t.resized_height == 192 && t.pad_left == 24 && t.pad_top == 0);
    pixel(23, 100, 0, 0, 0);
    pixel(168, 100, 0, 0, 0);
    pixel(24, 0, 0, 0, 0);
    pixel(25, 1, 2, 2, 4);
    pixel(167, 191, 239, 63, 46);

    memset(source, 255, sizeof(source));
    f = frame(7, 5, PI_RGB888, 21);
    CHECK(pi_prepare(&f, output.bytes, sizeof(output.bytes), &t) == PI_OK);
    CHECK(t.resized_width == 192 && t.resized_height == 137 && t.pad_top == 27);
    pixel(191, 26, 0, 0, 0);
    pixel(191, 27, 255, 255, 255);
    pixel(191, 163, 255, 255, 255);
    pixel(191, 164, 0, 0, 0); /* 27 pixels above, 28 below. */

    f = frame(4096, 1, PI_RGB888, PI_MAX_STRIDE_BYTES);
    f.data_size = 4096U * 3U;
    CHECK(pi_prepare(&f, output.bytes, sizeof(output.bytes), &t) == PI_OK);
    CHECK(t.resized_height == 1 && t.pad_top == 95);
    pixel(191, 94, 0, 0, 0);
    pixel(191, 95, 255, 255, 255);
    pixel(191, 96, 0, 0, 0);
    f = frame(1, 4096, PI_RGB888, 3);
    f.data_size = 4096U * 3U;
    CHECK(pi_prepare(&f, output.bytes, sizeof(output.bytes), &t) == PI_OK);
    CHECK(t.resized_width == 1 && t.pad_left == 95);
    pixel(94, 191, 0, 0, 0);
    pixel(95, 191, 255, 255, 255);
    pixel(96, 191, 0, 0, 0);
    guards();
}

static void invalid_frame(pi_frame f, pi_status expected)
{
    const pi_transform sentinel = {1, 2, 3, 4, 5, 6};
    pi_transform t = sentinel;
    size_t i;
    reset_output();
    CHECK(pi_prepare(&f, output.bytes, sizeof(output.bytes), &t) == expected);
    CHECK(memcmp(&t, &sentinel, sizeof(t)) == 0);
    for (i = 0; i < sizeof(output.bytes); ++i) CHECK(output.bytes[i] == 0x5a);
    guards();
}

static void test_rejection_and_no_partial_output(void)
{
    pi_frame good = frame(320, 240, PI_RGB888, 960), f;
    pi_transform t = {1, 2, 3, 4, 5, 6};
    f = good; f.data = NULL; invalid_frame(f, PI_INVALID_ARGUMENT);
    f = good; f.format = (pi_pixel_format)99; invalid_frame(f, PI_UNSUPPORTED_FORMAT);
    f = good; f.width = 0; invalid_frame(f, PI_INVALID_DIMENSIONS);
    f = good; f.height = 0; invalid_frame(f, PI_INVALID_DIMENSIONS);
    f = good; f.width = 4097; invalid_frame(f, PI_INVALID_DIMENSIONS);
    f = good; f.height = UINT32_MAX; invalid_frame(f, PI_INVALID_DIMENSIONS);
    f = good; f.stride_bytes = 959; invalid_frame(f, PI_INVALID_STRIDE);
    f = good; f.stride_bytes = 0; invalid_frame(f, PI_INVALID_STRIDE);
    f = good; f.stride_bytes = PI_MAX_STRIDE_BYTES + 1U; invalid_frame(f, PI_INVALID_STRIDE);
    f = good; f.stride_bytes = SIZE_MAX; invalid_frame(f, PI_INVALID_STRIDE);
    f = good; f.data_size = 320U * 240U * 3U - 1U; invalid_frame(f, PI_BUFFER_TOO_SMALL);
    CHECK(pi_prepare(NULL, output.bytes, sizeof(output.bytes), &t) == PI_INVALID_ARGUMENT);
    CHECK(pi_prepare(&good, NULL, sizeof(output.bytes), &t) == PI_INVALID_ARGUMENT);
    CHECK(pi_prepare(&good, output.bytes, sizeof(output.bytes), NULL) == PI_INVALID_ARGUMENT);
    CHECK(pi_prepare(&good, output.bytes, sizeof(output.bytes) - 1U, &t) == PI_BUFFER_TOO_SMALL);
    CHECK(t.source_width == 1 && t.pad_top == 6 && output.bytes[0] == 0x5a);

    f = frame(1, 1, PI_RGB888, 3);
    f.data = (const uint8_t *)output.bytes;
    invalid_frame(f, PI_BUFFER_OVERLAP);
    f = frame(1, 1, PI_RGB888, 3);
    CHECK(pi_prepare(&f, output.bytes, sizeof(output.bytes), (pi_transform *)(void *)output.bytes) == PI_BUFFER_OVERLAP);
    f.data = (const uint8_t *)(const void *)&t;
    f.data_size = sizeof(t);
    CHECK(pi_prepare(&f, output.bytes, sizeof(output.bytes), &t) == PI_BUFFER_OVERLAP);
    CHECK(t.source_width == 1 && t.pad_top == 6 && output.bytes[0] == 0x5a);
    guards();
}

static void test_inverse_geometry_and_bad_landmarks(void)
{
    pi_frame f = frame(320, 240, PI_RGB888, 960);
    pi_transform t, wrong;
    pi_source_point p = {11, 12, 13}, saved;
    CHECK(pi_prepare(&f, output.bytes, sizeof(output.bytes), &t) == PI_OK);
    CHECK(pi_unletterbox(&t, 0.25f, 0.5f, &p) == PI_OK);
    NEAR(p.x, 0.25); NEAR(p.y, 0.5); NEAR(p.image_aspect, 4.0 / 3.0);
    CHECK(pi_unletterbox(&t, 0, 0.125f, &p) == PI_OK);
    NEAR(p.x, 0); NEAR(p.y, 0);
    CHECK(pi_unletterbox(&t, 1, 0.875f, &p) == PI_OK);
    NEAR(p.x, 1); NEAR(p.y, 1);
    CHECK(pi_unletterbox(&t, 0.5f, 0.25f, &p) == PI_OK);
    NEAR(p.y, 1.0 / 6.0); /* No extra half-pixel adjustment. */
    saved = p;
    CHECK(pi_unletterbox(&t, 0.5f, 0.1249f, &p) == PI_OUTSIDE_IMAGE);
    CHECK(pi_unletterbox(&t, 0.5f, 0.8751f, &p) == PI_OUTSIDE_IMAGE);
    CHECK(pi_unletterbox(&t, -0.001f, 0.5f, &p) == PI_OUTSIDE_IMAGE);
    CHECK(pi_unletterbox(&t, 1.001f, 0.5f, &p) == PI_OUTSIDE_IMAGE);
    CHECK(pi_unletterbox(&t, NAN, 0.5f, &p) == PI_INVALID_ARGUMENT);
    CHECK(pi_unletterbox(&t, 0.5f, INFINITY, &p) == PI_INVALID_ARGUMENT);
    CHECK(pi_unletterbox(NULL, 0.5f, 0.5f, &p) == PI_INVALID_ARGUMENT);
    CHECK(pi_unletterbox(&t, 0.5f, 0.5f, NULL) == PI_INVALID_ARGUMENT);
    wrong = t; wrong.pad_top++;
    CHECK(pi_unletterbox(&wrong, 0.5f, 0.5f, &p) == PI_INVALID_TRANSFORM);
    wrong = t; wrong.resized_width = 0;
    CHECK(pi_unletterbox(&wrong, 0.5f, 0.5f, &p) == PI_INVALID_TRANSFORM);
    wrong = t; wrong.source_height = UINT32_MAX;
    CHECK(pi_unletterbox(&wrong, 0.5f, 0.5f, &p) == PI_INVALID_TRANSFORM);
    CHECK(memcmp(&p, &saved, sizeof(p)) == 0);

    f = frame(240, 320, PI_RGB888, 720);
    CHECK(pi_prepare(&f, output.bytes, sizeof(output.bytes), &t) == PI_OK);
    CHECK(pi_unletterbox(&t, 0.25f, 0.5f, &p) == PI_OK);
    NEAR(p.x, 1.0 / 6.0); NEAR(p.y, 0.5); NEAR(p.image_aspect, 0.75);
    CHECK(pi_unletterbox(&t, 0.12f, 0.5f, &p) == PI_OUTSIDE_IMAGE);

    f = frame(7, 5, PI_RGB888, 21);
    CHECK(pi_prepare(&f, output.bytes, sizeof(output.bytes), &t) == PI_OK);
    CHECK(pi_unletterbox(&t, 0.25f, (27.0f + 137.0f * 0.75f) / 192.0f, &p) == PI_OK);
    NEAR(p.x, 0.25); NEAR(p.y, 0.75); NEAR(p.image_aspect, 1.4);
    CHECK(pi_unletterbox(&t, 0, 27.0f / 192.0f, &p) == PI_OK);
    NEAR(p.y, 0);
    CHECK(pi_unletterbox(&t, 1, 164.0f / 192.0f, &p) == PI_OK);
    NEAR(p.y, 1);
    CHECK(pi_unletterbox(&t, 1, nextafterf(164.0f / 192.0f, INFINITY), &p) == PI_OUTSIDE_IMAGE);
}

int main(void)
{
    test_rgb888_identity_stride_and_bounds();
    test_rgb565_explicit_byte_order();
    test_extra_output_capacity_untouched();
    test_mini3_letterbox_and_center_sampling();
    test_portrait_odd_and_extreme_aspect();
    test_rejection_and_no_partial_output();
    test_inverse_geometry_and_bad_landmarks();
    printf("pose_image: %u checks passed (synthetic pixels; no camera/model claim).\n", checks);
    return 0;
}
