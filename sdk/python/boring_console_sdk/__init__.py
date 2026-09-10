"""Python SDK for BORING Console local extensions."""

from boring_console_sdk.client import (
    API_MAJOR,
    API_MINOR,
    BoringConsoleClient,
    BoringConsoleError,
    ConnectionError,
    RequestError,
)

__all__ = [
    "API_MAJOR",
    "API_MINOR",
    "BoringConsoleClient",
    "BoringConsoleError",
    "ConnectionError",
    "RequestError",
]

