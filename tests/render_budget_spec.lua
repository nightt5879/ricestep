scenarios = {
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
    {"rest/entry", function() boot(); single(); single() end},
    {"desk/off", function() single(); single(); double(); double() end},
    {"desk/active", function() double(); actual_tick(500) end},
    {"desk/paused", function() single(); double() end},
    {"desk/due", function() double(); actual_tick(4000) end},
    {"home/due reminder", function()
        single(); single(); single(); double()
        check(ricestep_debug().page == "home", "due reminder can return home")
        double(); double()
    end},
    {"desk/snoozed", function() single(); double(); actual_tick(100) end},
    {"desk/closed", function() single(); single(); double() end},
}
required_poses = {1, 2, 3, 4, 5, 6}
required_pages = {"home", "focus", "focus_confirm", "cook", "cook_result", "bag", "rest", "desk"}
