#ifndef RICESTEP_POSE_CAMERA_H
#define RICESTEP_POSE_CAMERA_H
#include <stdint.h>
enum { RICE_CAMERA_BUSY = -80, RICE_CAMERA_QUARANTINED = -81,
       RICE_CAMERA_NOT_OWNER = -82, RICE_CAMERA_CLEANUP_FAILED = -83 };
/* Worker-only lease API. Claim does not power or initialize the sensor. All
 * public legacy service_camera calls are serialized by the same bridge. */
int service_camera_pose_claim(void);
int service_camera_pose_init(void);
int service_camera_pose_capture(uint8_t *buffer, uint32_t bytes);
int service_camera_pose_get_framesize(uint16_t *width, uint16_t *height);
int service_camera_pose_release(void);
#endif
