"""Read Windows' connected BLE devices, without a nearby-device watcher.

Uses the OS WinRT API through bundled Windows PowerShell; no Python WinRT
package is required by the existing Windows installer.
"""
from __future__ import annotations

import base64
import json
import re

from PySide6.QtCore import QObject, QProcess, Signal


# Only open handles for devices already selected as Connected by Windows.
# Cached GATT metadata distinguishes renamed BORING devices from other keyboards.
_CONNECTED_QUERY = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Devices.Enumeration.DeviceInformation,Windows.Devices.Enumeration,ContentType=WindowsRuntime]
$null = [Windows.Devices.Bluetooth.BluetoothLEDevice,Windows.Devices.Bluetooth,ContentType=WindowsRuntime]
$null = [Windows.Devices.Bluetooth.GenericAttributeProfile.GattDeviceServicesResult,Windows.Devices.Bluetooth,ContentType=WindowsRuntime]
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetGenericArguments().Count -eq 1 -and
    $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1
function Await($operation, $resultType) {
    $task = $asTask.MakeGenericMethod($resultType).Invoke($null, @($operation))
    $task.GetAwaiter().GetResult()
}
$selector = [Windows.Devices.Bluetooth.BluetoothLEDevice]::GetDeviceSelectorFromConnectionStatus(
    [Windows.Devices.Bluetooth.BluetoothConnectionStatus]::Connected)
$devices = Await ([Windows.Devices.Enumeration.DeviceInformation]::FindAllAsync($selector)) ([Windows.Devices.Enumeration.DeviceInformationCollection])
$rows = @()
$problems = @()
foreach ($info in $devices) {
    $device = $null
    $knownName = $info.Name -match '^(boring |mist |codex micro )'
    try {
        $device = Await ([Windows.Devices.Bluetooth.BluetoothLEDevice]::FromIdAsync($info.Id)) ([Windows.Devices.Bluetooth.BluetoothLEDevice])
        if ($null -eq $device) { throw 'Windows denied access to this device.' }
        if ($device.ConnectionStatus -ne [Windows.Devices.Bluetooth.BluetoothConnectionStatus]::Connected) { continue }
        $services = @()
        if (-not $knownName) {
            $result = Await ($device.GetGattServicesAsync([Windows.Devices.Bluetooth.BluetoothCacheMode]::Cached)) ([Windows.Devices.Bluetooth.GenericAttributeProfile.GattDeviceServicesResult])
            if ($result.Status -ne [Windows.Devices.Bluetooth.GenericAttributeProfile.GattCommunicationStatus]::Success) { continue }
            $services = @($result.Services | ForEach-Object { $_.Uuid.ToString(); $_.Dispose() })
        }
        if ($device.ConnectionStatus -ne [Windows.Devices.Bluetooth.BluetoothConnectionStatus]::Connected) { continue }
        $rows += @{ address = $device.BluetoothAddress.ToString('X12'); name = $device.Name; services = $services; connected = $true }
    } catch {
        if ($knownName) { $problems += $info.Name }
    } finally { if ($null -ne $device) { $device.Dispose() } }
}
ConvertTo-Json -InputObject @{ devices = @($rows); unavailable = @($problems) } -Compress -Depth 4
'''


def connected_boring_rows(payload: bytes) -> tuple[tuple[str, str], ...]:
    rows = json.loads(payload.decode("utf-8-sig"))["devices"]
    if not isinstance(rows, list):
        raise ValueError("Invalid connected-device response")
    devices: dict[str, str] = {}
    for row in rows:
        if row.get("connected") is not True:
            continue
        name = str(row.get("name") or "").strip()
        services = {str(value).lower() for value in row.get("services", [])}
        if ("7e2f0001-7a91-4a5b-9c2d-6e8f4b524731" not in services
                and not name.casefold().startswith(("boring ", "mist ", "codex micro "))):
            continue
        address = str(row.get("address", "")).lower()
        if not re.fullmatch(r"[0-9a-f]{12}", address):
            raise ValueError("Invalid Bluetooth address")
        devices[":".join(address[i:i+2] for i in range(0, 12, 2))] = name
    return tuple(devices.items())


class WindowsConnectedDeviceFinder(QObject):
    device_found = Signal(str, str)
    finished = Signal()
    failed = Signal(str)
    warning = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._process = QProcess(self)
        self._process.finished.connect(self._completed)
        self._process.errorOccurred.connect(self._error)
        self._finished = False

    def start(self) -> None:
        encoded = base64.b64encode(_CONNECTED_QUERY.encode("utf-16-le")).decode("ascii")
        self._process.start("powershell.exe", ["-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded])

    def cancel(self) -> None:
        self._finished = True
        if self._process.state() != QProcess.NotRunning:
            self._process.kill()

    def _error(self, _error: object) -> None:
        if not self._finished:
            self._finished = True
            self.failed.emit("无法读取 Windows 蓝牙连接状态，请在系统设置确认连接后重试，或改用 USB。")

    def _completed(self, exit_code: int, _exit_status: object) -> None:
        if self._finished:
            return
        if exit_code:
            self._error(None)
            return
        try:
            payload = bytes(self._process.readAllStandardOutput())
            rows = connected_boring_rows(payload)
            unavailable = json.loads(payload.decode("utf-8-sig")).get("unavailable", [])
        except (ValueError, TypeError, KeyError, AttributeError):
            self._error(None)
            return
        if unavailable:
            detail = "无法读取部分已连接 BORING 设备的信息：" + "、".join(map(str, unavailable))
            if not rows:
                self._finished = True
                self.failed.emit(detail)
                return
            self.warning.emit(detail)
        self._finished = True
        for identifier, name in rows:
            self.device_found.emit(identifier, name)
        self.finished.emit()
