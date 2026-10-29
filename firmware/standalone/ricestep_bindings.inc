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

static void rice_lua_audio_open(lua_State *L)
{
    /* The stock clock table already exists; preserve now/localtime semantics. */
    miniapp_register_function(L, "clock", "monotonic", rice_lua_clock_monotonic);
    lua_newtable(L);
    lua_setglobal(L, "audio");
    miniapp_register_function(L, "audio", "play", rice_lua_audio_play);
    miniapp_register_function(L, "audio", "stop", rice_lua_audio_stop);
    miniapp_register_function(L, "audio", "get_state", rice_lua_audio_state);
}
