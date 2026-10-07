class ProvisioningError(RuntimeError):
    """Base class for errors safe to show to an operator."""


class PackageError(ProvisioningError):
    """The selected release package is incomplete, invalid, or unsafe."""


class DeviceError(ProvisioningError):
    """The requested serial device cannot be used safely."""


class CompatibilityError(ProvisioningError):
    """The installed flash layout does not match the selected release."""


class FlashToolError(ProvisioningError):
    """esptool failed or produced an unexpected response."""


class VerificationError(ProvisioningError):
    """The flashed application did not pass runtime validation."""


class OperatorActionRequired(VerificationError):
    """The firmware was written, but the operator must power-cycle the board."""


class TransportError(VerificationError):
    """The serial transport was interrupted before a response completed."""
