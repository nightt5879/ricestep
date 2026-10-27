-- Optional local relay transport. No URL means no networking or invented steps.
local relay_url = type(RICESTEP_CONFIG) == "table" and RICESTEP_CONFIG.step_url or nil
if type(relay_url) == "string" and relay_url ~= "" then
    relay_url = relay_url .. (relay_url:find("?", 1, true) and "&" or "?") .. "fresh=1"
end
local relay_elapsed, relay_pending = 8000, nil
local ui_tick, ui_response = on_tick, on_http_response
local function relay_status(status)
    if type(on_step_status) == "function" then on_step_status(status) end
end
function on_tick(dt_ms)
    ui_tick(dt_ms)
    if type(relay_url) ~= "string" or relay_url == "" or type(http) ~= "table" then return end
    relay_elapsed = relay_elapsed + math.max(0, math.min(dt_ms, 60000))
    if relay_elapsed >= 10000 and not relay_pending then
        relay_elapsed = 0
        relay_pending = http.get(relay_url, { timeout_ms = 6000, max_response_bytes = 2048 })
    end
end
function on_http_response(id, response)
    if id ~= relay_pending then
        if type(ui_response) == "function" then ui_response(id, response) end
        return
    end
    relay_pending = nil
    if response.error then relay_status("offline"); return end
    if response.status == 204 then relay_status("waiting"); return end
    if response.status ~= 200 then relay_status("offline"); return end
    local value = json.decode(response.body)
    if type(value) ~= "table" or type(value.total) ~= "number"
       or value.total ~= math.floor(value.total) or value.total < 0 or value.total > 200000
       or type(value.day) ~= "string" or not value.day:match("^%d%d%d%d%-%d%d%-%d%d$")
       or value.source ~= "apple_health" then relay_status("invalid"); return end
    if on_step_sample(value.total, value.day) == false then relay_status("invalid")
    else relay_status("ready") end
end
