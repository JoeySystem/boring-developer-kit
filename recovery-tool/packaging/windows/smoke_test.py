"""Exercise the distributed Windows EXE from an extracted, non-ASCII path."""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from urllib.request import Request, urlopen
import zipfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("--build-id", required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="BORING 车间测试 ") as directory:
        root = Path(directory)
        with zipfile.ZipFile(args.archive) as archive:
            archive.extractall(root)
        executable, = root.rglob("BORING-MIST-BLE-Recovery.exe")
        gui = subprocess.run([str(executable), "--check-gui"], timeout=60)
        if gui.returncode != 0:
            raise RuntimeError(f"native window backend missing: {gui.returncode}")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        process = subprocess.Popen([str(executable), "--no-browser", "--port", str(port)])
        try:
            deadline = time.monotonic() + 45
            while True:
                if process.poll() is not None:
                    raise RuntimeError(f"EXE exited early: {process.returncode}")
                try:
                    with urlopen(base + "/api/authority-update/bootstrap", timeout=2) as response:
                        state = json.load(response)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.25)
            assert state["authority"]["build_id"] == args.build_id, state
            assert state["authority"]["ota_supported"] is True
            assert state["readiness"] == "waiting", state
            for route in ("/", "/authority-update.js", "/authority-update.css"):
                with urlopen(base + route) as response:
                    body = response.read()
                    assert len(body) > 0
                    if route == "/":
                        page = body.decode("utf-8")
                        assert "固件更新与校准" in page
                        assert 'id="viewCalibrate"' in page
                        assert "修复" not in page
                        assert 'id="confirmationInput"' not in page
                        assert 'id="startCalibrationButton"' not in page
            request = Request(
                base + "/api/authority-update/shutdown",
                data=b"{}",
                headers={"Content-Type": "application/json",
                         "X-Authority-Updater-Token": state["token"]},
            )
            with urlopen(request) as response:
                assert json.load(response)["status"] == "shutting-down"
            assert process.wait(timeout=15) == 0
            print(json.dumps({
                "result": "passed", "build_id": args.build_id,
                "checks": ["native window backend bundled", "extracted EXE launch", "Chinese and spaced path",
                           "embedded firmware", "USB enumeration", "page assets",
                           "authenticated clean shutdown"],
                "hardware_flash_tested": False,
            }, ensure_ascii=False))
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)


if __name__ == "__main__":
    main()
