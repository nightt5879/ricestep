-- Exercise real UI paths with a counting screen; this does not emulate hardware.
-- SDK text is an independent queue, so report shapes and text separately.
local actual_start, actual_tick = on_start, on_tick
local real_screen, real_character, real_factory = screen, draw_character, create_game
local profile = RENDER_TEST_API or 4
local limits = {shapes = profile >= 5 and 256 or 128, texts = 8}
local shapes, texts, frame_count, max_shapes, max_texts, max_calls = 0, 0, 0, 0, 0, 0
local max_page, phase, scenario = "", 0, "startup"
local seen_poses, seen_pages, persisted, fail_save = {}, {}, nil, false
local function check(condition, text)
    assert(condition, "render API " .. profile .. " / " .. scenario .. ": " .. text)
end
local function bounds(x, y, w, h)
    check(x >= 0 and y >= 0 and w > 0 and h > 0 and x + w <= 240 and y + h <= 240, "shape outside canvas")
end
app = {api_version = profile, width = 240, height = 240}
screen = {
    begin = function() shapes, texts = 0, 0 end,
    rect = function(x, y, w, h) bounds(x, y, w, h); shapes = shapes + 1 end,
    text = function(value, x, y)
        check(type(value) == "string" and #value <= 63, "text exceeds 63 UTF-8 bytes")
        check(x >= 0 and x < 240 and y >= 0 and y <= 224, "text origin outside canvas")
        local width = 0
        for _, code in utf8.codes(value) do width = width + (code > 127 and 16 or 8) end
        check(x + width <= 240, "text clipped: " .. value)
        texts = texts + 1
    end,
    present = function()
        local page = ricestep_debug().page
        seen_pages[page], frame_count = true, frame_count + 1
        check(shapes <= limits.shapes, page .. " exceeds shape limit: " .. shapes)
        check(texts <= limits.texts, page .. " exceeds text limit: " .. texts)
        if shapes > max_shapes then max_shapes, max_page = shapes, page end
        max_texts, max_calls = math.max(max_texts, texts), math.max(max_calls, shapes + texts)
    end,
}
function draw_character(pose, x, y, scale)
    seen_poses[pose] = true
    return real_character(pose, x, y, scale)
end
-- Voice scheduling has its own tests. No sound/network calls in geometry checks.
function create_voice()
    return {say = function() end, update = function() end, busy = function() return false end,
        on_tts_result = function() end, debug = function() return {} end}
end
function create_game(options)
    options.focus_duration_ms = 4000
    options.random = function() return 6 end
    return real_factory(options)
end
storage = {
    load = function() return persisted end,
    save = function(value)
        if fail_save then return false, "test unavailable" end
        persisted = value
        return true
    end,
}
local function boot(changes)
    local saved = {v = 1, rice = 20, satiety = 50, collection = 63, last_meal = "burnt"}
    for key, value in pairs(changes or {}) do saved[key] = value end
    persisted, fail_save = real_factory({saved = saved}).export(), false
    actual_start()
    actual_tick(250)
end
local function single() on_button_click("function"); actual_tick(350) end
local function double() on_button_click("function"); on_button_click("function") end
local function cook(elapsed, quality)
    boot()
    single(); single(); double(); double()
    actual_tick(elapsed)
    on_button_click("function")
    check(ricestep_debug().page == "cook_result", "expected cooking result")
    check(ricestep_debug().state.cooking_quality == quality, "wrong cooking fixture")
end
local scenarios = {
    {"home/full collection", function() boot(); single(); single(); single(); single() end},
    {"home/idle after 15 seconds", function()
        boot(); actual_tick(5000); actual_tick(5000); actual_tick(5000); actual_tick(250)
        check(seen_poses[4], "idle sleepy pose was not rendered")
    end},
    {"feed/anticipation", function() boot(); double() end},
    {"feed/eating", function() actual_tick(800) end},
    {"feed/satisfied", function() actual_tick(2500) end},
    {"feed/full belly", function() boot({satiety = 100}); double() end},
    {"feed/empty pantry", function() boot({rice = 0}); double() end},
    {"focus/start", function() boot(); single(); double(); double(); actual_tick(500) end},
    {"focus/paused", function() double(); actual_tick(500) end},
    {"focus/end confirmation", function() single(); double() end},
    {"focus/cancelled", function() single(); double() end},
    {"focus/discovery", function() boot(); single(); double(); double(); actual_tick(4000) end},
    {"home/discovery plus all shelves", function()
        single(); double()
        check(ricestep_debug().page == "home", "discovery must reach home while animated")
    end},
    {"cook/remember burnt meal", function() boot(); single(); single(); double() end},
    {"cook/running", function() double(); actual_tick(500) end},
    {"cook/raw", function() cook(400, "raw") end},
    {"cook/good", function() cook(700, "good") end},
    {"cook/perfect", function() cook(1000, "perfect") end},
    {"cook/result eating", function() actual_tick(800) end},
    {"cook/result satisfied", function() actual_tick(2500) end},
    {"cook/result animation settled", function() actual_tick(1500) end},
    {"cook/burnt", function() cook(1900, "burnt") end},
    {"cook/burnt animation settled", function() actual_tick(5000) end},
    {"bag/all owned items", function()
        boot(); single(); single(); single(); double()
        for index = 1, 5 do single() end
    end},
    {"bag/unowned items", function()
        boot({collection = 0}); single(); single(); single(); double()
        for index = 1, 5 do single() end
    end},
    {"home/save failure", function() boot(); fail_save = true; actual_tick(5000); actual_tick(5000) end},
}
function on_start()
    phase = 0
    real_screen.begin(0xFFF5E5)
    real_screen.text("Checking render budgets", 8, 16, 0x233D61)
    real_screen.present()
end
function on_tick()
    phase = phase + 1
    local action = scenarios[phase]
    if action then scenario = action[1]; action[2](); return end
    if phase ~= #scenarios + 1 then return end
    for pose = 1, 6 do check(seen_poses[pose], "pose " .. pose .. " was not covered") end
    for _, page in ipairs({"home", "focus", "focus_confirm", "cook", "cook_result", "bag"}) do
        check(seen_pages[page], "page " .. page .. " was not covered")
    end
    real_screen.begin(0xFFF5E5)
    real_screen.text("Render budget PASS", 8, 16, 0x233D61)
    real_screen.text("API " .. profile .. " / " .. #scenarios .. " scenarios", 8, 45, 0x233D61)
    real_screen.text("Frames " .. frame_count, 8, 74, 0x233D61)
    real_screen.text("Shapes " .. max_shapes .. " / " .. limits.shapes, 8, 103, 0x233D61)
    real_screen.text("Text " .. max_texts .. " / " .. limits.texts, 8, 132, 0x233D61)
    real_screen.text("All calls " .. max_calls, 8, 161, 0x233D61)
    real_screen.text("Peak page: " .. max_page, 8, 190, 0x233D61)
    real_screen.present()
end
