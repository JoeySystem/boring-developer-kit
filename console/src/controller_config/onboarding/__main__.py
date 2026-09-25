"""Offline UI demo: python -m controller_config.onboarding [--english] [--reduced-motion]."""

import argparse
import sys
from PySide6.QtWidgets import QApplication
from .dialog import OnboardingDialog


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Offline Companion onboarding preview; no device connection"
    )
    parser.add_argument("--english", action="store_true")
    parser.add_argument("--reduced-motion", action="store_true")
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("BORING Companion Preview")
    dialog = OnboardingDialog(
        language="en_US" if args.english else "zh_CN", reduce_motion=args.reduced_motion
    )
    dialog.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
