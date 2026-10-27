-- One owned utterance and one short-lived pending line. No playback during startup.
-- Recorded clips use the Mini3 API 5 HTTP audio player; older runtimes use TTS.
function create_voice(options)
    options = type(options) == "table" and options or {}
    local enabled = options.enabled ~= false
    local base = options.base_url
    if type(base) == "string" and (base:match("^https://") or base:match("^http://")) then
        base = base:gsub("/+$", "")
    else base = nil end
    local now, ticks, active, pending = 0, 0, nil, nil
    local recent, last_status = {}, "idle"
    local voice = {}

    local function audio_state()
        if type(audio) == "table" and type(audio.get_state) == "function" then
            return audio.get_state()
        end
        return "idle"
    end
    local function audio_available()
        return base and type(audio) == "table" and type(audio.play) == "function"
            and type(audio.stop) == "function" and type(audio.get_state) == "function"
    end
    local function clear(status)
        active, last_status = nil, status
    end
    local function stop_owned()
        if not active then return true end
        if active.mode == "audio" then
            -- Only accepted audio.play requests are marked as owned.
            audio.stop()
        elseif active.mode == "tts" then
            if type(tts) ~= "table" or type(tts.cancel) ~= "function" then return false end
            tts.cancel(active.id)
        end
        return true
    end
    local function start_tts(line, started)
        if type(tts) ~= "table" or type(tts.speak) ~= "function" then
            clear("unavailable")
            return
        end
        local id = tts.speak(line.text)
        if type(id) ~= "number" then clear("failed"); return end
        active = {line = line, mode = "tts", id = id, started = started or now}
        last_status = "speaking"
    end
    local function start(line)
        if audio_available() then
            if audio.play(base .. "/" .. line.key .. ".mp3") == true then
                active = {line = line, mode = "audio", started = now, played = false}
                last_status = "loading"
            else
                -- A rejected request owns no audio channel and must not stop it.
                start_tts(line)
            end
        else start_tts(line) end
    end

    function voice.say(key, text, priority)
        if not enabled then return false, "disabled" end
        if type(key) ~= "string" or #key == 0 or #key > 48 or not key:match("^[%w_-]+$") or
            type(text) ~= "string" or #text == 0 or #text > 512 or text:find("\0", 1, true) then
            return false, "invalid_line"
        end
        local level = 0
        if type(priority) == "number" then
            if priority >= 2 then level = 2 elseif priority >= 1 then level = 1 end
        end
        for item, time in pairs(recent) do if now - time >= 4000 then recent[item] = nil end end
        if recent[key] then return false, "duplicate" end
        if pending and pending.priority > level and now < pending.expires then return false, "priority" end
        recent[key] = now
        pending = {key = key, text = text, priority = level, expires = now + 6000}
        return true
    end

    function voice.update(dt)
        ticks = ticks + 1
        if type(dt) == "number" and dt == dt and dt ~= math.huge and dt ~= -math.huge then
            now = now + math.max(0, math.min(5000, dt))
        end
        if not enabled or ticks <= 1 then return end
        if pending and now >= pending.expires then pending = nil end
        if active then
            local elapsed = now - active.started
            if active.mode == "audio" then
                local state = audio_state()
                if state == "playing" then active.played, last_status = true, "playing" end
                if active.played and (state == "idle" or state == "failed") then
                    clear("completed")
                elseif elapsed >= 15000 then
                    stop_owned(); clear("timeout")
                elseif not active.played and elapsed >= 8000 then
                    local line, started = active.line, active.started
                    stop_owned()
                    start_tts(line, started)
                end
            elseif elapsed >= 15000 then
                stop_owned(); clear("timeout")
            end
        end
        if active and pending and (pending.priority > active.line.priority or pending.priority == 2) then
            if stop_owned() then clear("replaced") end
        end
        if not active and pending and audio_state() == "idle" then
            local line = pending
            pending = nil
            start(line)
        end
    end

    function voice.on_tts_result(id, result)
        if active and active.mode == "tts" and active.id == id then
            clear(type(result) == "table" and result.status or "completed")
        end
    end
    function voice.busy()
        return active ~= nil or pending ~= nil or audio_state() ~= "idle"
    end
    function voice.debug()
        return {
            active_key = active and active.line.key or nil,
            pending_key = pending and pending.key or nil,
            mode = active and active.mode or "idle",
            speech_id = active and active.id or nil,
            elapsed_ms = active and now - active.started or 0,
            playing = active and active.played == true or false,
            status = last_status, ticks = ticks, uptime_ms = now,
        }
    end
    return voice
end
