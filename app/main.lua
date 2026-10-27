-- RiceStep development-environment smoke test.
-- LingClaw Lua API >= 2; 240 x 240 screen; "function" button.
-- This counts button clicks only. It does not measure walking steps.
-- Saving this file or pressing Ctrl+R reloads the app and resets the count.

local button_presses = 0

local function draw()
    screen.begin(0x142534)
    screen.text("RiceStep", 16, 20, 0xFFFFFF)
    screen.text("SDK Ready", 16, 50, 0x55DDCC)
    screen.rect(16, 78, 208, 2, 0x55DDCC)
    screen.text("Button presses: " .. button_presses, 16, 96, 0xFFFFFF)
    screen.text("Space / function: +1", 16, 132, 0xFFFFFF)
    screen.text("Ctrl+R: reset", 16, 164, 0xFFFFFF)
    screen.text("Not step tracking", 16, 206, 0xB8C5CF)
    screen.present()
end

function on_start()
    button_presses = 0
    draw()
end

function on_tick(dt_ms)
    -- A lifecycle callback is required even for a static screen.
end

function on_button_click(button_id)
    if button_id == "function" then
        button_presses = button_presses + 1
        draw()
    end
end
