/* Included after miniapp_register_function in miniapp_runtime.c. The private
 * VM flags keep validation/replacement callbacks free of playback side effects. */
static int rice_lua_clock_monotonic(lua_State *L)
{
    /* The miniapp Lua task is the only caller. Accumulate unsigned tick deltas
     * so a running app remains monotonic across the FreeRTOS counter wrap.
     * This is elapsed time, never a replacement for clock.now()'s Unix epoch. */
    static TickType_t previous;
    static uint64_t elapsed_ticks;
    static bool initialized;
    TickType_t now = xTaskGetTickCount();
    if (!initialized) {
        elapsed_ticks = (uint64_t)now;
        initialized = true;
    } else {
        elapsed_ticks += (TickType_t)(now - previous);
    }
    previous = now;
    lua_pushnumber(L, 1.0 + (lua_Number)elapsed_ticks / (lua_Number)configTICK_RATE_HZ);
    return 1;
}

static int rice_lua_audio_play(lua_State *L)
{
    miniapp_vm_t *vm = miniapp_vm_get(L);
    size_t length = 0;
    const char *url = luaL_checklstring(L, 1, &length);
    bool accepted = rice_storage_owns(vm->id) && !vm->validating &&
        !vm->faulted && !vm->retiring && rice_audio_play_url(url, length);
    lua_pushboolean(L, accepted);
    return 1;
}

static int rice_lua_audio_stop(lua_State *L)
{
    miniapp_vm_t *vm = miniapp_vm_get(L);
    if (rice_storage_owns(vm->id) && !vm->validating && !vm->retiring) rice_audio_stop();
    return 0;
}

static int rice_lua_audio_state(lua_State *L)
{
    miniapp_vm_t *vm = miniapp_vm_get(L);
    lua_pushstring(L, vm->validating || !rice_storage_owns(vm->id) ? "idle" : rice_audio_state());
    return 1;
}

#ifdef CONFIG_RICESTEP_POSE
static bool rice_lua_motion_allowed(lua_State *L)
{
    miniapp_vm_t *vm = miniapp_vm_get(L);
    return rice_storage_owns(vm->id) && !vm->validating && !vm->retiring && !vm->faulted;
}

static int rice_lua_motion_start(lua_State *L)
{
    const char *kind = luaL_checkstring(L, 1);
    lua_Integer id = luaL_checkinteger(L, 2);
    ml_exercise_kind exercise;
    if (strcmp(kind, "pushup") == 0) exercise = ML_PUSHUP;
    else if (strcmp(kind, "squat") == 0) exercise = ML_SQUAT;
    else return luaL_error(L, "motion kind must be pushup or squat");
    if (id <= 0 || (uint64_t)id > UINT32_MAX) return luaL_error(L, "invalid motion session");
    bool allowed = rice_lua_motion_allowed(L);
    bool accepted = allowed && rice_pose_start(exercise, (uint32_t)id);
    lua_pushboolean(L, accepted);
    if (!accepted) {
        const char *reason = allowed ? "busy" : "unavailable";
        if (allowed) {
            rice_pose_snapshot_t status;
            rice_pose_snapshot(&status);
            if (status.state == RICE_POSE_ERROR && status.reason) reason = status.reason;
        }
        lua_pushstring(L, reason); return 2;
    }
    return 1;
}

static int rice_lua_motion_stop(lua_State *L)
{
    if (rice_lua_motion_allowed(L)) rice_pose_stop();
    return 0;
}

static void rice_lua_pose_number(lua_State *L, const char *name, lua_Number value)
{
    lua_pushnumber(L, value); lua_setfield(L, -2, name);
}

static int rice_lua_motion_status(lua_State *L)
{
    lua_createtable(L, 0, 11);
    if (!rice_lua_motion_allowed(L)) {
        lua_pushliteral(L, "unavailable"); lua_setfield(L, -2, "state");
        return 1;
    }
    rice_pose_snapshot_t status;
    rice_pose_snapshot(&status);
    lua_pushstring(L, rice_pose_state_string(status.state)); lua_setfield(L, -2, "state");
    lua_pushstring(L, status.kind == ML_SQUAT ? "squat" : "pushup"); lua_setfield(L, -2, "kind");
    lua_pushstring(L, status.reason ? status.reason : "none"); lua_setfield(L, -2, "reason");
    rice_lua_pose_number(L, "session_id", status.session_id);
    rice_lua_pose_number(L, "reps", status.total_reps);
    rice_lua_pose_number(L, "frame_ms", status.capture_ms);
    rice_lua_pose_number(L, "inference_ms", status.inference_ms);
    rice_lua_pose_number(L, "frame_age_ms", status.frame_age_ms);
    if (status.have_frame) {
        rice_lua_pose_number(L, "image_aspect", status.image_aspect);
        lua_createtable(L, ML_KEYPOINT_COUNT, 0);
        for (unsigned i = 0; i < ML_KEYPOINT_COUNT; ++i) {
            lua_createtable(L, 0, 3);
            rice_lua_pose_number(L, "x", status.points[i].x);
            rice_lua_pose_number(L, "y", status.points[i].y);
            rice_lua_pose_number(L, "confidence", status.points[i].confidence);
            lua_rawseti(L, -2, i + 1);
        }
        lua_setfield(L, -2, "points");
    }
    return 1;
}
#endif

static void rice_lua_audio_open(lua_State *L)
{
    /* The stock clock table already exists; preserve now/localtime semantics. */
    miniapp_register_function(L, "clock", "monotonic", rice_lua_clock_monotonic);
    lua_newtable(L);
    lua_setglobal(L, "audio");
    miniapp_register_function(L, "audio", "play", rice_lua_audio_play);
    miniapp_register_function(L, "audio", "stop", rice_lua_audio_stop);
    miniapp_register_function(L, "audio", "get_state", rice_lua_audio_state);
#ifdef CONFIG_RICESTEP_POSE
    lua_newtable(L);
    lua_setglobal(L, "motion");
    miniapp_register_function(L, "motion", "start", rice_lua_motion_start);
    miniapp_register_function(L, "motion", "stop", rice_lua_motion_stop);
    miniapp_register_function(L, "motion", "status", rice_lua_motion_status);
#endif
}
