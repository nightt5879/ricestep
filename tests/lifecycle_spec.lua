-- Actual UI callbacks with test-only in-memory persistence and measured samples.
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
local function open_walk()
    single_click()
    single_click()
    double_click()
    equal(ricestep_debug().page, "walk", "open restored walk")
end
local function walk_save(status)
    local game = create_game()
    assert(game.start_walk("sensor"))
    if status == "paused" then assert(game.pause_walk()) end
    local value = game.export()
    value.step_base, value.step_offset, value.step_day = 1000, 0, "2026-10-27"
    return value
end
function on_start()
    -- First flush startup's dirty state before the idle decay occurs.
    boot({v = 1, satiety = 100, decay_ms = 285000})
    actual_tick(5000)
    actual_tick(5000)
    equal(persisted.satiety, 100, "initial idle save")
    equal(writes, 1, "startup flush")
    phase = 1
end
function on_tick(dt_ms)
    if phase == 1 then
        actual_tick(5000)
        actual_tick(5000)
        equal(persisted.satiety, 99, "idle decay marks progress dirty")
        equal(writes, 2, "idle decay flushed after write interval")
        boot(walk_save("active"))
        equal(ricestep_debug().state.status, "paused", "restored active waits for actual sample")
        actual_tick(5000)
        actual_tick(5000)
        equal(persisted.status, "active", "waiting restore retains active intent in storage")
        equal(persisted.step_base, 1000, "waiting restore retains baseline")
        equal(on_step_sample(900, "2026-10-27"), false, "below-baseline sample refused")
        equal(on_step_sample(1500, "2026-02-30"), false, "invalid calendar date refused")
        assert(on_step_sample(1500, "2026-10-27"))
        equal(ricestep_debug().state.status, "active", "real sample resumes prior active walk")
        equal(ricestep_debug().state.step_count, 500, "reboot recovers measured cumulative difference")
        equal(ricestep_debug().state.elapsed_ms, 0, "waiting time never invented")
        assert(on_step_sample(1500, "2026-10-27"))
        equal(ricestep_debug().state.step_count, 500, "repeated sample idempotent after restore")
        actual_tick(5000)
        actual_tick(5000)
        phase = 2
    elseif phase == 2 then
        boot(persisted)
        equal(on_step_sample(1400, "2026-10-27"), false, "stale sample below saved measured total refused")
        equal(ricestep_debug().steps_connected, false, "stale restored sample cannot mark connection ready")
        assert(on_step_sample(1600, "2026-10-27"))
        equal(ricestep_debug().state.step_count, 600, "second reboot preserves original baseline")
        local saved = walk_save("paused")
        boot(saved)
        assert(on_step_sample(1500, "2026-10-27"))
        equal(ricestep_debug().state.status, "paused", "manual pause stays paused after actual sample")
        equal(ricestep_debug().state.step_count, 0, "paused restore excludes samples")
        open_walk()
        double_click()
        assert(on_step_sample(1700, "2026-10-27"))
        equal(ricestep_debug().state.step_count, 200, "legacy pause continues from newest measured baseline")
        phase = 3
    elseif phase == 3 then
        local legacy = walk_save("active")
        legacy.step_base, legacy.step_offset, legacy.step_day = nil, nil, nil
        boot(legacy)
        assert(on_step_sample(1500, "2026-10-27"))
        equal(ricestep_debug().state.status, "paused", "old save without baseline remains paused")
        local invalid = walk_save("active")
        invalid.step_offset = 1
        boot(invalid)
        assert(on_step_sample(1500, "2026-10-27"))
        equal(ricestep_debug().state.status, "paused", "invalid offset never auto-resumes")
        local focus = create_game()
        assert(focus.start_focus())
        focus.update(1000)
        boot(focus.export())
        assert(on_step_sample(1500, "2026-10-27"))
        equal(ricestep_debug().state.focus_status, "paused", "focus still restores paused")
        equal(ricestep_debug().state.focus_ms, 1000, "focus never earns reboot time")
        phase = 4
    else actual_tick(dt_ms) end
end
