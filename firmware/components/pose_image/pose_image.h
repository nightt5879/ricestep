#ifndef RICESTEP_POSE_IMAGE_H
#define RICESTEP_POSE_IMAGE_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

enum {
    PI_INPUT_WIDTH = 192,
    PI_INPUT_HEIGHT = 192,
    PI_INPUT_CHANNELS = 3,
    PI_INPUT_BYTES = 192 * 192 * 3,
    PI_MAX_DIMENSION = 4096,
    PI_MAX_STRIDE_BYTES = 16384
};

typedef enum {
    /* A pixel is RRRRRGGG GGGBBBBB. LE stores its low byte first. */
    PI_RGB565_LE,
    PI_RGB565_BE,
    /* Three successive bytes R, G, B, independent of CPU endianness. */
    PI_RGB888
} pi_pixel_format;

typedef struct {
    const uint8_t *data;
    size_t data_size;
    size_t stride_bytes; /* Explicit distance between row starts; no auto-stride. */
    uint32_t width, height;
    pi_pixel_format format;
} pi_frame;

typedef struct {
    uint32_t source_width, source_height;
    uint32_t resized_width, resized_height;
    uint32_t pad_left, pad_top;
} pi_transform;

typedef struct {
    float x, y;         /* Normalized to the original frame, NOT the square tensor. */
    float image_aspect; /* source_width / source_height, for motion_logic. */
} pi_source_point;

typedef enum {
    PI_OK,
    PI_INVALID_ARGUMENT,
    PI_UNSUPPORTED_FORMAT,
    PI_INVALID_DIMENSIONS,
    PI_INVALID_STRIDE,
    PI_BUFFER_TOO_SMALL,
    PI_BUFFER_OVERLAP,
    PI_INVALID_TRANSFORM,
    PI_OUTSIDE_IMAGE
} pi_status;

/* No allocation, I/O, camera ownership, rotation, mirror or model inference.
 * Produces NHWC [1,192,192,3] RGB INT8: byte = RGB uint8 - 128, scale=1,
 * zero_point=-128. Black letterbox = -128. Caller owns all buffers.
 * Bilinear at src = (dst + 0.5) * source / resized - 0.5, edge-clamped
 * neighbors. Exact integer weights; floor final positive RGB once, no antialias.
 * Fit the long dimension to 192; round the short dimension half-up, minimum 1.
 * Split odd padding with the extra pixel at right/bottom.
 * Input pixels, output tensor and output transform must not overlap.
 * On validation error neither tensor nor transform is changed; excess output
 * capacity is untouched. All frame bytes must remain stable during this call.
 */
pi_status pi_prepare(const pi_frame *frame, int8_t *tensor,
                     size_t tensor_size, pi_transform *transform);

/* Map decoded full-tensor normalized x/y back to the original frame. Does NOT
 * add a half pixel, rotate, mirror, or clamp. Image boundary coordinates 0/1 are
 * allowed; padding/outside/nonfinite points are rejected. Caller must mark a
 * rejected landmark's confidence as zero before passing it to motion_logic.
 * Only transforms produced by pi_prepare are accepted. On failure, out is
 * unchanged. Keypoint confidence/order/subject tracking are the caller's job.
 */
pi_status pi_unletterbox(const pi_transform *transform, float model_x,
                         float model_y, pi_source_point *out);

#ifdef __cplusplus
}
#endif
#endif
