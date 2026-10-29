'use strict';
// Reproducible, opt-in source overlay. This script has no network/device/flash operations.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const cp = require('node:child_process');
const { buildPack } = require('./generate-native-voice.cjs');
const { minifyLua } = require('./minify-lua.cjs');
const ROOT = path.resolve(__dirname, '..');
const SDK_COMMIT = 'c0a95bcf3e45c7bd783ba4e997df19c96ffaaf35';
const LUA = Object.freeze({ version: '5.4.8', url: 'https://www.lua.org/ftp/lua-5.4.8.tar.gz',
  sha256: '4f18ddae154e793e46eeab727c59ef1c0c0c2b744e7b94219710d76f530629ae' });
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const normalize = text => text.replace(/\r\n/g, '\n');
const guard = body => `#ifdef CONFIG_RICESTEP_STANDALONE\n${body}\n#endif\n`;
const except = body => `#ifndef CONFIG_RICESTEP_STANDALONE\n${body}\n#endif`;

function replaceOnce(source, before, after, label) {
  const index = source.indexOf(before);
  if (index < 0 || source.indexOf(before, index + before.length) >= 0) {
    throw new Error(`Expected one pinned source anchor: ${label}`);
  }
  return source.slice(0, index) + after + source.slice(index + before.length);
}

function buildPatches(read, { poseRuntime = false } = {}) {
  const patches = {};
  function edit(file, mutate) { patches[file] = mutate(normalize(read(file))); }
  edit('apps/arcs-mini/Kconfig', s => s + '\nconfig RICESTEP_STANDALONE\n    bool "Resident offline RiceStep companion"\n    depends on MINIAPP && BOARD_ARCS_MINI3\n    default n\n\nconfig RICESTEP_POSE\n    bool "RiceStep local camera exercise candidate"\n    depends on RICESTEP_STANDALONE && BOARD_ARCS_MINI3\n    default n\n');
  edit('apps/arcs-mini/arcs_mini3.conf', s => s + '\n# RiceStep opt-in standalone overlay; stock builds do not include this block.\nCONFIG_MINIAPP=y\nCONFIG_MINIAPP_ADB_DEBUG=n\nCONFIG_MINIAPP_BUZZER=n\nCONFIG_RICESTEP_STANDALONE=y\n# Keep application header/link dependencies; runtime startup is gated separately.\nCONFIG_ACOMP=y\nCONFIG_ACOMP_WAKEUP=y\nCONFIG_OTA=n\n');
  patches['apps/arcs-mini/arcs_mini3.conf'] += `CONFIG_RICESTEP_POSE=${poseRuntime ? 'y' : 'n'}\n`;
  edit('apps/arcs-mini/CMakeLists.txt', s => {
    s = replaceOnce(s, 'add_custom_target(\n    combined_hex ALL', 'if(NOT CONFIG_RICESTEP_POSE)\nadd_custom_target(\n    combined_hex ALL', 'gate stock resource packages');
    return replaceOnce(s, 'add_dependencies(ota_txz ${PROJECT_NAME})', 'add_dependencies(ota_txz ${PROJECT_NAME})\nendif() # Pose CP requires the separately verified matching resource bundle.', 'end stock package guard');
  });
  edit('apps/arcs-mini/miniapp/CMakeLists.txt', s => {
    s = replaceOnce(s, 'set(MINIAPP_LUA_DIR ${PROJECT_TOP_DIR}/arcs-sdk/samples/subsys/lua/lua_binding_lvgl/components/lua)',
      'if(CONFIG_RICESTEP_STANDALONE)\n    set(MINIAPP_LUA_DIR ${PROJECT_TOP_DIR}/third_party/ricestep-lua)\nelse()\n    set(MINIAPP_LUA_DIR ${PROJECT_TOP_DIR}/arcs-sdk/samples/subsys/lua/lua_binding_lvgl/components/lua)\nendif()', 'Lua source directory');
    return s + '\nif(CONFIG_RICESTEP_STANDALONE)\n    include(${CMAKE_CURRENT_SOURCE_DIR}/../standalone/integration.cmake)\nendif()\n';
  });
  edit('apps/arcs-mini/miniapp/miniapp_storage.c', s => {
    s = guard('#include "rice_standalone.h"') + s;
    for (const [signature, body] of [
      ['int miniapp_storage_load(const char *id, char *data, size_t *size)', '    if (rice_storage_owns(id)) return rice_storage_load(data, size);'],
      ['const char *miniapp_storage_save(const char *id, const char *data, size_t size, uint32_t ttl)', '    if (rice_storage_owns(id)) return rice_storage_save(data, size);'],
      ['const char *miniapp_storage_clear(const char *id)', '    if (rice_storage_owns(id)) return rice_storage_clear();'],
    ]) s = replaceOnce(s, signature + '\n{\n', signature + '\n{\n' + guard(body), signature);
    return s;
  });
  edit('apps/arcs-mini/miniapp/miniapp_runtime.c', s => {
    s = guard('#include "rice_standalone.h"') + s;
    s = '#ifdef CONFIG_RICESTEP_POSE\n#include "rice_pose.h"\n#endif\n' + s;
    s = replaceOnce(s, 'if (vm->storage_read) {', 'if (vm->storage_read\n#ifdef CONFIG_RICESTEP_STANDALONE\n        && !rice_storage_owns(vm->id)\n#endif\n    ) {', 'preserve builtin save after script fault');
    s = replaceOnce(s, 'if (vm->faulted || vm->storage_pending == 2 || !ls_sys_time_is_valid())',
      'if (vm->faulted || vm->storage_pending == 2 || (!ls_sys_time_is_valid()\n#ifdef CONFIG_RICESTEP_STANDALONE\n        && !rice_storage_owns(vm->id)\n#endif\n    ))', 'clockless load');
    s = replaceOnce(s, 'if (!ls_sys_time_is_valid()) return miniapp_storage_result(L, "clock_unavailable");',
      'if (!ls_sys_time_is_valid()\n#ifdef CONFIG_RICESTEP_STANDALONE\n        && !rice_storage_owns(vm->id)\n#endif\n    ) return miniapp_storage_result(L, "clock_unavailable");', 'clockless save');
    s = replaceOnce(s, 'static void miniapp_open_safe_libraries(lua_State *L)',
      guard('#include "ricestep_bindings.inc"') + '\nstatic void miniapp_open_safe_libraries(lua_State *L)', 'local audio bindings');
    s = replaceOnce(s, '    miniapp_json_open(L);', '    miniapp_json_open(L);\n' + guard('    rice_lua_audio_open(L);'), 'local audio registration');
    s = replaceOnce(s, '    miniapp_http_discard(vm);\n    miniapp_tts_stop();',
      guard('    rice_audio_set_active(false);') + '    miniapp_http_discard(vm);\n    miniapp_tts_stop();', 'stop retired audio');
    s = replaceOnce(s, '    miniapp_set_active(true);', '    miniapp_set_active(true);\n' + guard('    rice_audio_set_active(true);'), 'activate local audio');
    s = replaceOnce(s, '    rice_audio_set_active(false);', '    rice_audio_set_active(false);\n#ifdef CONFIG_RICESTEP_POSE\n    rice_pose_set_active(false);\n#endif', 'cancel pose on runtime stop');
    s = replaceOnce(s, '        (*current_vm)->retiring = true;', '        (*current_vm)->retiring = true;\n#ifdef CONFIG_RICESTEP_POSE\n        rice_pose_set_active(false);\n#endif', 'cancel pose before retiring VM');
    s = replaceOnce(s, '    rice_audio_set_active(true);', '    rice_audio_set_active(true);\n#ifdef CONFIG_RICESTEP_POSE\n    rice_pose_set_active(rice_storage_owns(candidate->id));\n#endif', 'authorize lazy pose start');
    s = replaceOnce(s, '    s_event_queue = xQueueCreate(8, sizeof(miniapp_event_t));',
      guard('    if (rice_audio_init() != 0) return -1;') + '    s_event_queue = xQueueCreate(8, sizeof(miniapp_event_t));', 'audio worker init');
    // Offline apps have no network-capable Lua surface. The stock code and
    // contracts are retained unchanged when standalone is disabled.
    const http = '    lua_newtable(L);\n    lua_setglobal(L, "http");\n    miniapp_register_function(L, "http", "request", miniapp_lua_http_request);\n    miniapp_register_function(L, "http", "get", miniapp_lua_http_get);\n    miniapp_register_function(L, "http", "cancel", miniapp_lua_http_cancel);';
    s = replaceOnce(s, http, except(http), 'omit HTTP Lua bindings');
    const tts = '    lua_newtable(L);\n    lua_setglobal(L, "tts");\n    miniapp_register_function(L, "tts", "speak", miniapp_lua_tts_speak);\n    miniapp_register_function(L, "tts", "cancel", miniapp_lua_tts_cancel);';
    return replaceOnce(s, tts, except(tts), 'omit cloud TTS Lua bindings');
  });
  edit('apps/arcs-mini/main.c', s => {
    s = guard('#include "standalone/rice_standalone.h"') + s;
    const subscriptions = s.slice(s.indexOf('    voice_msg_sub(VOICE_MSG_SYSTEM_NETWORK_PROBE_SUCCESS,'), s.indexOf('\n\n    service_led_init();'));
    s = replaceOnce(s, subscriptions, except(subscriptions), 'main cloud subscriptions');
    s = replaceOnce(s, '    service_camera_init();', except('    service_camera_init();'), 'camera not started in desk mode');
    s = replaceOnce(s, '    app_show_initial_wifi_info_if_needed();', '#ifdef CONFIG_RICESTEP_STANDALONE\n    (void)rice_standalone_start();\n#else\n    app_show_initial_wifi_info_if_needed();\n#endif', 'resident boot');
    s = replaceOnce(s, '        boot_watchdog_feed();', '        boot_watchdog_feed();\n' + guard('        rice_standalone_poll();'), 'resident retry');
    return s;
  });
  for (const file of ['src/category/comm/voice_player_comm.c', 'src/category/comm/voice_player_comm.h']) {
    edit(file, s => {
      if (!s.includes('#ifdef CONFIG_MINIAPP_BUZZER')) throw new Error(`Missing player guard in ${file}`);
      return s.replaceAll('#ifdef CONFIG_MINIAPP_BUZZER', '#if defined(CONFIG_MINIAPP_BUZZER) || defined(CONFIG_RICESTEP_STANDALONE)');
    });
  }
  edit('apps/arcs-mini/voice_player/voice_player.c', s => {
    const policy = '    if (voice_player_network_tone_init() != 0) {\n        LOGE("network tone policy init failed");\n    }\n    if (voice_player_wakeup_tone_init() != 0) {\n        LOGE("wakeup tone policy init failed");\n    }';
    return replaceOnce(s, policy, except(policy), 'no network/wakeup prompt policy');
  });
  edit('src/system/platform.c', s => {
    s = '#ifdef CONFIG_RICESTEP_POSE\n#include "rice_pose_profile.h"\n#endif\n' + s;
    for (const [name, value] of [['TONE_BIN_ADDR', 'RICE_POSE_TONE_ADDRESS'], ['TONE_BIN_SIZE', 'RICE_POSE_TONE_BYTES'], ['EMOJI_BIN_ADDR', 'RICE_POSE_EMOJI_ADDRESS'], ['EMOJI_BIN_SIZE', 'RICE_POSE_EMOJI_BYTES']]) {
      const matches = s.match(new RegExp(`^#define ${name} .+$`, 'gm')) || [];
      if (matches.length !== 1) throw new Error(`Expected resource macro ${name}`);
      s = replaceOnce(s, matches[0], `#ifdef CONFIG_RICESTEP_POSE\n#define ${name} ${value}\n#else\n${matches[0]}\n#endif`, name);
    }
    const networkStart = '    if (ipc_ready) {\n        network_probe_init();';
    s = replaceOnce(s, networkStart, '#ifndef CONFIG_RICESTEP_STANDALONE\n' + networkStart, 'network init start');
    s = replaceOnce(s, '\n#if CONFIG_ACOMP\n', '\n#endif /* !CONFIG_RICESTEP_STANDALONE */\n\n#if CONFIG_ACOMP && !defined(CONFIG_RICESTEP_STANDALONE)\n', 'network and algorithm init end');
    s = replaceOnce(s, '\tevs_utils_init();', guard('    /* The existing NULL-resource path initializes the audio device/DAC,\n     * then returns before creating or starting a wakeup recognition engine. */\n    if (app_wakeup_init(NULL) != 0) {\n        LOGE("RiceStep audio hardware initialization failed");\n    }') + '\tevs_utils_init();', 'offline playback hardware init');
    return replaceOnce(s, '    sys_network_manager_init(ipc_ready);', except('    sys_network_manager_init(ipc_ready);'), 'network manager not started');
  });
  edit('src/category/comm/msgs/voice_platform.c', s => replaceOnce(s,
    'int voice_platform_evt_init(void)\n{',
    'int voice_platform_evt_init(void)\n{\n' + guard('    return 0; /* Offline product never subscribes cloud connection/OTA events. */'),
    'cloud event init guard'));
  edit('apps-ui/assets/fonts/font_romfs.c', s => {
    s = '#ifdef CONFIG_RICESTEP_POSE\n#include "rice_pose_profile.h"\n#endif\n' + s;
    for (const [before, name, value] of [
      ['#define RESPAK_BIN_ADDR (CMN_FLASH_REGION + 0x00440000)', 'RESPAK_BIN_ADDR', 'RICE_POSE_FONTS_ADDRESS'],
      ['#define RESPAK_BIN_SIZE (1792 * 1024)', 'RESPAK_BIN_SIZE', 'RICE_POSE_FONTS_BYTES']]) {
      s = replaceOnce(s, before, `#ifdef CONFIG_RICESTEP_POSE\n#define ${name} ${value}\n#else\n${before}\n#endif`, name);
    }
    return s;
  });
  // Always retain ownership of these patched paths when switching profiles.
  Object.assign(patches, require('./pose-camera-overlay.cjs').buildCameraPatches(read));
  return patches;
}

function bundleFiles() {
  let source = '-- Resident RiceStep: generated; no network or PC dependency.\nRICESTEP_CONFIG={tts_enabled=true,voice_base_url="builtin://ricestep",sound_enabled=false}\n';
  const moduleHashes = {};
  for (const name of ['game.lua', 'character.lua', 'voice.lua', 'main.lua']) {
    const data = fs.readFileSync(path.join(ROOT, 'app', name));
    moduleHashes[name] = sha(data);
    source += normalize(data.toString('utf8')) + '\n';
  }
  if (!source.includes('base == "builtin://ricestep"')) throw new Error('app/voice.lua must explicitly support builtin://ricestep.');
  // Same concatenated chunk: game/dirty are the existing main.lua locals.
  source += '\nlocal rice_app_exit = on_exit\nfunction on_exit()\n    if type(rice_app_exit) == "function" then rice_app_exit() end\n    if game and type(storage) == "table" and type(storage.save) == "function" then\n        storage.save(game.export(), 2592000)\n    end\nend\n';
  source = minifyLua(source);
  const bytes = Buffer.from(source, 'utf8');
  if (bytes.length > 65536 || bytes.includes(0)) throw new Error('Resident Lua source exceeds the stock runtime limit or contains NUL.');
  const md5 = crypto.createHash('md5').update(bytes).digest('hex');
  const header = `#ifndef RICE_MINIAPP_BUNDLE_H\n#define RICE_MINIAPP_BUNDLE_H\n#define RICE_BUNDLE_SIZE ${bytes.length}u\n#define RICE_BUNDLE_MD5 "${md5}"\n#define RICE_BUNDLE_VERSION "offline-${sha(bytes).slice(0, 12)}"\nextern const unsigned char rice_miniapp_source[RICE_BUNDLE_SIZE + 1];\n#endif\n`;
  const rows = [];
  for (let i = 0; i < bytes.length; i += 24) rows.push('    ' + [...bytes.subarray(i, i + 24)].join(',') + ',');
  const c = '#include "miniapp_bundle.h"\nconst unsigned char rice_miniapp_source[RICE_BUNDLE_SIZE + 1] __attribute__((section(".rodata.ricestep_app"))) = {\n' + rows.join('\n') + '\n    0\n};\n';
  return { source, header, c, bytes: bytes.length, sha256: sha(bytes), moduleHashes };
}

function git(sdk, args) { return cp.execFileSync('git', ['-C', sdk, ...args], { encoding: 'utf8', maxBuffer: 8 * 1024 * 1024 }).trimEnd(); }

function prepare({ sdk, luaSource, apply = false, poseLinkCheck = false, poseRuntime = false }) {
  if (poseLinkCheck && poseRuntime) throw new Error('Choose dormant link check or camera runtime, not both.');
  sdk = fs.realpathSync(sdk); luaSource = fs.realpathSync(luaSource);
  const revision = git(sdk, ['rev-parse', 'HEAD']);
  if (revision !== SDK_COMMIT) throw new Error(`Unsupported SDK revision ${revision}; expected ${SDK_COMMIT}.`);
  const luaHeader = fs.readFileSync(path.join(luaSource, 'lua.h'), 'utf8');
  if (!/^#define\s+LUA_VERSION_RELEASE\s+"8"\s*$/m.test(luaHeader) || !/^#define\s+LUA_VERSION_NUM\s+504\s*$/m.test(luaHeader)) {
    throw new Error('Expected official Lua 5.4.8 src directory. Verify its archive SHA-256 before extracting.');
  }
  const tracked = buildPatches(file => git(sdk, ['show', `${SDK_COMMIT}:${file}`]) + '\n', { poseRuntime });
  const builtin = bundleFiles(), voice = buildPack();
  const files = { ...tracked };
  for (const name of fs.readdirSync(path.join(ROOT, 'firmware/standalone'))) {
    if (!/\.(?:c|h|inc|ld|cmake)$/.test(name)) continue;
    files['apps/arcs-mini/standalone/' + name] = fs.readFileSync(path.join(ROOT, 'firmware/standalone', name));
  }
  files['apps/arcs-mini/standalone/miniapp_bundle.h'] = builtin.header;
  files['apps/arcs-mini/standalone/miniapp_bundle.c'] = builtin.c;
  files['apps/arcs-mini/standalone/voice_pack.h'] = voice.header;
  files['apps/arcs-mini/standalone/voice_pack.c'] = voice.source;
  files['apps/arcs-mini/standalone/voice_pack.inventory.json'] = JSON.stringify(voice.inventory, null, 2) + '\n';
  // Keep ownership hashes even when the optional target is disabled. Switching
  // pose -> desk -> pose must not turn our generated files into unknown edits.
  files['apps/arcs-mini/standalone/pose_runtime/CMakeLists.txt'] = fs.readFileSync(path.join(ROOT, 'firmware/pose_runtime/CMakeLists.txt'));
  for (const component of ['pose_engine', 'pose_pointwise', 'pose_decode', 'pose_image', 'pose_resources', 'motion_logic', 'pose_session']) {
    for (const name of fs.readdirSync(path.join(ROOT, 'firmware/components', component))) {
      if (!/\.(?:c|cc|h|inc)$/.test(name)) continue;
      files[`apps/arcs-mini/standalone/pose_components/${component}/${name}`] = fs.readFileSync(path.join(ROOT, 'firmware/components', component, name));
    }
  }
  let poseConfig = 'set(RICESTEP_POSE_LINK_CHECK OFF)\nset(RICESTEP_POSE_RUNTIME OFF)\n';
  if (poseLinkCheck || poseRuntime) {
    cp.execFileSync(process.execPath, [path.join(ROOT, 'scripts/setup-pose-source.cjs'), '--check-only'], { stdio: 'inherit' });
    const sourceRoot = path.join(sdk, '..', 'tflm-source');
    const flatRoot = path.join(sdk, '..', 'tflm-flatbuffers');
    for (const dir of [sourceRoot, flatRoot]) {
      if (fs.realpathSync.native(dir) !== fs.realpathSync.native(path.join(ROOT, '.cache', path.basename(dir)))) {
        throw new Error('Pose source roots must share the verified SDK cache parent.');
      }
    }
    poseConfig = `set(RICESTEP_POSE_LINK_CHECK ${poseLinkCheck ? 'ON' : 'OFF'})\nset(RICESTEP_POSE_RUNTIME ${poseRuntime ? 'ON' : 'OFF'})\n` +
      `set(RICESTEP_TFLM_ROOT "${sourceRoot.replaceAll('\\', '/')}" )\n` +
      `set(RICESTEP_FLATBUFFERS_ROOT "${flatRoot.replaceAll('\\', '/')}" )\n` +
      'set(RICESTEP_COMPONENTS_ROOT "${CMAKE_CURRENT_LIST_DIR}/pose_components")\n';
  }
  files['apps/arcs-mini/standalone/pose-config.cmake'] = poseConfig;
  for (const name of fs.readdirSync(luaSource)) {
    if (/\.[ch]$/.test(name)) files['third_party/ricestep-lua/' + name] = fs.readFileSync(path.join(luaSource, name));
  }
  for (const name of ['lapi.c', 'lauxlib.c', 'lbaselib.c', 'lcode.c', 'lctype.c', 'ldebug.c', 'ldo.c', 'ldump.c',
    'lfunc.c', 'lgc.c', 'llex.c', 'lmathlib.c', 'lmem.c', 'lobject.c', 'lopcodes.c', 'lparser.c', 'lstate.c',
    'lstring.c', 'lstrlib.c', 'ltable.c', 'ltablib.c', 'ltm.c', 'lundump.c', 'lutf8lib.c', 'lvm.c', 'lzio.c', 'luaconf.h', 'lualib.h']) {
    if (!files['third_party/ricestep-lua/' + name]) throw new Error('Incomplete Lua source: ' + name);
  }
  const marker = path.join(sdk, 'apps/arcs-mini/standalone/overlay-manifest.json');
  const previous = fs.existsSync(marker) ? JSON.parse(fs.readFileSync(marker, 'utf8')) : null;
  // Preflight every destination before the first write. Preserve unrelated edits.
  for (const [name, data] of Object.entries(files)) {
    const target = path.join(sdk, name);
    if (!fs.existsSync(target)) continue;
    const current = fs.readFileSync(target), currentHash = sha(current);
    if (currentHash === sha(data) || previous?.files?.[name] === currentHash) continue;
    if (Object.hasOwn(tracked, name) && normalize(current.toString('utf8')).trimEnd() ===
        normalize(git(sdk, ['show', `${SDK_COMMIT}:${name}`])).trimEnd()) continue;
    throw new Error(`Refusing to overwrite unknown modifications: ${name}`);
  }
  const manifest = {
    schema: 1, sdk_revision: SDK_COMMIT, sdk_origin: 'public third-party mirror; upstream identity unverified',
    lua: LUA, lua_source_sha256: Object.fromEntries(Object.entries(files).filter(([n]) => n.startsWith('third_party/')).map(([n,b]) => [n, sha(b)])),
    app: { bytes: builtin.bytes, sha256: builtin.sha256, modules: builtin.moduleHashes },
    voice: voice.inventory, poseLinkCheck, poseRuntime,
    files: Object.fromEntries(Object.entries(files).map(([name,data]) => [name, sha(data)])),
  };
  if (apply) {
    for (const [name, data] of Object.entries(files)) {
      const target = path.join(sdk, name);
      fs.mkdirSync(path.dirname(target), { recursive: true });
      // Preserve timestamps for identical generated sources so incremental
      // native builds do not recompile the entire Lua/TFLM dependency tree.
      if (!fs.existsSync(target) || !fs.readFileSync(target).equals(Buffer.from(data))) fs.writeFileSync(target, data);
    }
    const manifestText = JSON.stringify(manifest, null, 2) + '\n';
    if (!fs.existsSync(marker) || fs.readFileSync(marker, 'utf8') !== manifestText) fs.writeFileSync(marker, manifestText);
  }
  return { applied: apply, sdk, patched_paths: Object.keys(tracked), copied_paths: Object.keys(files).length - Object.keys(tracked).length,
    lua: LUA.version, source_bytes: builtin.bytes, voice_bytes: voice.inventory.total_bytes,
    next: '.\\build.ps1 -S .\\apps\\arcs-mini -B build-mini3-standalone -DBOARD=arcs_mini3',
    manifest: apply ? marker : null };
}

if (require.main === module) {
  const args = process.argv.slice(2), options = {};
  for (let i = 0; i < args.length; ++i) {
    if (args[i] === '--apply') options.apply = true;
    else if (args[i] === '--pose-link-check') options.poseLinkCheck = true;
    else if (args[i] === '--pose-runtime') options.poseRuntime = true;
    else if (args[i] === '--sdk' && args[i + 1]) options.sdk = args[++i];
    else if (args[i] === '--lua-source' && args[i + 1]) options.luaSource = args[++i];
    else throw new Error('Usage: node scripts/prepare-standalone.cjs --sdk PATH --lua-source PATH [--apply]');
  }
  if (!options.sdk || !options.luaSource) throw new Error('--sdk and --lua-source are required. No network/download/flash action is performed.');
  console.log(JSON.stringify(prepare(options), null, 2));
}
module.exports = { SDK_COMMIT, LUA, buildPatches, bundleFiles, prepare };
