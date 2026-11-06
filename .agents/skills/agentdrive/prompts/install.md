Install AgentDrive so this Muse agent can use the vehicle.

Clone or download https://github.com/agentdrive-dev/agentdrive.
Read README.md, DISCLAIMER.md, and SECURITY.md.
Show the disclaimer and the security reminder in full, then stop and wait.
Do not install anything until I reply in a later message with the acceptance sentence.

After I accept, install from the repository root with bash setup/install.sh --adapter muse.
Git Bash can run that script on Windows. If the shell is PowerShell only, run python -m pip install -e ., set the variables below, and run python -m core adapter install --adapter muse.
Building tesla-control still needs Go. Its Bluetooth path does not support Windows. Internet commands are what AgentDrive uses.
The config home is $AGENTDRIVE_HOME when that variable is set.
Otherwise use ~/.config/agentdrive on macOS and Linux, and C:\Users\<you>\.config\agentdrive on Windows.

Point AgentDrive at the existing private key file and the existing Tesla OAuth token file.
Do not generate a new key. Do not paste the key or the tokens into chat. Do not copy them into the git checkout.
Set AGENTDRIVE_KEY_FILE, or set key_file in $AGENTDRIVE_HOME/runtime.yaml.
A placeholder key path is C:\Users\<you>\agentdrive-key\private-key.pem. Replace <you> and use the real file.
Set AGENTDRIVE_TOKEN_FILE, or set token_file in that same runtime.yaml, to the existing token file.
Copy config/runtime.example.yaml to see both fields.

Sharing a key that is already paired with another agent is supported. You do not pair a second key for that case.
The security trade-off: one private key file can sign vehicle commands for every agent that can read it, together with a valid token, inside the scopes that were approved. Trust every agent that can read the file. Keep a single copy, limited to my user. A second copy is a second secret.
The token file rotates when it is refreshed. Point at the existing file. A second token copy stops working after the next refresh. Two agents refreshing the same file can invalidate each other, so use one writer.

Then run `agentdrive doctor` and a read-only vehicle status check before any command.
The read-only check is `agentdrive check`. It does not wake a sleeping vehicle and it does not send a command.
Do not flash, lock, unlock, or change climate or charging until doctor and that check have finished.

Then read `agentdrive report-schedule`. The default is 07:30 local time and enabled.
Create a daily job at that time that runs `agentdrive report` and send every result to me, including the skip line when the vehicle is asleep.
If I later ask for a different time, run `agentdrive report-schedule set HH:MM` and update the job.
If I ask to turn the report off, run `agentdrive report-schedule disable` and remove the job.
AgentDrive stores the time and does not run in the background.
The same daily job is used for every shell-capable assistant.
Keep every secret in the config home or in the existing key and token files.
