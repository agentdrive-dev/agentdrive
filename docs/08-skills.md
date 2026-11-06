# Skills

[Back to the checklist](00-before-you-start.md)

![A health check stays quiet unless you need to act](images/time-2.svg)

Phase 1 answers a question you just asked, or sends a parked command you just asked for:

- **Status** answers a question you just asked. It does not push a notification and it does not include coordinates or a VIN.
- **Flash lights** requires Park and speed 0, sends one signed command, and reads the car again.
- **Climate on or off** uses the same park rule and checks the climate flag afterward.
- **`agentdrive check`** is the health check. It does not wake a sleeping car. On install, set that routine up by default and notify only when it exits 1. See [Assistants](07-assistants.md).
- **`agentdrive report`** is the daily briefing. On install, create a daily job at the time in `agentdrive report-schedule` (default 07:30 local time, enabled). Run `agentdrive report` and send the result every time, including the skip line. AgentDrive does not run that job itself. It is separate from the health check.

These are stubs with a TODO for Phase 2: onboarding, trip advisor, charging tips, and news watch. News watch, when it exists, is still supposed to stay quiet unless you need to act.

Habits and suburb-level places live in the profile file. Copy `config/profile.example.yaml` outside the repository before you fill it in. Do not put a street, a VIN, a token, or a live coordinate in it.

## Expected result

A direct status question gets a sentence back. `agentdrive check` prints that it skipped, and exits 0, when the car is asleep.
