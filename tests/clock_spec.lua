-- Exercise main.lua's calibrated-clock path, not just the game time API.
local actual_start, actual_tick = on_start, on_tick
local original_factory = create_game
local fake_now, phase, focus_duration = 1793100000, 0, 10000
clock = {now = function() return fake_now end}
function create_game(options)
    options.focus_duration_ms = focus_duration
    options.desk_interval_ms, options.desk_snooze_ms = 10000, 5000
    options.random = function() return 2 end
    return original_factory(options)
end
local function equal(actual, expected, label)
    assert(actual == expected, label .. ": expected " .. tostring(expected) .. ", got " .. tostring(actual))
end
local function double_click()
    on_button_click("function")
    on_button_click("function")
end
local function focused(milliseconds, label)
    equal(ricestep_debug().state.focus_ms, milliseconds, label)
end
function on_start()
    assert(RICESTEP_CONFIG.clock_enabled == true, "clock test must enable the UI clock path")
    actual_start()
    actual_tick(20)
    fake_now = fake_now + 3
    actual_tick(20)
    focused(0, "idle calendar time is not focus credit")
    on_button_click("function")
    actual_tick(350)
    double_click()
    equal(ricestep_debug().page, "focus", "open focus using menu callbacks")
    fake_now = fake_now + 2
    actual_tick(20)
    double_click()
    equal(ricestep_debug().state.focus_status, "active", "start focus after idle menu time")
    focused(0, "starting focus does not credit prior menu time")
    phase = 1
end
function on_tick(dt_ms)
    if phase == 1 then
        actual_tick(20)
        focused(0, "same calibrated second does not count logical ticks")
        fake_now = fake_now + 1
        actual_tick(20)
        focused(1000, "one actual second counts despite a 20ms logical tick")
        fake_now = fake_now + 3
        actual_tick(20)
        focused(4000, "small scheduling delay uses actual elapsed seconds")
        double_click()
        equal(ricestep_debug().state.focus_status, "paused", "pause focus")
        fake_now = fake_now + 2
        actual_tick(20)
        focused(4000, "paused calendar seconds are excluded")
        double_click()
        fake_now = fake_now + 1
        actual_tick(20)
        focused(5000, "resume counts only subsequent clock seconds")
        phase = 2
    elseif phase == 2 then
        fake_now = fake_now + 10
        actual_tick(20)
        focused(5000, "large forward clock jump is rejected")
        fake_now = fake_now - 3
        actual_tick(20)
        focused(5000, "backward clock adjustment is rejected")
        fake_now = fake_now + 1
        actual_tick(20)
        focused(6000, "normal timing resumes after a rejected adjustment")
        local calibrated = fake_now
        fake_now = nil
        actual_tick(400)
        focused(6400, "uncalibrated clock falls back to logical duration")
        actual_tick(600)
        focused(7000, "continued uncalibrated duration is counted once")
        fake_now = calibrated + 1
        actual_tick(20)
        focused(7000, "first calibrated value only establishes a new baseline")
        fake_now = fake_now + 3
        actual_tick(20)
        focused(10000, "calibrated elapsed time completes focus")
        equal(ricestep_debug().state.focus_status, "claimed", "completion automatically claims reward")
        equal(ricestep_debug().state.rice, 4, "completed focus grants one rice")
        equal(ricestep_debug().state.focus_count, 1, "completion counted once")
        equal(ricestep_debug().state.collection_count, 1, "completion grants one collectible")
        fake_now = fake_now + 5
        actual_tick(20)
        focused(10000, "finished timer stops accumulating")
        equal(ricestep_debug().state.rice, 4, "later clock callbacks cannot replay reward")
        equal(ricestep_debug().state.focus_count, 1, "later clock callbacks cannot replay completion")
        phase = 3
    elseif phase == 3 then
        local monotonic, wall_reads = 20, 0
        focus_duration = 100000
        clock = {
            monotonic = function() return monotonic end,
            now = function() wall_reads = wall_reads + 1; return fake_now end
        }
        actual_start()
        actual_tick(20)
        on_button_click("function")
        actual_tick(350)
        double_click()
        equal(ricestep_debug().page, "focus", "open offline focus through the UI")
        actual_tick(20)
        double_click()
        equal(ricestep_debug().state.focus_status, "active", "start offline focus through the UI")
        fake_now = fake_now - 10000
        monotonic = monotonic + 1.25
        actual_tick(20)
        focused(1250, "monotonic elapsed time includes rendering delays despite wall clock adjustment")
        fake_now = nil
        monotonic = monotonic + 0.75
        actual_tick(20)
        focused(2000, "offline focus progresses with no calibrated epoch")
        equal(wall_reads, 0, "valid monotonic time takes precedence over wall clock")

        -- Each invalid native value must fall back to a valid epoch. The first
        -- sample after switching time domains establishes a baseline only.
        fake_now = 1793100000
        monotonic = nil
        actual_tick(20)
        focused(2000, "switching clock domains cannot create a large reward")
        fake_now = fake_now + 1
        actual_tick(20)
        focused(3000, "missing monotonic value falls back to wall clock")
        local expected = 3000
        for _, invalid in ipairs({false, 0, -1, "invalid", math.huge, 0 / 0}) do
            monotonic = invalid
            fake_now = fake_now + 1
            actual_tick(20)
            expected = expected + 1000
            focused(expected, "invalid monotonic value falls back to wall clock")
        end
        assert(wall_reads > 0, "fallback reads the calibrated clock")
        phase = 4
    elseif phase == 4 then
        local monotonic = 500
        fake_now = 1793100000
        clock = {monotonic = function() return monotonic end, now = function() return fake_now end}
        actual_start()
        for index = 1, 4 do on_button_click("function"); actual_tick(350) end
        double_click(); double_click()
        equal(ricestep_debug().state.desk_status, "active", "manual reminder starts through clock-enabled UI")
        actual_tick(200)
        equal(ricestep_debug().state.desk_remaining_ms, 10000, "duplicate clock sample cannot consume reminder time")
        fake_now = fake_now - 10000
        monotonic = monotonic + 1.25
        actual_tick(20)
        equal(ricestep_debug().state.desk_remaining_ms, 8750, "reminder follows monotonic time not animation delta or wall clock")
        monotonic = monotonic + 10; actual_tick(20)
        equal(ricestep_debug().state.desk_remaining_ms, 8750, "large reminder clock gap is rejected")
        monotonic = monotonic - 2; actual_tick(20)
        equal(ricestep_debug().state.desk_remaining_ms, 8750, "backwards reminder time is rejected")
        monotonic = monotonic + 1; actual_tick(20)
        equal(ricestep_debug().state.desk_remaining_ms, 7750, "normal reminder clock resumes after rejection")
        on_button_click("function"); actual_tick(350); double_click()
        equal(ricestep_debug().state.desk_status, "paused", "pause monotonic reminder")
        monotonic = monotonic + 2; actual_tick(20)
        equal(ricestep_debug().state.desk_remaining_ms, 7750, "paused monotonic seconds are excluded")
        double_click()
        monotonic = monotonic + 5; actual_tick(20)
        monotonic = monotonic + 2.75; actual_tick(20)
        equal(ricestep_debug().state.desk_status, "due", "accepted real elapsed seconds make reminder due")
        monotonic = monotonic + 1; actual_tick(20)
        equal(ricestep_debug().state.desk_remaining_ms, 0, "due remainder cannot go negative")
        on_button_click("function"); actual_tick(350); double_click()
        equal(ricestep_debug().state.desk_status, "snoozed", "monotonic reminder can snooze")
        monotonic, fake_now = nil, nil
        actual_tick(500)
        equal(ricestep_debug().state.desk_remaining_ms, 4500, "missing clocks use logical tick duration once")
        monotonic = 1000; actual_tick(20)
        equal(ricestep_debug().state.desk_remaining_ms, 4500, "restored clock first establishes baseline")
        monotonic = monotonic + 4.5; actual_tick(20)
        equal(ricestep_debug().state.desk_status, "due", "snooze uses resumed clock without double counting")
        equal(ricestep_debug().state.rice, 3, "reminder timing gives no rice")
        equal(ricestep_debug().state.total_steps, 0, "reminder timing creates no exercise data")
        phase = 5
    else actual_tick(dt_ms) end
end
