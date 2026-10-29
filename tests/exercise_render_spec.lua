local native_status = {state = "idle"}
local motion_mock = {
    start = function(kind, id) native_status = {state = "starting", session_id = id, kind = kind, reps = 0}; return true end,
    stop = function() native_status = {state = "idle"} end,
    status = function() return native_status end,
}
local function prepare_motion(api)
    motion = api
    boot()
    single(); single()
end
local function open_motion()
    single(); single(); double(); single(); double()
end
scenarios = {
    {"exercise/unavailable setup", function() prepare_motion(nil) end},
    {"exercise/unavailable", function() open_motion() end},
    {"exercise/idle setup", function() prepare_motion(motion_mock) end},
    {"exercise/idle", function() open_motion() end},
    {"exercise/starting", function() double() end},
    {"exercise/real-status-fixture", function()
        native_status.state, native_status.reps, native_status.points, native_status.image_aspect = "running", 12, {}, 4 / 3
        for i = 1, 17 do native_status.points[i] = {x = (i - 1) / 16, y = (17 - i) / 16, confidence = 1} end
        actual_tick(250)
        check(ricestep_debug().exercise_reps == 12, "mock native count must reach display")
        check(#pose_dots == 17 and pose_dots[1].x == 64 and pose_dots[17].x == 173, "4:3 real point bounds")
    end},
    {"exercise/portrait aspect", function()
        native_status.image_aspect = 3 / 4; actual_tick(250)
        check(#pose_dots == 17 and pose_dots[1].x == 88 and pose_dots[17].x == 148, "portrait aspect must fit instead of stretch")
    end},
    {"exercise/landscape aspect", function()
        native_status.image_aspect = 2; actual_tick(250)
        check(#pose_dots == 17 and pose_dots[1].y == 126 and pose_dots[17].y == 73, "wide aspect must fit instead of stretch")
    end},
    {"exercise/missing or invalid aspect", function()
        native_status.image_aspect = nil; actual_tick(250); check(#pose_dots == 0, "missing aspect rejects points")
        for _, value in ipairs({0, -1, math.huge, 0/0}) do
            native_status.image_aspect = value; actual_tick(250); check(#pose_dots == 0, "invalid aspect rejects points")
        end
        native_status.image_aspect = 4 / 3
    end},
    {"exercise/processing limits", function()
        native_status.reason = "too_slow"; actual_tick(250)
        check(frame_text["识别太慢，暂不计数"], "slow inference must be visible")
        native_status.reason = "insufficient_sampling"; actual_tick(250)
        check(frame_text["采样不足，暂不计数"], "insufficient sampling must be visible")
        native_status.reason = "out_of_order"; actual_tick(250)
        check(frame_text["画面顺序异常，暂不计数"], "out-of-order frames must be visible")
        native_status.reason = "ok"
    end},
    {"exercise/rejected-landmarks", function()
        native_status.points[1].x = 0 / 0
        native_status.points[2].y = 2
        native_status.points[3].confidence = math.huge
        native_status.points[4].confidence = -1
        actual_tick(250)
    end},
    {"exercise/stopping", function() double() end},
    {"exercise/error", function() native_status = {state = "error"}; actual_tick(250) end},
    {"exercise/squat", function()
        native_status = {state = "idle"}; actual_tick(250); single(); double()
        check(frame_text["侧身全身入镜，再开始"], "squat guidance matches side-on native geometry")
    end},
    {"exercise/cleanup latch", function()
        native_status = {state = "error", reason = "camera_cleanup_failed"}; actual_tick(250)
        check(frame_text["相机关闭失败，请断电"], "cleanup latch must explain power-off requirement")
        check(frame_text["返回"] and not frame_text["开始"], "cleanup latch cannot offer futile retry")
        native_status.reason = "camera_quarantined"; actual_tick(250)
        check(frame_text["相机异常，请重启设备"], "quarantine must explain reboot requirement")
        check(frame_text["返回"] and not frame_text["开始"], "quarantine cannot offer futile retry")
    end},
}
required_pages = {"home", "rest", "exercise"}
