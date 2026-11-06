Experimental and testing purposes only.

AgentDrive is an unofficial personal toolkit. It is not affiliated with, endorsed by, or supported by Tesla. It is not a Tesla product, and Tesla does not provide support for it. Tesla's name is used only where their hosts, their public-key path, or the `tesla-control` tool require it.

Use AgentDrive only on a vehicle you own, or a vehicle you are explicitly allowed to control, with your own account, for your own personal use. You bear all consequences of installing, configuring, and using this project. The author is not responsible for vehicle damage, injury, property loss, theft, fines, charges, account limits, data loss, or any other outcome.

To the maximum extent permitted by law, the software and the documentation are provided as is, without warranty of any kind, and the author's liability is excluded. Nothing in this disclaimer excludes, restricts, or modifies any consumer guarantee, right, or remedy under the Australian Consumer Law that cannot be excluded. Where the Australian Consumer Law allows liability for a failure to comply with a consumer guarantee to be limited, the author's liability is limited to the maximum extent permitted by that law.

A command that changes the vehicle is sent only when the shift state is Park and the speed is zero. After every command, AgentDrive reads the vehicle again and reports that observed state. Unlock, remote start, opening the front trunk, opening the rear trunk, turning sentry mode off, and any charging change each require a fresh explicit confirmation. That confirmation covers that single action and then expires. It is never reused, and a schedule cannot supply it or skip it. These checks also apply to trunk, frunk, and remote start when those commands are added.

Follow Tesla's current safety guidance, developer terms, and Fleet API terms. Where those terms and this project differ, the terms win. Obey the road rules that apply where you are, and do not interact with the AI agent while you are driving.

A private key together with an access token can operate the vehicle inside the scopes you approved. Keep both of them private. The author is not responsible if they leak, or if a command is sent in error.

Reported problems, safety issues, and suspected abuse are reviewed and fixed promptly. Ordinary reports go to GitHub Issues. Security reports go through GitHub private vulnerability reporting, as described in SECURITY.md.
