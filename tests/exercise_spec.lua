-- Real app callbacks, synthetic native status ONLY in this test. No camera/audio.
local actual_start, actual_tick, actual_exit = on_start, on_tick, on_exit
local phase, starts, stops, writes = 0, 0, 0, 0
local persisted, fail_save, accept_start = nil, false, true
local native = {state = "idle"}
local function eq(actual, expected, label)
    assert(actual == expected, label .. ": expected " .. tostring(expected) .. ", got " .. tostring(actual))
end
local function clone(value)
    local result = {}
    for key, item in pairs(value) do result[key] = item end
    return result
end
local backend = {
    start = function(kind, id)
        starts = starts + 1
        if not accept_start then return false, "busy" end
        native = {state = "starting", session_id = id, kind = kind, reps = 0}
        return true
    end,
    stop = function() stops = stops + 1; native = {state = "idle"} end,
    status = function() return native end,
}
motion = backend
storage = {
    load = function() return persisted and clone(persisted) or nil end,
    save = function(value)
        local count = 0
        for _, item in pairs(value) do
            count = count + 1
            assert(type(item) ~= "table")
            if type(item) == "string" then assert(#item <= 256, "SDK scalar string bound") end
        end
        eq(count, 26, "compact v2 save field count")
        assert(#json.encode(value) <= 1024, "v2 JSON byte budget")
        if fail_save then return false, "test failure" end
        persisted, writes = clone(value), writes + 1
        return true
    end,
}
local function double() on_button_click("function"); on_button_click("function") end
local function single() on_button_click("function"); actual_tick(350) end
local function open_exercise()
    for i = 1, 4 do single() end
    double(); single(); double()
    eq(ricestep_debug().page, "exercise", "enter exercise via rest hub")
end
local function sample(reps, id, kind, state)
    return {state = state or "running", session_id = id or 1, kind = kind or "pushup", reps = reps}
end
local function emit(value) native = value; actual_tick(250) end
local function flush() actual_tick(5000); actual_tick(5000) end
function on_start()
    assert(RICESTEP_CONFIG.sound_enabled == false and RICESTEP_CONFIG.tts_enabled == false, "exercise tests must stay silent")
    assert(RICESTEP_CONFIG.storage_enabled == true, "exercise lifecycle test needs in-memory saves")
    actual_start()
end
function on_tick(dt)
    phase = phase + 1
    if phase == 1 then
        -- Preserve every pre-existing v1 entitlement through v2 packing.
        local legacy = create_game({saved = {v = 1, rice = 7, satiety = 89, bond = 88,
            session_id = 9, claimed_id = 8, status = "finished", source = "sensor",
            elapsed_ms = 12345, step_count = 750, step_carry = 250, timer_carry = 456,
            pending_item = 4, focus_status = "paused", focus_id = 3, focus_ms = 123,
            cook_count = 11, last_meal = "burnt", desk = 4, desk_left = 23456}})
        local before, packed = legacy.snapshot(), legacy.export()
        local restored = create_game({saved = packed})
        for key, value in pairs(before) do
            eq(restored.snapshot()[key], value, "v1 to v2 preserves " .. key)
        end
        eq(packed.v, 2, "save version migrated")
        eq(packed.status, nil, "legacy status moves into compact scalar")
        eq(packed.exercise_id, 0, "migration invents no exercise sessions")
        local ok, reward = restored.claim_walk()
        assert(ok); eq(reward.rice_added, 2, "legacy pending rice remains claimable")
        eq(reward.item, 4, "legacy pending discovery preserved")
        eq(restored.claim_walk(), false, "legacy claim remains idempotent")
        local full = create_game({saved = {v = 1, rice = 999}})
        assert(full.start_exercise("pushup", 1))
        assert(full.accept_exercise(sample(5)))
        eq(full.snapshot().rice, 999, "rice capacity respected")
        assert(full.feed())
        eq(full.accept_exercise(sample(5)), false, "capacity-clipped reward cannot be replayed after feeding")
        eq(full.snapshot().rice, 998, "no deferred capacity credit")
        assert(full.accept_exercise(sample(10)))
        eq(full.snapshot().rice, 999, "only new repetitions earn next rice")
        eq(full.snapshot().total_steps, 0, "native repetitions never become walking steps")
        full.stop_exercise()
        assert(full.start_exercise("squat", 2))
        eq(full.accept_exercise(sample(100, 1)), false, "old session ignored")
        assert(full.accept_exercise(sample(4, 2, "squat")))
        eq(full.snapshot().rice, 999, "new session does not inherit partial rice credit")
        eq(full.start_focus(), false, "camera session cannot run alongside focus credit")
    elseif phase == 2 then
        open_exercise()
        eq(starts, 0, "entering exercise never opens camera")
        accept_start = false; double()
        eq(starts, 1, "explicit start attempted")
        eq(ricestep_debug().state.exercise_id, 0, "busy rejection does not consume session ID")
        eq(ricestep_debug().state.exercise_active, false, "busy cannot activate rewards")
        emit({state = "idle"})
        accept_start = true; double()
        eq(ricestep_debug().state.exercise_id, 1, "accepted start commits candidate ID")
        eq(ricestep_debug().state.exercise_active, true, "only accepted explicit start activates session")
        emit(sample(999, 1, "pushup", "starting"))
        eq(ricestep_debug().state.rice, 3, "starting status is not confirmed repetitions")
    elseif phase == 3 then
        emit(sample(4)); emit(sample(4)); emit(sample(3))
        eq(ricestep_debug().state.exercise_reps, 4, "duplicate/backwards totals cannot reduce watermark")
        eq(ricestep_debug().state.rice, 3, "four confirmed reps earn no whole rice")
        for _, invalid in ipairs({-1, 4.5, math.huge, 0/0, "5", 1000001}) do emit(sample(invalid)) end
        eq(ricestep_debug().state.exercise_reps, 4, "malformed totals rejected")
        emit(sample(5)); emit(sample(5))
        eq(ricestep_debug().state.rice, 4, "first five reps rewarded once")
    elseif phase == 4 then
        emit(sample(100, 9)); emit(sample(100, 1, "squat"))
        eq(ricestep_debug().state.rice, 4, "wrong session/kind status cannot pay")
        emit(sample(10))
        eq(ricestep_debug().state.rice, 5, "cumulative skipped polling still pays only new threshold")
        flush()
        eq(persisted.exercise_id, 1, "session ID saved with inventory")
        eq(persisted.exercise_reps, 10, "reward watermark saved with inventory")
        eq(persisted.rice, 5, "same export contains corresponding rice")
        local previous_stops = stops
        actual_exit()
        assert(stops > previous_stops, "app exit calls native stop")
        eq(ricestep_debug().state.exercise_active, false, "exit retires reward acceptance")
        actual_start()
        eq(ricestep_debug().state.rice, 5, "earned food survives restart")
        eq(ricestep_debug().state.exercise_active, false, "camera sessions never auto-restore")
    elseif phase == 5 then
        open_exercise()
        local previous_starts = starts
        emit(sample(100))
        eq(starts, previous_starts, "status cannot trigger native start")
        eq(ricestep_debug().state.rice, 5, "stale pre-reboot counter cannot pay")
        emit({state = "idle"}); double()
        eq(ricestep_debug().state.exercise_id, 2, "new explicit session gets increasing ID")
        emit(sample(4, 2)); emit(sample(5, 2))
        eq(ricestep_debug().state.rice, 6, "new session earns exactly one new rice")
        emit(sample(100, 2, "pushup", "error"))
        eq(ricestep_debug().state.exercise_active, false, "native error retires session")
        eq(ricestep_debug().state.rice, 6, "error status cannot award its count")
    elseif phase == 6 then
        emit(sample(100, 2))
        eq(ricestep_debug().state.rice, 6, "late running sample cannot revive failed session")
        double()
        eq(ricestep_debug().page, "rest", "return leaves exercise page")
        motion = nil; single(); double()
        eq(ricestep_debug().motion.state, "unavailable", "native API absence is honestly unavailable")
        double()
        eq(ricestep_debug().page, "rest", "unavailable page offers return only")
        motion = backend; single(); double(); double()
        eq(ricestep_debug().state.exercise_id, 3, "explicit retry may start new session")
        single(); double()
        eq(ricestep_debug().page, "rest", "cancel pending startup by leaving page")
        eq(ricestep_debug().state.exercise_active, false, "leaving starting state retires acceptance")
        native = sample(100, 3); actual_tick(500)
        eq(ricestep_debug().state.rice, 6, "late startup completion after leave cannot reward")
    elseif phase == 7 then
        -- A rejected save updates neither durable inventory nor its ledger.
        flush()
        local old_rice, old_id, old_reps = persisted.rice, persisted.exercise_id, persisted.exercise_reps
        native = {state = "idle"}; single(); double(); double()
        emit(sample(5, 4))
        eq(ricestep_debug().state.rice, 7, "new accepted session can earn food")
        fail_save = true; flush()
        eq(persisted.rice, old_rice, "failed save leaves old inventory intact")
        eq(persisted.exercise_id, old_id, "failed save leaves old session ID intact")
        eq(persisted.exercise_reps, old_reps, "failed save leaves old watermark intact")
        fail_save = false; actual_start()
        eq(ricestep_debug().state.rice, old_rice, "unsaved rice rolls back together with watermark")
        eq(ricestep_debug().state.exercise_id, old_id, "reload uses the same durable ledger as rice")
        assert(writes > 0, "test exercised persistence")
        screen.begin(0xFFF5E5)
        screen.text("Exercise UI / ledger PASS", 8, 40, 0x233D61)
        screen.text("Mock status, no hardware", 8, 80, 0x233D61)
        screen.present()
    end
end
