-- Append to generated bundle. Synthetic pose for visual QA only; never deployed.
RICESTEP_CONFIG.tts_enabled, RICESTEP_CONFIG.sound_enabled = false, false
RICESTEP_CONFIG.clock_enabled, RICESTEP_CONFIG.storage_enabled = false, false
local actual_start, actual_tick = on_start, on_tick
local value = {state = "idle"}
motion = {
    start = function(kind, id) value = {state = "starting", kind = kind, session_id = id, reps = 0}; return true end,
    stop = function() value = {state = "idle"} end,
    status = function() return value end,
}
local real_text = screen.text
screen.text = function(text, x, y, color)
    if text == "单击切换  双击确认" then text = "测试数据，不是实测" end
    return real_text(text, x, y, color)
end
local function double() on_button_click("function"); on_button_click("function") end
function on_start()
    actual_start()
    for i = 1, 4 do on_button_click("function"); actual_tick(350) end
    double(); on_button_click("function"); actual_tick(350); double(); double()
    -- COCO17 sample, already in original normalized camera coordinates.
    local xy = {{.50,.12},{.47,.10},{.53,.10},{.44,.12},{.56,.12},
        {.39,.29},{.61,.29},{.32,.46},{.68,.46},{.28,.62},{.72,.62},
        {.43,.55},{.57,.55},{.38,.76},{.62,.76},{.35,.94},{.65,.94}}
    value.state, value.reason, value.reps, value.image_aspect, value.points = "running", "ok", 5, 4/3, {}
    for i, pair in ipairs(xy) do value.points[i] = {x = pair[1], y = pair[2], confidence = 1} end
    actual_tick(250)
end
function on_tick() end
