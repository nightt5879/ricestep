-- Desktop companion. Concatenate after game.lua and character.lua.
local config = type(RICESTEP_CONFIG) == "table" and RICESTEP_CONFIG or {}
local game, page, home_selection, selection = nil, "home", 1, 1
local uptime, draw_elapsed, click_at, tick_count = 0, 0, nil, 0
local toast, toast_until, animation_pose, animation_until = "", 0, 1, 0
local dirty, last_save, save_error, speech_id = false, 0, nil, nil
local cook_armed_at, input_after, cook_result, last_clock = 0, 0, nil, nil
local BG, INK, MUTED, BLUE = 0xFFF5E5, 0x233D61, 0x6F8395, 0x399AB2
local CARD, LIGHT, GOLD, WHITE = 0xFFFBF3, 0xDCEFF0, 0xD7A749, 0xFFFFFF
local item_names = {"荷叶帽", "小贝壳", "鹅卵石", "晴天花", "云朵邮票", "米粒勋章"}
local item_notes = {"认真生活，也要记得休息", "把海边的声音装进口袋", "小小的努力，也会发光", "今天又收集到一点晴天", "寄给下一次努力的自己", "你负责努力，我负责吃饭"}
local dish_names = {perfect = "完美白米饭", good = "香香米饭", raw = "夹生饭", burnt = "锅巴饭"}
local dish_lines = {perfect = "这碗饭，我认可了！", good = "香香的，我先开动啦", raw = "还差一点点，再焖一会", burnt = "锅巴也很香，下次早点"}

local function width_of(text)
    local width = 0
    for _, code in utf8.codes(text) do width = width + (code > 127 and 16 or 8) end
    return width
end
local function centered(text, y, color)
    screen.text(text, math.max(4, math.floor((240 - width_of(text)) / 2)), y, color or INK)
end
local function message(text, duration) toast, toast_until = text, uptime + (duration or 3000) end
local function sound(hz, duration)
    if config.sound_enabled ~= false and type(buzzer) == "table" and type(buzzer.play) == "function" then buzzer.play(hz, duration) end
end
local function say(text)
    if config.tts_enabled == true and tick_count > 1 and not speech_id and type(tts) == "table" and type(tts.speak) == "function" then
        speech_id = tts.speak(text)
    end
end
local function animate(pose) animation_pose, animation_until = pose, uptime + 3000 end
local function headline(default)
    if uptime < toast_until then return toast end
    if save_error then return "进度暂未保存，稍后重试" end
    return default
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
        screen.text(label, x + math.max(2, math.floor((w - width_of(label)) / 2)), 193, index == selected and WHITE or INK)
    end
    centered("单击切换  双击确认", 220, MUTED)
end
local function character(pose, small)
    local chosen = uptime < animation_until and animation_pose or pose
    if small then draw_character(chosen, 88, 58, 2)
    else
        local bob = chosen == 1 and math.floor(math.sin(uptime / 450) * 1.5) or 0
        screen.rect(60, 178, 120, 3, LIGHT)
        draw_character(chosen, 72, 58 + bob, 3)
    end
end
local function shelf()
    screen.rect(14, 124, 42, 4, 0xE3C49F)
    screen.rect(184, 124, 42, 4, 0xE3C49F)
    local colors = {0x76A978, 0xE4B3A1, 0x9AAEB5, 0xEAB46B, BLUE, GOLD}
    for index = 1, 6 do
        if game.has_item(index) then
            local x = index <= 3 and (15 + (index - 1) * 14) or (185 + (index - 4) * 14)
            screen.rect(x, 112 - index % 2 * 3, 10, 12 + index % 2 * 3, colors[index])
            screen.rect(x + 2, 114 - index % 2 * 3, 3, 3, WHITE)
        end
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
local function draw_item(index, owned)
    local color = owned and GOLD or 0xBAC8CB
    screen.rect(79, 63, 82, 66, CARD)
    if not owned then
        screen.rect(96, 79, 48, 36, color); screen.rect(94, 76, 52, 7, MUTED); screen.rect(118, 77, 5, 38, LIGHT)
    elseif index == 1 then
        screen.rect(98, 79, 45, 24, 0x76A978); screen.rect(88, 102, 64, 8, 0x527D58); screen.rect(118, 70, 4, 15, 0x527D58)
    elseif index == 2 then
        screen.rect(103, 78, 34, 31, 0xE4B3A1); screen.rect(96, 86, 48, 17, 0xE4B3A1)
        screen.rect(115, 79, 4, 27, 0xFFF0DB); screen.rect(124, 81, 4, 24, 0xFFF0DB)
    elseif index == 3 then
        screen.rect(97, 83, 47, 26, 0x9AAEB5); screen.rect(103, 78, 32, 36, 0x9AAEB5); screen.rect(105, 84, 18, 5, LIGHT)
    elseif index == 4 then
        screen.rect(118, 91, 4, 27, 0x78A476); screen.rect(107, 77, 26, 24, 0xEAB46B)
        screen.rect(101, 84, 38, 10, 0xEAB46B); screen.rect(115, 84, 10, 10, 0xFFF0BA)
    elseif index == 5 then
        screen.rect(100, 73, 40, 48, BLUE); screen.rect(104, 77, 32, 40, WHITE)
        screen.rect(109, 89, 22, 12, LIGHT); screen.rect(114, 84, 12, 20, LIGHT)
    else
        screen.rect(106, 72, 11, 35, BLUE); screen.rect(123, 72, 11, 35, BLUE)
        screen.rect(103, 96, 34, 24, GOLD); screen.rect(109, 91, 22, 34, GOLD); screen.rect(117, 101, 6, 13, WHITE)
    end
end
local function cooking_scene(state)
    draw_character(1, 28, 59, 2)
    screen.rect(126, 99, 68, 35, BLUE)
    screen.rect(120, 95, 80, 8, INK)
    screen.rect(137, 87, 47, 8, LIGHT)
    screen.rect(153, 81, 15, 7, INK)
    screen.rect(123, 136, 74, 5, 0xE3C49F)
    if state.cooking_status == "active" then
        local rise = math.floor(uptime / 160) % 3 * 3
        screen.rect(144, 65 - rise, 3, 9, LIGHT)
        screen.rect(174, 68 - rise, 3, 9, LIGHT)
    end
    screen.rect(20, 155, 200, 16, 0xE4C5A8)
    screen.rect(76, 155, 88, 16, 0x92C5A1)
    screen.rect(108, 155, 24, 16, GOLD)
    local marker = 20 + math.floor(state.cooking_position * 2)
    screen.rect(math.min(218, marker), 151, 3, 24, INK)
end
local function draw()
    if not game then return end
    local state = game.snapshot()
    if page == "home" then
        header("肥鱼陪工记", state)
        screen.rect(12, 36, 216, 22, CARD)
        local hints = {"你负责努力，我负责吃饭", "一起专心一小会儿", "小锅开火，等一碗好饭", "每份努力，都有小收获"}
        local hint = hints[home_selection]
        if state.focus_status == "active" then hint = "我陪着你  " .. time_label(state.focus_remaining_ms)
        elseif state.focus_status == "paused" then hint = "歇一小会儿，再继续"
        elseif state.satiety >= 100 and home_selection == 1 then hint = "肚子饱饱的，陪你坐会儿"
        elseif home_selection == 1 and math.floor(uptime / 12000) % 2 == 1 then hint = "抬抬头，伸个懒腰吧" end
        centered(headline(hint), 39, INK)
        shelf()
        character(1, false)
        actions({"喂饭", "专注", "煮饭", "收藏"}, home_selection)
    elseif page == "focus" then
        header("肥鱼陪你专注", state)
        local hint = "专注25分钟，饭和小惊喜"
        if state.focus_status == "active" then hint = "你忙你的，我在这里陪你"
        elseif state.focus_status == "paused" then hint = "歇一小会儿，再继续"
        elseif state.focus_status == "claimed" then hint = "辛苦啦！一起开饭吧" end
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
        centered("本次专注不会获得奖励", 40, INK)
        character(1, true)
        centered("已专注 " .. time_label(state.focus_ms), 146, INK)
        centered("也可以回来继续", 166, MUTED)
        actions({"返回专注", "确认结束"}, selection)
    elseif page == "cook" then
        header("肥鱼小厨房", state)
        centered(headline(state.cooking_status == "active" and "金色完美，绿色也好吃" or "对准火候，煮一碗好饭"), 40, INK)
        cooking_scene(state)
        if state.cooking_status == "active" then
            centered("单击起锅  " .. time_label(state.cooking_remaining_ms), 190, INK)
            centered("完美+2碗  好饭+1碗", 220, MUTED)
        else actions({"开始", "回家"}, selection) end
    elseif page == "cook_result" then
        header(dish_names[cook_result.quality] or "这次的米饭", state)
        centered(dish_lines[cook_result.quality] or "下次也一起做饭吧", 40, INK)
        character(cook_result.rice_added > 0 and 2 or 1, true)
        centered(cook_result.rice_added > 0 and ("米饭 +" .. cook_result.rice_added .. " 碗") or "再试一次，存粮不会减少", 146, INK)
        centered(cook_result.item > 0 and ((cook_result.is_new and "新发现：" or "又遇见：") .. item_names[cook_result.item]) or "轻松做饭，不着急", 166, MUTED)
        actions({"再来", "回家"}, selection)
    elseif page == "bag" then
        header("肥鱼的收藏架", state)
        centered("已收集 " .. state.collection_count .. " / 6", 39, MUTED)
        local owned = game.has_item(selection)
        draw_item(selection, owned)
        centered(selection .. "/6  " .. (owned and "已收集" or "未发现"), 115, MUTED)
        centered(owned and item_names[selection] or "等待下一份小惊喜", 141, INK)
        centered(owned and item_notes[selection] or "专注完成或煮出完美米饭", 163, MUTED)
        actions({"回家"}, 1)
    end
    screen.present()
end
local function set_page(next_page) page, selection, click_at = next_page, 1, nil end
local function error_message(reason)
    local messages = {no_rice = "先专注或煮饭，攒一碗饭", full = "肚子饱饱的，晚点再吃",
        focus_in_progress = "先暂停专注，再来做饭", cooking_in_progress = "先把这锅米饭做好吧",
        reward_pending = "先收下这次的小收获", walk_in_progress = "旧散步进度等待结算"}
    message(messages[reason] or "稍等一下，再试试")
end
local function finish_cooking()
    local ok, result = game.claim_cooking()
    if not ok then return end
    cook_result, dirty, input_after = result, true, uptime + 500
    animation_until = 0
    set_page("cook_result")
    sound(result.rice_added > 0 and 659 or 330, 80)
    say(dish_lines[result.quality])
end
local function begin_cooking()
    local ok, reason = game.start_cooking()
    if ok then
        cook_armed_at, animation_until = uptime + 350, 0
        set_page("cook")
        sound(440, 40)
    else error_message(reason) end
end
local function choose()
    local state = game.snapshot()
    if page == "home" then
        if home_selection == 1 then
            local ok, reason = game.feed()
            if ok then dirty = true; message("开饭啦！白米饭真好吃"); animate(2); sound(523, 70); say("开饭啦，白米饭真好吃！")
            else error_message(reason) end
        elseif home_selection == 2 then set_page("focus")
        elseif home_selection == 3 then set_page("cook")
        else set_page("bag") end
    elseif page == "focus" then
        local choice = focus_labels(state)[selection]
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
    elseif page == "cook" then
        if selection == 2 then set_page("home") else begin_cooking() end
    elseif page == "cook_result" then
        if selection == 2 then set_page("home") else begin_cooking() end
    elseif page == "bag" then set_page("home") end
    draw()
end
local function next_choice()
    if page == "home" then home_selection = home_selection % 4 + 1
    elseif page == "focus" then selection = selection % #focus_labels(game.snapshot()) + 1
    elseif page == "bag" then selection = selection % 6 + 1
    else selection = selection % 2 + 1 end
    draw()
end
local function clock_seconds()
    if config.clock_enabled == false or type(clock) ~= "table" or type(clock.now) ~= "function" then return nil end
    local now = clock.now()
    if type(now) == "number" and now == now and now > 0 and now < math.huge then return now end
    return nil
end
function on_start()
    page, home_selection, selection = "home", 1, 1
    uptime, draw_elapsed, click_at, tick_count = 0, 0, nil, 0
    toast, toast_until, animation_pose, animation_until = "", 0, 1, 0
    dirty, last_save, save_error, speech_id = false, 0, nil, nil
    cook_armed_at, input_after, cook_result, last_clock = 0, 0, nil, clock_seconds()
    local saved = nil
    if config.storage_enabled ~= false and type(storage) == "table" and type(storage.load) == "function" then saved = storage.load() end
    game = create_game({saved = saved})
    local state = game.snapshot()
    -- Retire a legacy walk using only already stored progress; no new steps.
    if state.status == "active" or state.status == "paused" or state.status == "finished" then
        game.finish_walk(); game.claim_walk()
    end
    if state.focus_status == "finished" then game.claim_focus(); page = "focus" end
    dirty = true
    draw()
end
function on_tick(dt_ms)
    if not game then return end
    tick_count = tick_count + 1
    local dt = type(dt_ms) == "number" and dt_ms == dt_ms and math.floor(math.max(0, math.min(5000, dt_ms))) or 0
    uptime, draw_elapsed = uptime + dt, draw_elapsed + dt
    local observed, now = dt, clock_seconds()
    if now then
        local difference = last_clock and now - last_clock or 0
        observed = difference >= 0 and difference <= 5 and difference * 1000 or 0
    end
    last_clock = now
    local before = game.snapshot()
    local event = game.update(dt, observed)
    if before.focus_status == "active" or (event and event.kind == "satiety_changed") then dirty = true end
    if event and event.kind == "focus_finished" then
        local ok, result = game.claim_focus()
        if ok then
            dirty = true
            message(result.is_new and ("新发现：" .. item_names[result.item]) or "专注完成！一起开饭吧", 5000)
            animate(3); sound(659, 100); say("辛苦啦，一起开饭吧！"); set_page("focus")
        end
    elseif event and event.kind == "cooking_finished" then finish_cooking() end
    if click_at and uptime - click_at >= 320 then click_at = nil; next_choice() end
    if dirty and uptime - last_save >= 10000 then
        last_save = uptime
        if config.storage_enabled ~= false and type(storage) == "table" and type(storage.save) == "function" then
            local ok, reason = storage.save(game.export(), 2592000)
            if ok then dirty, save_error = false, nil else save_error = reason or "unavailable" end
        elseif config.storage_enabled ~= false then save_error = "unavailable" end
    end
    local interval = page == "cook" and game.snapshot().cooking_status == "active" and 60 or 250
    if draw_elapsed >= interval then draw_elapsed = 0; draw() end
end
function on_button_click(button_id)
    if button_id ~= "function" or not game or uptime < input_after then return end
    if page == "cook" and game.snapshot().cooking_status == "active" then
        click_at = nil
        if uptime >= cook_armed_at then
            if game.stop_cooking() then finish_cooking(); draw() end
        end
        return
    end
    if click_at and uptime - click_at < 320 then click_at = nil; choose() else click_at = uptime end
end
function on_tts_result(id, result) if id == speech_id then speech_id = nil end end
function ricestep_debug()
    return {page = page, selection = selection, home_selection = home_selection,
        state = game and game.snapshot() or nil, save_error = save_error}
end
