-- Real UI lifecycle callbacks with isolated, in-memory storage.
local actual_start, actual_tick = on_start, on_tick
local persisted, writes, phase = nil, 0, 0
local function equal(actual, expected, label)
    assert(actual == expected, label .. ": expected " .. tostring(expected) .. ", got " .. tostring(actual))
end
local function clone(value)
    local copy = {}
    for key, item in pairs(value) do copy[key] = item end
    return copy
end
storage = {
    load = function() return persisted and clone(persisted) or nil end,
    save = function(value, ttl)
        local fields = 0
        for _, item in pairs(value) do
            fields = fields + 1
            assert(type(item) ~= "table", "storage stays flat")
        end
        assert(fields <= 32, "storage field budget")
        assert(#json.encode(value) <= 1024, "storage byte budget")
        equal(ttl, 2592000, "documented storage TTL")
        persisted, writes = clone(value), writes + 1
        return true
    end,
}
local function boot(value)
    persisted = value and clone(value) or nil
    actual_start()
end
local function double_click()
    on_button_click("function")
    on_button_click("function")
end
local function single_click()
    on_button_click("function")
    actual_tick(350)
end
local function flush()
    actual_tick(5000)
    actual_tick(5000)
end
function on_start()
    -- Flush startup first, so a later save must be caused by idle digestion.
    boot({v = 1, satiety = 100, decay_ms = 285000})
    flush()
    equal(persisted.satiety, 100, "initial idle save")
    equal(writes, 1, "startup flush")
    phase = 1
end
function on_tick(dt_ms)
    if phase == 1 then
        flush()
        equal(persisted.satiety, 99, "idle digestion marks progress dirty")
        equal(writes, 2, "idle digestion flushed after write interval")
        boot(persisted)
        equal(ricestep_debug().state.satiety, 99, "digestion survives reloading")
        boot({v = 1, rice = 7, satiety = 50, collection = 3, cook_count = 4, perfect_count = 2})
        equal(ricestep_debug().state.collection_count, 2, "owned collections load")
        double_click()
        equal(ricestep_debug().state.rice, 6, "feeding loaded inventory")
        flush()
        boot(persisted)
        equal(ricestep_debug().state.rice, 6, "food balance survives reload")
        equal(ricestep_debug().state.collection_count, 2, "collections survive feeding reload")
        equal(ricestep_debug().state.cook_count, 4, "cooking history survives reload")
        equal(ricestep_debug().state.perfect_count, 2, "perfect cooking history survives reload")
        phase = 2
    elseif phase == 2 then
        local focus = create_game()
        assert(focus.start_focus())
        focus.update(1000)
        boot(focus.export())
        equal(ricestep_debug().state.focus_status, "paused", "focus restores paused")
        flush()
        equal(ricestep_debug().state.focus_ms, 1000, "restored focus cannot earn offline time")
        assert(focus.cancel_focus())
        boot(focus.export())
        equal(ricestep_debug().state.focus_status, "cancelled", "cancelled focus stays cancelled")
        equal(ricestep_debug().state.rice, 3, "cancelled focus never earns rice on reload")
        boot({v = 1, rice = 6, cook_count = 4, perfect_count = 2})
        flush()
        single_click()
        single_click()
        double_click()
        double_click()
        equal(ricestep_debug().state.cooking_status, "active", "begin transient cooking round")
        actual_tick(5000)
        actual_tick(4400)
        equal(ricestep_debug().state.cooking_status, "active", "round remains incomplete at autosave")
        equal(persisted.cooking_status, nil, "transient round is never persisted")
        boot(persisted)
        equal(ricestep_debug().state.cooking_status, "idle", "unfinished cooking is cancelled on reload")
        equal(ricestep_debug().state.rice, 6, "unfinished cooking cannot earn reboot credit")
        equal(ricestep_debug().state.cook_count, 4, "unfinished cooking cannot count as completed")
        phase = 3
    elseif phase == 3 then
        single_click()
        single_click()
        double_click()
        double_click()
        actual_tick(1000)
        on_button_click("function")
        equal(ricestep_debug().state.cooking_quality, "perfect", "complete a real callback round before save")
        equal(ricestep_debug().state.rice, 8, "perfect round credited once")
        flush()
        boot(persisted)
        equal(ricestep_debug().state.rice, 8, "completed cooking reward survives reload")
        equal(ricestep_debug().state.cook_count, 5, "completed round counted once after reload")
        equal(ricestep_debug().state.perfect_count, 3, "perfect count saved")
        equal(ricestep_debug().state.cooking_status, "idle", "old result cannot be claimed again after reload")
        equal(ricestep_debug().state.total_steps, 0, "companion saves never create steps")
        phase = 4
    elseif phase == 4 then
        local old_walk = create_game()
        assert(old_walk.start_walk("sensor"))
        assert(old_walk.set_steps(500))
        boot(old_walk.export())
        equal(ricestep_debug().state.rice, 4, "migration settles only already saved walking credit")
        equal(ricestep_debug().state.total_steps, 500, "migration never invents extra steps")
        equal(ricestep_debug().state.status, "claimed", "old walk cannot block companion activities")
        single_click()
        double_click()
        double_click()
        equal(ricestep_debug().state.focus_status, "active", "focus works after old walk migration")
        phase = 5
    elseif phase == 5 then
        boot({v = 1, rice = 6, desk = 1, desk_left = 123456})
        equal(ricestep_debug().state.desk_status, "paused", "active reminder restores paused on boot")
        flush()
        equal(persisted.desk_left, 123456, "boot does not consume unknown offline time")
        for index = 1, 4 do single_click() end
        double_click(); double_click(); double_click()
        equal(ricestep_debug().state.desk_status, "active", "restored reminder explicitly resumed")
        actual_tick(3456); flush()
        equal(persisted.desk_left, 110000, "running remainder autosaves without unrelated actions")
        boot(persisted); flush()
        equal(ricestep_debug().state.desk_status, "paused", "second reboot still needs resume")
        equal(ricestep_debug().state.desk_remaining_ms, 110000, "saved remaining time survives second boot")
        phase = 6
    elseif phase == 6 then
        boot({v = 1, rice = 6, desk = 2, desk_left = 99}); flush()
        equal(ricestep_debug().state.desk_status, "due", "due reminder remains due after boot")
        equal(persisted.desk_left, 0, "due save is canonical")
        for index = 1, 4 do single_click() end
        double_click(); double_click(); single_click(); double_click(); flush()
        equal(persisted.desk, 3, "snoozed mode is persisted")
        equal(persisted.desk_left, 290000, "snooze remaining time is persisted")
        boot(persisted); flush()
        equal(ricestep_debug().state.desk_status, "snooze_paused", "snooze also needs explicit resume after boot")
        equal(ricestep_debug().state.desk_remaining_ms, 290000, "snooze does not count downtime")
        phase = 7
    elseif phase == 7 then
        for index = 1, 4 do single_click() end
        double_click(); double_click(); double_click(); flush()
        equal(persisted.desk, 3, "resuming keeps the short snooze mode")
        equal(persisted.desk_left, 280000, "resuming snooze does not reset it to a full session")
        single_click(); single_click(); double_click(); flush()
        equal(persisted.desk, 0, "closing a reminder is saved")
        boot(persisted)
        equal(ricestep_debug().state.desk_status, "off", "closed reminder stays off after restart")
        equal(ricestep_debug().state.rice, 6, "all reminder lifecycle actions leave food alone")
        equal(ricestep_debug().state.total_steps, 0, "reminder reboot cannot invent steps")
        phase = 8
    else actual_tick(dt_ms) end
end
