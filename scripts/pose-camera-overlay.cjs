'use strict';
// Pinned app-service and camera allocation overlay. No hardware, SDK-cache or HAL writes.
const names = ['init', 'capture', 'stop', 'suspend', 'resume', 'get_framesize', 'is_inited'];
function buildCameraPatches(read) {
  const file = 'apps/arcs-mini/services/service_camera.c';
  const source = read(file).toString().replace(/\r\n/g, '\n');
  for (const name of names) {
    const signature = new RegExp('^(?:int|bool) service_camera_' + name + '\\(', 'gm');
    if ([...source.matchAll(signature)].length !== 1) throw new Error(`Pinned camera source mismatch: ${name}`);
  }
  const before = '#ifdef CONFIG_RICESTEP_POSE\n' + names.map(n => `#define service_camera_${n} rice_camera_impl_${n}`).join('\n') + '\n#endif\n';
  const after = '\n#ifdef CONFIG_RICESTEP_POSE\n' + names.map(n => `#undef service_camera_${n}`).join('\n') + '\n#include "../standalone/rice_pose_camera.inc"\n#endif\n';
  const driver = 'arcs-sdk/drivers/lisa_camera/lisa_camera.c';
  const original = read(driver).toString().replace(/\r\n/g, '\n');
  const anchor = '#include <lisa_typedef.h>\n';
  if (original.split(anchor).length !== 2 || !/^#define MAX_BUF_NUM\s+5$/m.test(original)) {
    throw new Error('Pinned camera driver allocation anchors changed');
  }
  for (const [name, count] of Object.entries({ lisa_mem_align_alloc: 2, lisa_mem_free: 4,
      lisa_queue_create: 2, lisa_queue_delete: 2 })) {
    if ([...original.matchAll(new RegExp('\\b' + name + '\\s*\\(', 'g'))].length !== count) {
      throw new Error(`Pinned camera allocator call sites changed: ${name}`);
    }
  }
  const allocation = '#ifdef CONFIG_RICESTEP_POSE\n' +
    '#include "../../../apps/arcs-mini/standalone/rice_pose_camera_memory.h"\n' +
    '#define lisa_mem_align_alloc rice_pose_camera_fb_alloc\n' +
    '#define lisa_mem_free rice_pose_camera_fb_free\n' +
    '#define lisa_queue_create rice_pose_camera_queue_create\n' +
    '#define lisa_queue_delete rice_pose_camera_queue_delete\n' +
    '#endif\n';
  return { [file]: before + source + after, [driver]: original.replace(anchor, anchor + allocation) };
}
module.exports = { buildCameraPatches };
