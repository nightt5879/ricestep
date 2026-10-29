#ifndef RICE_POSE_PROFILE_H
#define RICE_POSE_PROFILE_H
#include <stdint.h>
/* This layout is only valid with mini3-pose-v1 resources; never stock resources. */
#define RICE_POSE_TONE_ADDRESS 0x30100000u
#define RICE_POSE_TONE_BYTES 0x80000u
#define RICE_POSE_WINDOW_ADDRESS 0x30180000u
#define RICE_POSE_WINDOW_BYTES 0x300000u
#define RICE_POSE_FONTS_ADDRESS 0x30480000u
#define RICE_POSE_FONTS_BYTES 0x100000u
#define RICE_POSE_EMOJI_ADDRESS 0x30580000u
#define RICE_POSE_EMOJI_BYTES 0x80000u
extern const uint32_t rice_pose_profile[12];
#endif
