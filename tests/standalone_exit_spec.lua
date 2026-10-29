-- Append to the ACTUAL generated standalone bundle, including its exit trailer.
RICESTEP_CONFIG.tts_enabled, RICESTEP_CONFIG.sound_enabled = false, false
RICESTEP_CONFIG.clock_enabled = false
local actual_start, actual_tick, actual_exit = on_start, on_tick, on_exit
local native_active, stored, starts = false, nil, 0
function draw_character() end
motion = {
    start = function() native_active = true; starts = starts + 1; return true end,
    stop = function() native_active = false end,
    status = function() return {state = "idle"} end,
}
storage = {
    load = function() return nil end,
    save = function(value)
        assert(not native_active, "native camera must stop BEFORE exit persistence")
        assert(not ricestep_debug().state.exercise_active, "app exit must retire reward acceptance")
        stored = value
        return true
    end,
}
local function double() on_button_click("function"); on_button_click("function") end
function on_start()
    actual_start()
    for i = 1, 4 do on_button_click("function"); actual_tick(350) end
    double()
    on_button_click("function"); actual_tick(350); double(); double()
    assert(starts == 1 and native_active, "fixture must explicitly start motion")
    actual_exit()
    assert(not native_active and stored, "generated trailer must call app exit and save")
    assert(stored.v == 2 and stored.exercise_id == 1 and stored.exercise_reps == 0, "exit saves the current atomic ledger")
    screen.begin(0xFFF5E5)
    screen.text("Standalone exit PASS", 8, 40, 0x233D61)
    screen.present()
end
function on_tick() end
