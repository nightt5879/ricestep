-- RiceStep UI; built after game.lua and character.lua, without require.
local config = type(RICESTEP_CONFIG) == "table" and RICESTEP_CONFIG or {}
local game, page, home_selection, selection = nil, "home", 1, 1
local uptime, draw_elapsed, click_at, tick_count = 0, 0, nil, 0
local toast, toast_until, animation_pose, animation_until = "", 0, 1, 0
local dirty, last_save, save_error, speech_id = false, 0, nil, nil
local step_sample, step_base, step_offset, step_day = nil, nil, 0, nil
local step_status, last_result = "waiting", nil
local BG, INK, MUTED, BLUE = 0xFFF5E5, 0x233D61, 0x6F8395, 0x399AB2
local CARD, LIGHT, GOLD, WHITE = 0xFFFBF3, 0xDCEFF0, 0xD7A749, 0xFFFFFF
local item_names = {"荷叶帽", "小贝壳", "鹅卵石", "晴天花", "云朵邮票", "米粒勋章"}
local item_notes = {"借一顶小帽子，继续走吧", "把海边的声音装进口袋", "路边一颗圆圆的小幸运", "今天又收集到一点晴天", "寄给下次出门的自己", "每一小步，都值得开饭"}

local function width_of(text)
    local width = 0
    for _, code in utf8.codes(text) do width = width + (code > 127 and 16 or 8) end
    return width
end
local function centered(text, y, color)
    screen.text(text, math.max(4, math.floor((240 - width_of(text)) / 2)), y, color or INK)
end
local function message(text, milliseconds)
    toast, toast_until = text, uptime + (milliseconds or 3000)
end
local function sound(hz, milliseconds)
    if config.sound_enabled ~= false and type(buzzer) == "table" and type(buzzer.play) == "function" then
        buzzer.play(hz, milliseconds)
    end
end
local function say(text)
    if config.tts_enabled == true and tick_count > 1 and not speech_id and
        type(tts) == "table" and type(tts.speak) == "function" then speech_id = tts.speak(text) end
end
local function animate(pose)
    animation_pose, animation_until = pose, uptime + 3000
end
local function headline(default)
    if uptime < toast_until then return toast end
    if save_error then return "进度暂未保存，稍后重试" end
    return default
end
local function fresh_steps()
    return step_status == "ready" and step_sample ~= nil and uptime - step_sample.at <= 90000
end
local function header(title, state)
    screen.begin(BG)
    screen.text(title, 12, 10, INK)
    screen.text("米饭 " .. state.rice .. "碗", 148, 10, BLUE)
    screen.rect(12, 31, 216, 1, LIGHT)
end
local function actions(labels, selected)
    local cell = math.floor(220 / #labels)
    for index, label in ipairs(labels) do
        local x, w = 10 + (index - 1) * cell, cell - 4
        screen.rect(x, 188, w, 26, index == selected and BLUE or LIGHT)
        screen.text(label, x + math.max(2, math.floor((w - width_of(label)) / 2)), 193,
            index == selected and WHITE or INK)
    end
    centered("单击切换  双击确认", 220, MUTED)
end
local function character(pose, small)
    local use_pose = uptime < animation_until and animation_pose or pose
    if small then draw_character(use_pose, 88, 58, 2)
    else
        local bob = use_pose == 1 and math.floor(math.sin(uptime / 450) * 1.5) or 0
        screen.rect(68, 178, 104, 3, LIGHT)
        draw_character(use_pose, 72, 58 + bob, 3)
    end
end
local function time_label(milliseconds)
    local seconds = math.max(0, math.ceil(milliseconds / 1000))
    return string.format("%02d:%02d", math.floor(seconds / 60), seconds % 60)
end
local function focus_labels(state)
    if state.focus_status == "active" then return {"暂停", "结束", "回家"} end
    if state.focus_status == "paused" then return {"继续", "结束", "回家"} end
    return {state.focus_status == "claimed" and "再来" or "开始", "回家"}
end
local function walk_labels(state)
    if state.status == "active" then return {"暂停", "结束"} end
    if state.status == "paused" then return {"继续", "结束"} end
    return {"出发", "回家"}
end
local function draw_item(index, owned)
    local color = owned and GOLD or 0xBAC8CB
    screen.rect(79, 63, 82, 66, CARD)
    if not owned then
        screen.rect(96, 79, 48, 36, color)
        screen.rect(94, 76, 52, 7, MUTED)
        screen.rect(118, 77, 5, 38, LIGHT)
    elseif index == 1 then
        screen.rect(98, 79, 45, 24, 0x76A978)
        screen.rect(88, 102, 64, 8, 0x527D58)
        screen.rect(118, 70, 4, 15, 0x527D58)
    elseif index == 2 then
        screen.rect(103, 78, 34, 31, 0xE4B3A1)
        screen.rect(96, 86, 48, 17, 0xE4B3A1)
        screen.rect(115, 79, 4, 27, 0xFFF0DB)
        screen.rect(124, 81, 4, 24, 0xFFF0DB)
    elseif index == 3 then
        screen.rect(97, 83, 47, 26, 0x9AAEB5)
        screen.rect(103, 78, 32, 36, 0x9AAEB5)
        screen.rect(105, 84, 18, 5, LIGHT)
    elseif index == 4 then
        screen.rect(118, 91, 4, 27, 0x78A476)
        screen.rect(107, 77, 26, 24, 0xEAB46B)
        screen.rect(101, 84, 38, 10, 0xEAB46B)
        screen.rect(115, 84, 10, 10, 0xFFF0BA)
    elseif index == 5 then
        screen.rect(100, 73, 40, 48, BLUE)
        screen.rect(104, 77, 32, 40, WHITE)
        screen.rect(109, 89, 22, 12, LIGHT)
        screen.rect(114, 84, 12, 20, LIGHT)
    else
        screen.rect(106, 72, 11, 35, BLUE)
        screen.rect(123, 72, 11, 35, BLUE)
        screen.rect(103, 96, 34, 24, GOLD)
        screen.rect(109, 91, 22, 34, GOLD)
        screen.rect(117, 101, 6, 13, WHITE)
    end
end
local function draw()
    if not game then return end
    local state = game.snapshot()
    if page == "home" then
        header("肥鱼开饭", state)
        screen.rect(12, 36, 216, 22, CARD)
        local hints = {"今天也要好好吃饭呀", "一起专心一小会儿", "走到哪儿，吃到哪儿", "把小惊喜装进口袋"}
        centered(headline(hints[home_selection]), 39, INK)
        character(1, false)
        actions({"喂饭", "专注", "散步", "背包"}, home_selection)
    elseif page == "focus" then
        header("一起专注", state)
        local hint = "完成25分钟，米饭加一"
        if state.focus_status == "paused" then hint = "歇一小会儿，再继续"
        elseif state.focus_status == "claimed" then hint = "一碗专心饭，送给你" end
        centered(headline(hint), 40, INK)
        character(1, true)
        local inactive = state.focus_status == "idle" or state.focus_status == "cancelled"
        centered(time_label(inactive and state.focus_duration_ms or state.focus_remaining_ms), 146, INK)
        screen.rect(20, 172, 200, 5, LIGHT)
        local progress = inactive and 0 or math.floor(200 * state.focus_ms / state.focus_duration_ms)
        if progress > 0 then screen.rect(20, 172, math.min(200, progress), 5, BLUE) end
        local labels = focus_labels(state)
        selection = math.min(selection, #labels)
        actions(labels, selection)
    elseif page == "focus_confirm" then
        header("提前结束吗", state)
        centered("本次专注不会获得米饭", 40, INK)
        character(1, true)
        centered("已专注 " .. time_label(state.focus_ms), 146, INK)
        centered("也可以回来继续", 166, MUTED)
        actions({"返回专注", "确认结束"}, selection)
    elseif page == "walk" then
        header("散步找惊喜", state)
        local active = state.status == "active" or state.status == "paused"
        local hint = fresh_steps() and "手机步数已连接" or "等待手机步数"
        if state.status == "active" and not fresh_steps() then hint = "回家同步后结算"
        elseif step_status == "offline" then hint = "步数同步已断开"
        elseif step_status == "invalid" then hint = "步数数据暂不可用"
        elseif state.status == "paused" then hint = "已暂停，随时继续" end
        centered(headline(hint), 40, INK)
        character(1, true)
        local count = active and state.step_count or (step_sample and step_sample.total or nil)
        local count_label = active and (fresh_steps() and "本次 " or "已同步 ") or "今日同步 "
        centered(count and (count_label .. count .. " 步") or "尚未接入步数", 142, INK)
        local remaining = state.steps_per_rice - ((state.step_carry + (active and state.step_count or 0)) % state.steps_per_rice)
        centered(state.status == "active" and not fresh_steps() and "回家联网，领取米饭" or
            ("再走" .. remaining .. "步，攒一碗饭"), 163, MUTED)
        actions(walk_labels(state), selection)
    elseif page == "bag" then
        header("旅行背包", state)
        centered("已收集 " .. state.collection_count .. " / 6", 39, MUTED)
        local owned = game.has_item(selection)
        draw_item(selection, owned)
        centered(selection .. "/6  " .. (owned and "已收集" or "未发现"), 115, MUTED)
        centered(owned and item_names[selection] or "未发现的纪念品", 141, INK)
        centered(owned and item_notes[selection] or "散步攒够步数，就有惊喜", 163, MUTED)
        actions({"回家"}, 1)
    else
        header("散步完成", state)
        centered(headline("今天也多走了一点点"), 40, INK)
        character(state.last_item > 0 and 3 or 1, true)
        local result = last_result or {step_count = state.step_count, rice_added = 0, item = state.last_item, is_new = state.last_new}
        centered(result.step_count .. " 步  米饭 +" .. result.rice_added, 146, INK)
        centered(result.item > 0 and ((result.is_new and "新发现：" or "又遇见：") .. item_names[result.item]) or "小步数也会留到下次", 166, MUTED)
        actions({"回家"}, 1)
    end
    screen.present()
end
local function set_page(next_page)
    page, selection, click_at = next_page, 1, nil
end
local function error_message(reason)
    local messages = {
        no_rice = "先专注或散步，攒一碗饭", full = "肚子饱饱的，晚点再吃",
        focus_in_progress = "先完成或结束这次专注", walk_in_progress = "先结束这次散步吧",
        reward_pending = "先收下上次的小收获", not_active = "当前没有进行中的任务",
    }
    message(messages[reason] or "稍等一下，再试试", 3000)
end
local function baseline_steps()
    if not fresh_steps() then message("先同步手机步数再出发"); return false end
    step_base, step_offset, step_day = step_sample.total, game.snapshot().step_count, step_sample.day
    return true
end
local function choose()
    local state = game.snapshot()
    if page == "home" then
        if home_selection == 1 then
            local ok, result = game.feed()
            if ok then
                dirty = true
                message("开饭啦！白米饭真好吃")
                animate(2)
                sound(523, 70)
                say("开饭啦，白米饭真好吃！")
            else error_message(result) end
        elseif home_selection == 2 then set_page("focus")
        elseif home_selection == 3 then set_page("walk")
        else set_page("bag") end
    elseif page == "focus" then
        local labels = focus_labels(state)
        local choice = labels[selection]
        if choice == "回家" then set_page("home")
        elseif choice == "结束" then set_page("focus_confirm")
        else
            local ok, reason
            if choice == "暂停" then ok, reason = game.pause_focus()
            elseif choice == "继续" then ok, reason = game.resume_focus()
            else ok, reason = game.start_focus() end
            if ok then dirty = true; selection = 1; sound(440, 50) else error_message(reason) end
        end
    elseif page == "focus_confirm" then
        if selection == 2 then game.cancel_focus(); dirty = true; message("休息一下，也很好") end
        set_page("focus")
    elseif page == "walk" then
        local choice = walk_labels(state)[selection]
        if choice == "回家" then set_page("home")
        elseif choice == "暂停" then game.pause_walk(); dirty = true
        elseif choice == "继续" then
            if baseline_steps() then game.resume_walk(); dirty = true end
        elseif choice == "结束" then
            if not fresh_steps() then message("先同步步数，再收下米饭")
            else
                local ok, reason = game.finish_walk()
                if ok then
                    local claimed, result = game.claim_walk()
                    if claimed then
                        dirty, last_result = true, result
                        message(result.rice_added > 0 and "今天的散步，开饭啦" or "今天也多走了一点点")
                        if result.item > 0 then animate(3); sound(659, 90) end
                        set_page("summary")
                    else error_message(result) end
                else error_message(reason) end
            end
        else
            if fresh_steps() then
                local ok, reason = game.start_walk("sensor")
                if ok then baseline_steps(); dirty = true; sound(440, 50) else error_message(reason) end
            else message("先同步手机步数再出发") end
        end
    elseif page == "bag" then
        set_page("home")
    else set_page("home") end
    draw()
end
local function next_choice()
    if page == "home" then home_selection = home_selection % 4 + 1
    elseif page == "focus" then selection = selection % #focus_labels(game.snapshot()) + 1
    elseif page == "walk" or page == "focus_confirm" then selection = selection % 2 + 1
    elseif page == "bag" then selection = selection % 6 + 1 end
    draw()
end
function on_start()
    local saved = nil
    if config.storage_enabled ~= false and type(storage) == "table" and type(storage.load) == "function" then saved = storage.load() end
    game = create_game({saved = saved})
    local state = game.snapshot()
    if state.status == "finished" then
        local ok, result = game.claim_walk()
        if ok then last_result, page = result, "summary" end
    elseif state.focus_status == "finished" then game.claim_focus(); page = "focus" end
    dirty = true
    draw()
end
function on_tick(dt_ms)
    if not game then return end
    tick_count = tick_count + 1
    local dt = type(dt_ms) == "number" and dt_ms == dt_ms and math.floor(math.max(0, math.min(5000, dt_ms))) or 0
    uptime, draw_elapsed = uptime + dt, draw_elapsed + dt
    local before = game.snapshot()
    local event = game.update(dt)
    if before.status == "active" or before.focus_status == "active" then dirty = true end
    if event and event.kind == "focus_finished" then
        local ok, result = game.claim_focus()
        if ok then
            dirty = true
            message(result.rice_added > 0 and "专注完成！米饭加一" or "专注完成！饭盒已经满啦", 5000)
            animate(3)
            sound(659, 100)
            set_page("focus")
        end
    end
    -- A carried device may leave Wi-Fi. Preserve its measured-step baseline;
    -- only a deliberate pause excludes movement, and fresh sync gates settlement.
    if click_at and uptime - click_at >= 320 then click_at = nil; next_choice() end
    if dirty and uptime - last_save >= 10000 then
        last_save = uptime
        if config.storage_enabled ~= false and type(storage) == "table" and type(storage.save) == "function" then
            local ok, reason = storage.save(game.export(), 2592000)
            if ok then dirty, save_error = false, nil else save_error = reason or "unavailable" end
        elseif config.storage_enabled ~= false then save_error = "unavailable" end
    end
    if draw_elapsed >= 250 then draw_elapsed = 0; draw() end
end
function on_button_click(button_id)
    if button_id ~= "function" or not game then return end
    if click_at and uptime - click_at < 320 then click_at = nil; choose() else click_at = uptime end
end
function on_tts_result(id, result)
    if id == speech_id then speech_id = nil end
end
function on_step_sample(total, day)
    if type(total) ~= "number" or total ~= total or total < 0 or total > 10000000 or total % 1 ~= 0 or
        type(day) ~= "string" or not day:match("^%d%d%d%d%-%d%d%-%d%d$") then return false end
    local previous = step_sample
    if previous and previous.day == day and total < previous.total then return false end
    if previous and day < previous.day then return false end
    local was_fresh = fresh_steps()
    step_sample, step_status = {total = total, day = day, at = uptime}, "ready"
    if not game then return true end
    local state = game.snapshot()
    if state.status == "active" and state.source == "sensor" then
        if step_base == nil then step_base, step_offset, step_day = total, state.step_count, day end
        if day ~= step_day then step_base, step_offset, step_day = 0, state.step_count, day end
        local next_total = math.min(200000, step_offset + math.max(0, total - step_base))
        if game.set_steps(next_total) then dirty = true end
        if not was_fresh then message("步数已同步，可以开饭啦") end
    end
    return true
end
function on_step_status(status)
    if status == "ready" or status == "waiting" or status == "offline" or status == "invalid" then step_status = status end
end
-- Read-only test diagnostics. There is no synthetic-step input in the product UI.
function ricestep_debug()
    return {page = page, selection = selection, home_selection = home_selection,
        state = game and game.snapshot() or nil, steps_connected = fresh_steps(), save_error = save_error}
end
