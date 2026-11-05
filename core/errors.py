"""Errors raised by AgentDrive. Messages must stay free of secrets."""


class AgentDriveError(Exception):
    """Base error for this toolkit."""


class ConfigError(AgentDriveError):
    """A configuration file is missing or invalid."""


class TokenError(AgentDriveError):
    """A token cannot be used or rotated."""


class ReauthenticationRequired(TokenError):
    """The refresh token is no longer usable. The owner must sign in again."""


class NotParked(AgentDriveError):
    """A state-changing command was refused because the vehicle is not parked."""


class ConfirmationRequired(AgentDriveError):
    """A sensitive command has no matching one-time confirmation."""


class ScheduleBlocked(AgentDriveError):
    """A schedule tried to run or confirm a sensitive command."""


class VerificationFailed(AgentDriveError):
    """The vehicle state after a command did not match the request."""


class CommandFailed(AgentDriveError):
    """The signed-command tool returned a failure. The command was not retried."""


class EndpointNotAllowed(AgentDriveError):
    """The route is outside the personal-account set."""


class MemoryRejected(AgentDriveError):
    """A value must not be stored in assistant memory."""


class InstallBlocked(AgentDriveError):
    """Installation is not allowed yet."""


class AmbiguousAdapter(AgentDriveError):
    """More than one host bot matched. The user has to choose."""


class ProfileError(AgentDriveError):
    """An owner profile contains a field this toolkit will not store."""
