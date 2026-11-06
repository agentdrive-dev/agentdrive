# Skills

[Back to the checklist](00-before-you-start.md)

![A health check stays quiet unless you need to act](images/time-2.svg)

Phase 1 answers a question you just asked, or sends a parked command you just asked for:

- **Status** answers a question you just asked. It does not push a notification and it does not include coordinates or a VIN.
- **Flash lights** requires Park and speed 0, sends one signed command, and reads the car again.
- **Climate on or off** uses the same park rule and checks the climate flag afterward.
- **`agentdrive check`** is the health check. It does not wake a sleeping car. On install, set that routine up by default and notify only when it exits 1. See [Assistants](07-assistants.md). The morning report is separate planned work. See [Next](../README.md#next).
- **`agentdrive where`** is a coarse area: suburb or locality, state or region, and country. It does not wake a sleeping car and it does not print coordinates. A rounded point is sent to Nominatim. It is not part of the recurring health check.
- **`agentdrive charge-schedule show`**, **`set`**, and **`remove`** read and change the in-car charging timetable. `show` does not wake a sleeping car. `set` and `remove` need Park, speed 0, and a fresh confirmation. An assistant schedule cannot confirm them.

Onboarding, trip advisor, charging tips, and news watch are still to build. Calling one of them fails. News watch, once it exists, stays quiet unless the owner needs to act. See [Next](../README.md#next).

Habits and suburb-level places live in the profile file. Copy `config/profile.example.yaml` outside the repository before you fill it in. Do not put a street, a VIN, a token, or a live coordinate in it.

## Expected result

A direct status question gets a sentence back. `agentdrive check`, `agentdrive where`, and `agentdrive charge-schedule show` print that they skipped, and exit 0, when the car is asleep.
