-- Pure rules for RiceStep. Concatenate before the UI; the sandbox has no require.
-- Time and measured steps are deliberately separate input sources.
-- All methods use dot calls. Persist export(), never the derived snapshot().
-- set_steps expects measured active-session totals, not a raw lifetime counter.
function create_game(options)
    options = type(options) == "table" and options or {}
    local function integer(value, fallback, low, high)
        if type(value) ~= "number" or value ~= value or value == math.huge or value == -math.huge then
            return fallback
        end
        return math.max(low, math.min(high, math.floor(value)))
    end
    local timer_unit = integer(options.timer_rice_ms, 300000, 1000, 3600000)
    local step_unit = integer(options.steps_per_rice, 500, 1, 10000)
    local focus_duration = integer(options.focus_duration_ms, 1500000, 1000, 3600000)
    local desk_interval = integer(options.desk_interval_ms, 1800000, 1000, 7200000)
    local desk_snooze = integer(options.desk_snooze_ms, 300000, 1000, 3600000)
    local DESK_OFF, DESK_ACTIVE, DESK_DUE, DESK_SNOOZED, DESK_PAUSED, DESK_SNOOZE_PAUSED = 0, 1, 2, 3, 4, 5
    local desk_names = {"off", "active", "due", "snoozed", "paused", "snooze_paused"}
    local random = type(options.random) == "function" and options.random or math.random
    local MAX_RICE, MAX_WALK_MS, MAX_WALK_STEPS = 999, 86400000, 200000
    local state = {
        v = 1, rice = 3, satiety = 50, mood = 70, bond = 0, fed = 0,
        walks = 0, total_ms = 0, total_steps = 0, collection = 0,
        last_item = 0, last_new = false, session_id = 0, claimed_id = 0,
        status = "idle", source = "none", elapsed_ms = 0, step_count = 0,
        pending_item = 0, timer_carry = 0, step_carry = 0, decay_ms = 0,
        focus_status = "idle", focus_ms = 0, focus_id = 0, focus_claimed = 0, focus_count = 0,
        cook_count = 0, perfect_count = 0, last_meal = "",
        desk = DESK_OFF, desk_left = desk_interval,
    }
    local bounds = {
        rice = {0, MAX_RICE}, satiety = {20, 100}, mood = {0, 100},
        bond = {0, 9999}, fed = {0, 1000000}, walks = {0, 1000000},
        total_ms = {0, 1000000000000}, total_steps = {0, 1000000000},
        collection = {0, 63}, last_item = {0, 6}, session_id = {0, 1000000000},
        claimed_id = {0, 1000000000}, elapsed_ms = {0, MAX_WALK_MS},
        step_count = {0, MAX_WALK_STEPS}, pending_item = {0, 6},
        timer_carry = {0, timer_unit - 1}, step_carry = {0, step_unit - 1},
        decay_ms = {0, 299999},
        focus_ms = {0, focus_duration}, focus_id = {0, 1000000000},
        focus_claimed = {0, 1000000000}, focus_count = {0, 1000000},
        cook_count = {0, 1000000}, perfect_count = {0, 1000000},
        desk_left = {0, math.max(desk_interval, desk_snooze)},
    }
    local saved = options.saved
    if type(saved) == "table" and saved.v == 1 then
        for key, range in pairs(bounds) do
            state[key] = integer(saved[key], state[key], range[1], range[2])
        end
        state.last_new = saved.last_new == true
        if saved.last_meal == "perfect" or saved.last_meal == "good" or
            saved.last_meal == "raw" or saved.last_meal == "burnt" then
            state.last_meal = saved.last_meal
        end
        if saved.source == "timer" or saved.source == "sensor" then
            state.source = saved.source
        end
        if saved.status == "active" or saved.status == "paused" then
            -- A reboot cannot prove the user was walking while the app was away.
            state.status = "paused"
        elseif saved.status == "finished" or saved.status == "claimed" then
            state.status = saved.status
        end
        state.claimed_id = math.min(state.claimed_id, state.session_id)
        if state.source == "timer" then state.step_count = 0 end
        if state.source == "none" or state.session_id == 0 then state.status = "idle" end
        if state.status ~= "idle" and state.claimed_id == state.session_id then
            state.status = "claimed"
        end
        if state.status == "idle" then
            state.source, state.elapsed_ms, state.step_count, state.pending_item = "none", 0, 0, 0
        end
        if saved.focus_status == "active" or saved.focus_status == "paused" then
            state.focus_status = "paused"
        elseif saved.focus_status == "finished" or saved.focus_status == "claimed" or saved.focus_status == "cancelled" then
            state.focus_status = saved.focus_status
        end
        state.focus_claimed = math.min(state.focus_claimed, state.focus_id)
        if state.focus_id == 0 then state.focus_status, state.focus_ms = "idle", 0 end
        if state.focus_status ~= "idle" and state.focus_status ~= "cancelled" and
            state.focus_claimed == state.focus_id then state.focus_status = "claimed" end
        if state.focus_status == "finished" and state.focus_ms < focus_duration then state.focus_status = "paused" end
        -- Only an explicit desk session can remind. Reload never infers sitting
        -- or elapsed time while the app was closed; active timers await resume.
        if saved.desk == DESK_ACTIVE or saved.desk == DESK_PAUSED then
            state.desk = DESK_PAUSED
            state.desk_left = math.min(state.desk_left, desk_interval)
        elseif saved.desk == DESK_SNOOZED or saved.desk == DESK_SNOOZE_PAUSED then
            state.desk = DESK_SNOOZE_PAUSED
            state.desk_left = math.min(state.desk_left, desk_snooze)
        elseif saved.desk == DESK_DUE then
            state.desk, state.desk_left = DESK_DUE, 0
        else state.desk_left = desk_interval end
    end

    local game = {}
    -- A cooking round is deliberately transient: reloading never awards it.
    local cooking_status, cooking_ms, cooking_quality = "idle", 0, "none"
    local function cooking_position()
        local phase = cooking_ms % 4000
        return math.floor((phase <= 2000 and phase or 4000 - phase) / 20)
    end
    local function copy_state()
        local data = {}
        for key, value in pairs(state) do data[key] = value end
        return data
    end
    local function pending_reward()
        if state.source == "timer" then
            return math.floor((state.timer_carry + state.elapsed_ms) / timer_unit)
        elseif state.source == "sensor" then
            return math.floor((state.step_carry + state.step_count) / step_unit)
        end
        return 0
    end
    local function has_item(index)
        if index < 1 or index > 6 then return false end
        local flag = 2 ^ (index - 1)
        return math.floor(state.collection / flag) % 2 == 1
    end
    local function discover(index)
        state.last_item, state.last_new = index, index > 0 and not has_item(index)
        if index > 0 then
            if state.last_new then state.collection = state.collection + 2 ^ (index - 1) end
            state.bond = math.min(9999, state.bond + (state.last_new and 5 or 1))
            state.mood = math.min(100, state.mood + 5)
        end
    end
    local function random_item()
        return integer(random(1, 6), 1, 1, 6)
    end

    function game.snapshot()
        local data = copy_state()
        data.pending_rice = (state.status == "active" or state.status == "paused" or state.status == "finished")
            and pending_reward() or 0
        data.timer_rice_ms, data.steps_per_rice = timer_unit, step_unit
        data.focus_duration_ms = focus_duration
        data.focus_remaining_ms = math.max(0, focus_duration - state.focus_ms)
        data.desk_status, data.desk_remaining_ms = desk_names[state.desk + 1], state.desk_left
        data.desk_interval_ms, data.desk_snooze_ms = desk_interval, desk_snooze
        data.cooking_status, data.cooking_quality = cooking_status, cooking_quality
        data.cooking_elapsed_ms, data.cooking_remaining_ms = cooking_ms, 10000 - cooking_ms
        data.cooking_position = cooking_position()
        data.collection_count = 0
        for index = 1, 6 do
            if has_item(index) then data.collection_count = data.collection_count + 1 end
        end
        return data
    end

    function game.export()
        -- Exactly 32 scalar fields; desk uses only its mode and remaining ms.
        -- Caller supplies TTL and respects storage's write rate.
        return copy_state()
    end

    function game.has_item(index)
        return has_item(integer(index, 0, 0, 6))
    end

    function game.feed()
        if state.rice < 1 then return false, "no_rice" end
        if state.satiety >= 100 then return false, "full" end
        state.rice = state.rice - 1
        state.satiety = math.min(100, state.satiety + 18)
        state.mood = math.min(100, state.mood + 8)
        state.bond = math.min(9999, state.bond + 3)
        state.fed = math.min(1000000, state.fed + 1)
        return true, {kind = "fed", rice = state.rice, satiety = state.satiety}
    end

    function game.start_walk(source)
        if cooking_status == "active" or cooking_status == "finished" then return false, "cooking_in_progress" end
        if source ~= "timer" and source ~= "sensor" then return false, "invalid_source" end
        if state.focus_status == "active" or state.focus_status == "paused" or state.focus_status == "finished" then
            return false, "focus_in_progress"
        end
        if state.status == "active" or state.status == "paused" then return false, "walk_in_progress" end
        if state.status == "finished" then return false, "reward_pending" end
        if state.session_id >= 1000000000 then return false, "session_limit" end
        state.session_id = state.session_id + 1
        state.status, state.source = "active", source
        state.elapsed_ms, state.step_count, state.pending_item = 0, 0, 0
        return true, {kind = "walk_started", source = source, session_id = state.session_id}
    end

    function game.pause_walk()
        if state.status ~= "active" then return false, "not_active" end
        state.status = "paused"
        return true, {kind = "walk_paused"}
    end

    function game.resume_walk()
        if cooking_status == "active" or cooking_status == "finished" then return false, "cooking_in_progress" end
        if state.status ~= "paused" then return false, "not_paused" end
        state.status = "active"
        return true, {kind = "walk_resumed"}
    end

    function game.update(dt_ms, observed_ms)
        -- Animation timing and observed elapsed time can differ on busy hardware.
        -- The caller rejects clock jumps; the rules also bound each accepted delta.
        local dt = integer(dt_ms, 0, 0, 5000)
        local observed = integer(observed_ms, dt, 0, 5000)
        local desk_became_due = false
        if state.desk == DESK_ACTIVE or state.desk == DESK_SNOOZED then
            state.desk_left = math.max(0, state.desk_left - observed)
            if state.desk_left == 0 then
                state.desk, desk_became_due = DESK_DUE, true
            end
        end
        local changed_satiety = false
        state.decay_ms = state.decay_ms + observed
        if state.decay_ms >= 300000 then
            state.decay_ms = state.decay_ms - 300000
            changed_satiety = state.satiety > 20
            state.satiety = math.max(20, state.satiety - 1)
        end
        if state.status == "active" then
            state.elapsed_ms = math.min(MAX_WALK_MS, state.elapsed_ms + observed)
        end
        if state.focus_status == "active" then
            state.focus_ms = math.min(focus_duration, state.focus_ms + observed)
            if state.focus_ms >= focus_duration then
                state.focus_status = "finished"
                return {kind = "focus_finished"}
            end
        end
        if cooking_status == "active" then
            cooking_ms = math.min(10000, cooking_ms + dt)
            if cooking_ms >= 10000 then
                cooking_status, cooking_quality = "finished", "burnt"
                return {kind = "cooking_finished", quality = cooking_quality}
            end
        end
        if desk_became_due then return {kind = "desk_due"} end
        if changed_satiety then return {kind = "satiety_changed"} end
    end

    -- Manual desk reminder only. These operations never grant food, steps,
    -- exercise credit, or infer presence from a camera/absence of movement.
    function game.start_desk()
        if state.desk ~= DESK_OFF then return false, "desk_in_progress" end
        state.desk, state.desk_left = DESK_ACTIVE, desk_interval
        return true
    end
    function game.pause_desk()
        if state.desk == DESK_ACTIVE then state.desk = DESK_PAUSED
        elseif state.desk == DESK_SNOOZED then state.desk = DESK_SNOOZE_PAUSED
        else return false, "not_active" end
        return true
    end
    function game.resume_desk()
        if state.desk == DESK_PAUSED then state.desk = DESK_ACTIVE
        elseif state.desk == DESK_SNOOZE_PAUSED then state.desk = DESK_SNOOZED
        else return false, "not_paused" end
        return true
    end
    function game.snooze_desk()
        if state.desk ~= DESK_DUE then return false, "not_due" end
        state.desk, state.desk_left = DESK_SNOOZED, desk_snooze
        return true
    end
    function game.desk_break()
        if state.desk == DESK_OFF then return false, "not_active" end
        -- User explicitly acknowledges a break; feeding/cooking never calls this.
        state.desk, state.desk_left = DESK_ACTIVE, desk_interval
        return true
    end
    function game.stop_desk()
        if state.desk == DESK_OFF then return false, "not_active" end
        state.desk, state.desk_left = DESK_OFF, desk_interval
        return true
    end

    function game.set_steps(session_total)
        if state.status ~= "active" then return false, "not_active" end
        if state.source ~= "sensor" then return false, "not_sensor" end
        if type(session_total) ~= "number" or session_total ~= session_total or
            session_total == math.huge or session_total == -math.huge or
            session_total < 0 or session_total % 1 ~= 0 or session_total > MAX_WALK_STEPS then
            return false, "invalid_steps"
        end
        if session_total < state.step_count then return false, "steps_went_backwards" end
        -- An absolute per-session count makes repeated samples idempotent.
        state.step_count = session_total
        return true, {kind = "steps_updated", step_count = state.step_count}
    end

    function game.finish_walk()
        if state.status == "finished" then
            return true, {kind = "walk_finished", pending_rice = pending_reward(), repeated = true}
        end
        if state.status == "claimed" then return false, "already_claimed" end
        if state.status ~= "active" and state.status ~= "paused" then return false, "no_walk" end
        state.status = "finished"
        if pending_reward() > 0 then
            state.pending_item = random_item()
        end
        return true, {kind = "walk_finished", pending_rice = pending_reward(), source = state.source}
    end

    function game.claim_walk()
        if state.status == "claimed" or state.claimed_id == state.session_id then
            return false, "already_claimed"
        end
        if state.status ~= "finished" then return false, "not_finished" end
        local reward = pending_reward()
        local added = math.min(reward, MAX_RICE - state.rice)
        state.rice = state.rice + added
        if state.source == "timer" then
            state.timer_carry = (state.timer_carry + state.elapsed_ms) % timer_unit
        else
            state.step_carry = (state.step_carry + state.step_count) % step_unit
        end
        state.total_ms = math.min(1000000000000, state.total_ms + state.elapsed_ms)
        state.total_steps = math.min(1000000000, state.total_steps + state.step_count)
        state.walks = math.min(1000000, state.walks + 1)
        state.claimed_id, state.status = state.session_id, "claimed"
        discover(reward > 0 and state.pending_item or 0)
        state.pending_item = 0
        return true, {
            kind = "walk_claimed", rice_added = added, rice_earned = reward,
            source = state.source, elapsed_ms = state.elapsed_ms, step_count = state.step_count,
            item = state.last_item, is_new = state.last_new,
        }
    end

    function game.start_focus()
        if cooking_status == "active" or cooking_status == "finished" then return false, "cooking_in_progress" end
        if state.status == "active" or state.status == "paused" or state.status == "finished" then
            return false, "walk_in_progress"
        end
        if state.focus_status == "active" or state.focus_status == "paused" then return false, "focus_in_progress" end
        if state.focus_status == "finished" then return false, "reward_pending" end
        if state.focus_id >= 1000000000 then return false, "session_limit" end
        state.focus_id, state.focus_ms, state.focus_status = state.focus_id + 1, 0, "active"
        return true, {kind = "focus_started"}
    end

    function game.pause_focus()
        if state.focus_status ~= "active" then return false, "not_active" end
        state.focus_status = "paused"
        return true, {kind = "focus_paused"}
    end

    function game.resume_focus()
        if cooking_status == "active" or cooking_status == "finished" then return false, "cooking_in_progress" end
        if state.focus_status ~= "paused" then return false, "not_paused" end
        state.focus_status = "active"
        return true, {kind = "focus_resumed"}
    end

    function game.cancel_focus()
        if state.focus_status ~= "active" and state.focus_status ~= "paused" then return false, "no_focus" end
        state.focus_status, state.focus_claimed = "cancelled", state.focus_id
        return true, {kind = "focus_cancelled"}
    end

    function game.claim_focus()
        if state.focus_status ~= "finished" or state.focus_claimed == state.focus_id then return false, "not_finished" end
        state.focus_status, state.focus_claimed = "claimed", state.focus_id
        state.focus_count = math.min(1000000, state.focus_count + 1)
        local added = state.rice < MAX_RICE and 1 or 0
        state.rice = state.rice + added
        state.bond, state.mood = math.min(9999, state.bond + 3), math.min(100, state.mood + 4)
        discover(random_item())
        return true, {kind = "focus_claimed", rice_added = added, source = "focus", item = state.last_item, is_new = state.last_new}
    end

    function game.start_cooking()
        if cooking_status == "active" then return false, "cooking_in_progress" end
        if cooking_status == "finished" then return false, "reward_pending" end
        if state.focus_status == "active" then return false, "focus_in_progress" end
        if state.focus_status == "finished" then return false, "reward_pending" end
        if state.status == "active" or state.status == "paused" or state.status == "finished" then return false, "walk_in_progress" end
        cooking_status, cooking_ms, cooking_quality = "active", 0, "none"
        return true, {kind = "cooking_started"}
    end

    function game.stop_cooking()
        if cooking_status == "finished" then return true, {kind = "cooking_finished", quality = cooking_quality} end
        if cooking_status ~= "active" then return false, "not_active" end
        local position = cooking_position()
        if position >= 44 and position <= 56 then cooking_quality = "perfect"
        elseif position >= 28 and position <= 72 then cooking_quality = "good"
        elseif position < 28 then cooking_quality = "raw"
        else cooking_quality = "burnt" end
        cooking_status = "finished"
        return true, {kind = "cooking_finished", quality = cooking_quality}
    end

    function game.claim_cooking()
        if cooking_status ~= "finished" then return false, "not_finished" end
        cooking_status = "claimed"
        -- Remember only a settled meal; an interrupted round leaves the last one intact.
        state.last_meal = cooking_quality
        state.cook_count = math.min(1000000, state.cook_count + 1)
        local earned = cooking_quality == "perfect" and 2 or (cooking_quality == "good" and 1 or 0)
        local added = math.min(earned, MAX_RICE - state.rice)
        state.rice = state.rice + added
        if cooking_quality == "perfect" then state.perfect_count = math.min(1000000, state.perfect_count + 1) end
        discover(cooking_quality == "perfect" and random_item() or 0)
        return true, {kind = "cooking_claimed", quality = cooking_quality,
            rice_added = added, rice_earned = earned, item = state.last_item, is_new = state.last_new}
    end

    return game
end
