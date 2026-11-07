# Skills

[Back to the checklist](00-before-you-start.md)

![A health check stays quiet unless you need to act](images/time-2.svg)

Phase 1 answers a question you just asked, or sends a parked command you just asked for:

- **Status** answers a question you just asked. It does not push a notification and it does not include coordinates or a VIN.
- **Flash lights** requires Park and speed 0, sends one signed command, and reads the car again.
- **Climate on or off** uses the same park rule and checks the climate flag afterward.
- **`agentdrive check`** is the health check. It does not wake a sleeping car. On install, schedule `agentdrive check --json` every 30 minutes. Notify only when it exits 1. The same alert exits 0 until it clears and later returns. See [AI agents](07-assistants.md).
- **`agentdrive report`** is the live parked snapshot. **`agentdrive history due`** is the morning usage briefing. On install, create one daily job at the time in `agentdrive report-schedule` (default 07:30 local time, enabled). Run `agentdrive history due` and send that briefing. On Monday it is the previous week, on the 1st the previous month, and on 1 January the previous year. Run `agentdrive report` when you want the car's current status, including the skip line. AgentDrive does not run that job itself. It is separate from the health check.
- **`agentdrive where`** is a coarse area: suburb or locality, state or region, and country. It does not wake a sleeping car and it does not print coordinates. A rounded point is sent to Nominatim. It is not part of the recurring health check.
- **`agentdrive charge-schedule show`**, **`set`**, and **`remove`** read and change the in-car charging timetable. `show` does not wake a sleeping car. `set` and `remove` need Park, speed 0, and a fresh confirmation. A scheduled job cannot confirm them.

Calling onboarding, trip advisor, or charging tips fails. The Monday weekly briefing can include one new official item from the Tesla site for the owner's country. `agentdrive news` prints that item. When there is nothing new, that part is left out.

Habits and suburb-level places live in the profile file. Copy `config/profile.example.yaml` outside the repository before you fill it in. Do not put a street, a VIN, a token, or a live coordinate in it.

## Expected result

A direct status question gets a sentence back. `agentdrive check`, `agentdrive report`, `agentdrive where`, and `agentdrive charge-schedule show` print that they skipped, and exit 0, when the car is asleep. The AI agent still sends the report.
