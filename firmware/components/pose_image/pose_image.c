#include "pose_image.h"

#include <math.h>
#include <stdbool.h>

static bool dimensions_valid(uint32_t width, uint32_t height)
{
    return width >= 1U && width <= PI_MAX_DIMENSION &&
           height >= 1U && height <= PI_MAX_DIMENSION;
}

static pi_transform make_transform(uint32_t width, uint32_t height)
{
    pi_transform t;
    t.source_width = width;
    t.source_height = height;
    if (width >= height) {
        t.resized_width = PI_INPUT_WIDTH;
        t.resized_height = (height * PI_INPUT_HEIGHT + width / 2U) / width;
        if (t.resized_height == 0U) t.resized_height = 1U;
    } else {
        t.resized_height = PI_INPUT_HEIGHT;
        t.resized_width = (width * PI_INPUT_WIDTH + height / 2U) / height;
        if (t.resized_width == 0U) t.resized_width = 1U;
    }
    t.pad_left = (PI_INPUT_WIDTH - t.resized_width) / 2U;
    t.pad_top = (PI_INPUT_HEIGHT - t.resized_height) / 2U;
    return t;
}

/* uintptr_t range arithmetic avoids comparing pointers to unrelated objects.
 * Check for wrap before constructing the end address. */
static bool address_range_valid(const void *data, size_t length)
{
    return data != NULL && length <= UINTPTR_MAX - (uintptr_t)data;
}

static bool overlaps(const void *a, size_t a_size, const void *b, size_t b_size)
{
    const uintptr_t begin_a = (uintptr_t)a, begin_b = (uintptr_t)b;
    return begin_a < begin_b + b_size && begin_b < begin_a + a_size;
}

static void unpack(const uint8_t *p, pi_pixel_format format, uint8_t rgb[3])
{
    uint32_t value, red, green, blue;
    if (format == PI_RGB888) {
        rgb[0] = p[0]; rgb[1] = p[1]; rgb[2] = p[2];
        return;
    }
    value = format == PI_RGB565_LE ? (uint32_t)p[0] | ((uint32_t)p[1] << 8U)
                                    : ((uint32_t)p[0] << 8U) | (uint32_t)p[1];
    red = value >> 11U;
    green = (value >> 5U) & 63U;
    blue = value & 31U;
    /* Bit replication, including exact 0 and 255 endpoints. */
    rgb[0] = (uint8_t)((red << 3U) | (red >> 2U));
    rgb[1] = (uint8_t)((green << 2U) | (green >> 4U));
    rgb[2] = (uint8_t)((blue << 3U) | (blue >> 2U));
}

typedef struct {
    uint16_t first, second, fraction;
} sample_axis;

/* Exact half-pixel coordinate: ((2*d+1)*source-resized)/(2*resized).
 * C division truncates negative values, so correct it to floor before clamping
 * each neighbor. Fractions are integer numerators with denominator 2*resized. */
static sample_axis axis_sample(uint32_t d, uint32_t source, uint32_t resized)
{
    const int32_t denominator = (int32_t)(2U * resized);
    const int32_t numerator = (int32_t)((2U * d + 1U) * source) - (int32_t)resized;
    int32_t first = numerator / denominator;
    int32_t fraction = numerator % denominator;
    int32_t second;
    sample_axis sample;
    if (fraction < 0) { --first; fraction += denominator; }
    second = first + 1;
    sample.first = (uint16_t)(first < 0 ? 0 : first >= (int32_t)source ? source - 1U : (uint32_t)first);
    sample.second = (uint16_t)(second < 0 ? 0 : second >= (int32_t)source ? source - 1U : (uint32_t)second);
    sample.fraction = (uint16_t)fraction;
    return sample;
}

pi_status pi_prepare(const pi_frame *frame, int8_t *tensor,
                     size_t tensor_size, pi_transform *transform)
{
    pi_frame f;
    pi_transform t;
    size_t bytes_per_pixel, row_bytes, required;
    uint32_t y, x;
    sample_axis columns[PI_INPUT_WIDTH];
    size_t i;
    if (frame == NULL || tensor == NULL || transform == NULL) return PI_INVALID_ARGUMENT;
    f = *frame; /* The descriptor itself need not outlive the call. */
    if (f.data == NULL) return PI_INVALID_ARGUMENT;
    if (f.format != PI_RGB565_LE && f.format != PI_RGB565_BE && f.format != PI_RGB888)
        return PI_UNSUPPORTED_FORMAT;
    if (!dimensions_valid(f.width, f.height)) return PI_INVALID_DIMENSIONS;
    bytes_per_pixel = f.format == PI_RGB888 ? 3U : 2U;
    row_bytes = f.width * bytes_per_pixel;
    if (f.stride_bytes < row_bytes || f.stride_bytes > PI_MAX_STRIDE_BYTES)
        return PI_INVALID_STRIDE;
    if ((size_t)(f.height - 1U) > (SIZE_MAX - row_bytes) / f.stride_bytes)
        return PI_INVALID_STRIDE;
    required = (f.height - 1U) * f.stride_bytes + row_bytes;
    if (f.data_size < required || tensor_size < PI_INPUT_BYTES) return PI_BUFFER_TOO_SMALL;
    if (!address_range_valid(f.data, required) || !address_range_valid(tensor, PI_INPUT_BYTES) ||
        !address_range_valid(transform, sizeof(*transform))) return PI_INVALID_ARGUMENT;
    if (overlaps(f.data, required, tensor, PI_INPUT_BYTES) ||
        overlaps(f.data, required, transform, sizeof(*transform)) ||
        overlaps(tensor, PI_INPUT_BYTES, transform, sizeof(*transform))) return PI_BUFFER_OVERLAP;

    t = make_transform(f.width, f.height);
    for (x = 0; x < t.resized_width; ++x)
        columns[x] = axis_sample(x, f.width, t.resized_width);
    for (i = 0; i < PI_INPUT_BYTES; ++i) tensor[i] = -128;
    for (y = 0; y < t.resized_height; ++y) {
        const sample_axis rows = axis_sample(y, f.height, t.resized_height);
        const uint32_t dx = 2U * t.resized_width, dy = 2U * t.resized_height;
        const uint32_t denominator = dx * dy;
        const uint8_t *row0 = f.data + rows.first * f.stride_bytes;
        const uint8_t *row1 = f.data + rows.second * f.stride_bytes;
        for (x = 0; x < t.resized_width; ++x) {
            const sample_axis column = columns[x];
            const uint32_t w00 = (dx - column.fraction) * (dy - rows.fraction);
            const uint32_t w01 = column.fraction * (dy - rows.fraction);
            const uint32_t w10 = (dx - column.fraction) * rows.fraction;
            const uint32_t w11 = column.fraction * rows.fraction;
            uint8_t rgb00[3], rgb01[3], rgb10[3], rgb11[3];
            unsigned channel;
            const size_t dest = ((y + t.pad_top) * PI_INPUT_WIDTH + x + t.pad_left) * 3U;
            unpack(row0 + column.first * bytes_per_pixel, f.format, rgb00);
            unpack(row0 + column.second * bytes_per_pixel, f.format, rgb01);
            unpack(row1 + column.first * bytes_per_pixel, f.format, rgb10);
            unpack(row1 + column.second * bytes_per_pixel, f.format, rgb11);
            for (channel = 0; channel < 3U; ++channel) {
                /* Weights sum to denominator <= 384*384. The largest possible
                 * sum is 255*147456=37,601,280, safely within uint32_t. Divide
                 * only once: exact rational interpolation, final RGB floor. */
                const uint32_t sum = rgb00[channel] * w00 + rgb01[channel] * w01 +
                                     rgb10[channel] * w10 + rgb11[channel] * w11;
                tensor[dest + channel] = (int8_t)((int)(sum / denominator) - 128);
            }
        }
    }
    *transform = t;
    return PI_OK;
}

pi_status pi_unletterbox(const pi_transform *transform, float model_x,
                         float model_y, pi_source_point *out)
{
    pi_transform expected;
    pi_source_point p;
    float left, right, top, bottom;
    if (transform == NULL || out == NULL) return PI_INVALID_ARGUMENT;
    if (!dimensions_valid(transform->source_width, transform->source_height))
        return PI_INVALID_TRANSFORM;
    expected = make_transform(transform->source_width, transform->source_height);
    if (transform->resized_width != expected.resized_width ||
        transform->resized_height != expected.resized_height ||
        transform->pad_left != expected.pad_left || transform->pad_top != expected.pad_top)
        return PI_INVALID_TRANSFORM;
    if (!isfinite(model_x) || !isfinite(model_y)) return PI_INVALID_ARGUMENT;
    /* Compare and interpolate using the same representable normalized edges:
     * e.g. the exact bottom edge 164/192 cannot be represented as a float.
     * This accepts its float representation without clamping outside points. */
    left = (float)transform->pad_left / PI_INPUT_WIDTH;
    right = (float)(transform->pad_left + transform->resized_width) / PI_INPUT_WIDTH;
    top = (float)transform->pad_top / PI_INPUT_HEIGHT;
    bottom = (float)(transform->pad_top + transform->resized_height) / PI_INPUT_HEIGHT;
    if (model_x < left || model_x > right || model_y < top || model_y > bottom)
        return PI_OUTSIDE_IMAGE;
    p.x = (model_x - left) / (right - left);
    p.y = (model_y - top) / (bottom - top);
    p.image_aspect = (float)transform->source_width / (float)transform->source_height;
    *out = p;
    return PI_OK;
}
