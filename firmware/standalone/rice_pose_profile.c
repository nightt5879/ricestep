#include "rice_pose_profile.h"
/* Retained ELF metadata. The packer reads these exact bytes from CP's load
 * image, verifies its bin, and refuses mismatched layout/disabled runtime. */
const uint32_t rice_pose_profile[12] = {
    0x45434952u, 0x45534f50u, 1u, 1u, /* RICEPOSE, profile 1, runtime 1 */
    RICE_POSE_TONE_ADDRESS, RICE_POSE_TONE_BYTES,
    RICE_POSE_WINDOW_ADDRESS, RICE_POSE_WINDOW_BYTES,
    RICE_POSE_FONTS_ADDRESS, RICE_POSE_FONTS_BYTES,
    RICE_POSE_EMOJI_ADDRESS, RICE_POSE_EMOJI_BYTES
};
