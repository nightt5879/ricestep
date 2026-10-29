'use strict';
// Pinned app-service overlay only. No hardware, SDK-cache or HAL writes.
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
  return { [file]: before + source + after };
}
module.exports = { buildCameraPatches };
