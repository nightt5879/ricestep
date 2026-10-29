-- Append after app/voice.lua. These mocks never request real playback.
local function equal(actual, expected, label)
    assert(actual == expected, label .. ": expected " .. tostring(expected) .. ", got " .. tostring(actual))
end
local function fixture(base, enabled)
    local calls = {clips = {}, spoken = {}, cancelled = {}, stopped = 0, state = "idle", accepted = true}
    audio = {
        get_state = function() return calls.state end,
        play = function(url)
            calls.clips[#calls.clips + 1] = url
            if calls.accepted then calls.state = "loading" end
            return calls.accepted
        end,
        stop = function() calls.stopped = calls.stopped + 1; calls.state = "idle" end,
    }
    tts = {
        speak = function(text) calls.spoken[#calls.spoken + 1] = text; return #calls.spoken end,
        cancel = function(id) calls.cancelled[#calls.cancelled + 1] = id; return true end,
    }
    return create_voice({enabled = enabled, base_url = base}), calls
end
local function ready(voice)
    voice.update(20)
    voice.update(20)
end
local function advance(voice, milliseconds)
    while milliseconds > 0 do
        local dt = math.min(1000, milliseconds)
        voice.update(dt)
        milliseconds = milliseconds - dt
    end
end

function on_start()
    local voice, calls = fixture("http://example.test/voice/")
    assert(voice.say("hello", "先坐一会儿。"))
    equal(#calls.clips, 0, "say never plays during on_start")
    voice.update(20)
    equal(#calls.clips, 0, "first tick never plays")
    voice.update(20)
    equal(calls.clips[1], "http://example.test/voice/hello.mp3", "clip URL normalized")
    equal(voice.debug().mode, "audio", "recorded voice preferred")
    calls.state = "playing"
    voice.update(20)
    equal(voice.busy(), true, "playing suppresses competing buzzer")
    calls.state = "idle"
    voice.update(20)
    equal(voice.busy(), false, "played clip completes on idle")
    advance(voice, 9000)
    equal(#calls.spoken, 0, "completed or interrupted clip is never reread")

    voice, calls = fixture("builtin://ricestep")
    ready(voice)
    assert(voice.say("feed", "啊呜，好吃。"))
    voice.update(20)
    equal(calls.clips[1], "builtin://ricestep/feed.mp3", "standalone firmware selects its embedded clip")
    calls.state = "playing"
    voice.update(20)
    calls.state = "idle"
    voice.update(20)
    equal(voice.debug().status, "completed", "embedded playback uses the same completion semantics")
    equal(#calls.spoken, 0, "embedded playback never calls device TTS")
    voice, calls = fixture("builtin://other")
    ready(voice)
    assert(voice.say("feed", "啊呜。"))
    voice.update(20)
    equal(#calls.clips, 0, "arbitrary builtin namespaces rejected")

    voice, calls = fixture("https://example.test/voice")
    ready(voice)
    assert(voice.say("hello", "先坐一会儿。", 0))
    voice.update(20)
    assert(voice.say("feed", "我先尝一口。", 1))
    voice.update(20)
    equal(calls.stopped, 1, "normal interaction interrupts background greeting")
    equal(voice.debug().active_key, "feed", "feeding is not blocked by hello")
    assert(voice.say("focus", "你忙吧。", 1))
    voice.update(20)
    equal(calls.stopped, 1, "equal normal priority waits rather than interrupts")
    equal(voice.debug().pending_key, "focus", "normal interaction remains pending")
    equal(voice.say("idle", "我再歇会儿。", 0), false, "background line cannot replace pending interaction")

    voice, calls = fixture("https://example.test/voice")
    ready(voice)
    assert(voice.say("hungry", "有饭吗？"))
    voice.update(20)
    calls.state = "playing"
    voice.update(20)
    assert(voice.say("perfect", "这口得认真吃。", 2))
    voice.update(20)
    equal(calls.stopped, 1, "high result stops only owned previous clip")
    equal(voice.debug().active_key, "perfect", "high result replaces old clip")
    assert(voice.say("idle_a", "我歇会儿。"))
    assert(voice.say("idle_b", "闻着好香。"))
    equal(voice.debug().pending_key, "idle_b", "latest low line replaces pending low line")
    equal(calls.stopped, 1, "low line cannot preempt result")
    assert(voice.say("burnt", "这是锅巴。", 2))
    local accepted, why = voice.say("idle_c", "再歇会儿。")
    equal(accepted, false, "low line cannot displace queued result")
    equal(why, "priority", "priority rejection explicit")
    voice.update(20)
    equal(voice.debug().active_key, "burnt", "latest high result wins")

    voice, calls = fixture("https://example.test/voice")
    ready(voice)
    assert(voice.say("offline", "我等你开饭。"))
    voice.update(20)
    advance(voice, 7900)
    equal(#calls.spoken, 0, "loading gets full grace period")
    advance(voice, 100)
    equal(calls.stopped, 1, "timeout cancels owned stuck download")
    equal(#calls.spoken, 0, "stuck loading never changes to device voice")
    equal(voice.debug().status, "unavailable", "download failure reported")
    equal(voice.busy(), false, "failed download immediately releases queue")
    advance(voice, 7000)
    equal(#calls.cancelled, 0, "system TTS is never cancelled")
    equal(voice.busy(), false, "timeout cannot leave queue blocked")
    advance(voice, 9000)
    equal(#calls.spoken, 0, "timeout never triggers delayed device voice")
    assert(voice.say("feed", "再来一口。"))
    voice.update(20)
    calls.state = "playing"
    voice.update(20)
    equal(voice.debug().status, "playing", "next interaction recovers when server returns")
    advance(voice, 15000)
    equal(voice.busy(), false, "stuck playing has bounded lifetime")
    equal(voice.debug().status, "timeout", "playing timeout recorded")

    voice, calls = fixture("https://example.test/voice")
    ready(voice)
    assert(voice.say("silent_fail", "饭好了。"))
    voice.update(20)
    calls.state = "idle"
    advance(voice, 8000)
    equal(#calls.spoken, 0, "idle without playing cannot start device voice")
    equal(voice.debug().status, "unavailable", "idle without playing is failed loading, not completed")
    voice.on_tts_result(1, {status = "failed"})
    advance(voice, 9000)
    equal(#calls.spoken, 0, "foreign TTS callbacks never trigger playback")

    voice, calls = fixture("https://example.test/voice")
    ready(voice)
    calls.accepted = false
    assert(voice.say("rejected", "这锅有点香。"))
    voice.update(20)
    equal(#calls.spoken, 0, "rejected audio request stays silent")
    equal(voice.busy(), false, "rejection releases queue")
    equal(calls.stopped, 0, "rejected audio request owns nothing to stop")

    voice, calls = fixture("https://example.test/voice")
    ready(voice)
    calls.state = "playing"
    assert(voice.say("external", "开饭啦。", 2))
    advance(voice, 6100)
    equal(#calls.clips, 0, "external audio is never preempted")
    equal(#calls.spoken, 0, "external audio cannot trigger device voice")
    equal(calls.stopped, 0, "external audio is never stopped")
    equal(voice.debug().pending_key, nil, "queued line expires while external channel busy")
    calls.state = "idle"
    voice.update(20)
    equal(voice.busy(), false, "expired line never speaks late")

    voice, calls = fixture(nil)
    ready(voice)
    assert(voice.say("no_url", "碗我摆好了。"))
    voice.update(20)
    equal(#calls.spoken, 0, "missing clip base never uses TTS")
    equal(voice.busy(), false, "missing URL does not block interaction")
    equal(voice.say("no_url", "重复。"), false, "same key debounced")
    advance(voice, 4000)
    assert(voice.say("no_url", "现在可以再说。"))
    voice.update(20)
    equal(#calls.spoken, 0, "debounce expiry never bypasses voice policy")

    voice, calls = fixture("https://example.test/voice")
    audio = nil
    ready(voice)
    assert(voice.say("old_api", "碗我摆好了。"))
    voice.update(20)
    equal(#calls.spoken, 0, "old runtime with TTS but no audio stays silent")
    equal(voice.busy(), false, "old runtime does not block UI")

    voice, calls = fixture("https://example.test/voice")
    ready(voice)
    assert(voice.say("feed", "啊呜。"))
    voice.update(20)
    voice.on_tts_result(1, {status = "completed"})
    equal(voice.debug().active_key, "feed", "system callback cannot clear our clip")

    voice, calls = fixture(nil, false)
    equal(voice.say("disabled", "无声。"), false, "disabled voice rejects line")
    ready(voice)
    equal(#calls.spoken, 0, "disabled voice stays silent")
    voice, calls = fixture(nil)
    ready(voice)
    equal(voice.say("../bad", "无效。"), false, "path traversal key rejected")
    equal(voice.say("empty", ""), false, "empty text rejected before API")
    equal(voice.say("nul", "a\0b"), false, "NUL text rejected before API")
    audio, tts = nil, nil
    assert(voice.say("unavailable", "没关系。"))
    voice.update(20)
    equal(voice.busy(), false, "missing playback APIs fail gracefully")
    screen.begin(0x163A32)
    screen.text("Voice queue: PASS", 16, 32, 0xFFFFFF)
    screen.text("Priority / clip-only", 16, 68, 0xFFFFFF)
    screen.text("Ownership / expiry", 16, 104, 0xFFFFFF)
    screen.present()
end
function on_tick(dt_ms) end
