"""Native accessibility preferences shared by animated interface modules."""


def system_reduces_motion() -> bool:
    """Read the native macOS reduce-motion preference when available."""

    try:
        from AppKit import NSWorkspace

        return bool(
            NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceMotion()
        )
    except (AttributeError, ImportError):
        return False
