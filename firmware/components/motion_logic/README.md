# Board-side motion state machines

This C11 component is a native-firmware building block. It **does not capture images, run a pose model, or establish that Mini3 can recognize a person**. It consumes real, timestamped COCO-17 landmarks from a separately verified camera/model/tracker and emits completed-repetition events. Current host tests use explicitly synthetic landmarks and cannot establish vision accuracy, frame rate, or exercise-form correctness.

## Input contract

- Map model outputs back to the original image before calling: normalized x/y in `[0,1]`, confidence in `[0,1]`, and the original `image_aspect = width / height`. Angles are computed with the aspect ratio; treating a widescreen frame as square changes joint geometry.
- Select one person and provide a stable `subject_id`. A changed person or changed visible side discards an incomplete cycle. The producer must not alternate people under the same ID.
- Supply monotonically increasing `uint32_t` milliseconds. Natural tick wrap works if successive deltas are below `2^31`. Duplicate frames and reversed timestamps cannot advance a repetition. Call with `NULL` when the selected pose is missing rather than recycling its last landmarks.
- Begin with `ml_exercise_start`, using a nonzero session ID. The UI must explicitly start/pause/stop exercise. Pause resets the incomplete cycle; resume does not credit paused movement. Completed counts remain in that session.

## Supported state transitions

Push-ups require a side view with a usable shoulder, elbow, wrist, hip, knee and ankle on one side. The torso must be approximately horizontal and the hip/knee chains approximately straight; knee push-ups are not this preset. Squats require shoulder/hip/knee/ankle in a side view with a non-horizontal torso. These are counting gates, not medical or coaching judgments.

Both presets require a held top position, a full descent, a held bottom and a held return to the top. A startup bottom, half repetition, one-frame angle spike, occlusion, person switch or implausibly fast/slow cycle cannot earn a repetition. Default confidence is 0.6, endpoint dwell 180ms, maximum frame gap 400ms, cycle duration 900–12000ms, refractory period 500ms. Angle endpoints are >=155° and <=100° with 10° hysteresis. These settings are provisional engineering thresholds and must be calibrated using the real board's frame rate, view and model before being represented as reliable counting.

`ml_exercise_result` exposes `phase`, `reason`, measured joint angle, `session_id`, cumulative `total_reps`, and a one-update `rep_completed` event. There are no generated steps or automatic rice writes. A downstream reward consumer must store the last accepted `(session_id,total_reps)` and award only the positive unseen delta. Persistently increasing session IDs prevent old sessions from being replayed; do not reuse IDs after restart.

## Desk reminders

The desk timer starts only through `ml_desk_start`; default interval is 30 minutes and snooze is 5 minutes. It measures an explicitly started desk session, **not inferred sitting or camera presence**. An absent frame does not reset it. It emits `new_reminder` once, retains `reminder_due` until snooze/break, and rejects reversed clock values. Snoozing postpones the notification but retains elapsed desk time. An explicit user break or a newly completed exercise repetition resets the interval. `ml_desk_on_rep` rejects repeated or older session totals.

The structures are fixed-size with no allocation or I/O. Link `motion_logic.c` and the platform's math library. The camera/inference adapter, UI, persistent session IDs and durable rice reward accounting remain integration work.

## Host verification

```powershell
pwsh -File scripts/test-motion.ps1
```

The script compiles with GCC, C11 and warnings as errors, then runs deterministic geometry/state/timestamp tests. Use `-Compiler <path>` for another compatible C compiler; GCC/MinGW and Clang/MSVC targets have both passed the 105 assertions. Outputs stay in ignored `.cache/native-tests/`; no board is flashed.
