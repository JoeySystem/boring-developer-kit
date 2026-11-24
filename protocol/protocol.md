# Wired Macro Pad shared protocol v1

Status: frozen for development prototypes

Contract version: `1.0.0`  
Wire protocol: `1.0`  
Configuration schema: `1`

This directory is the executable shared contract between firmware and desktop
software. Internal design plans explain intent; this file and its fixtures lock the
wire values used by both implementations.

## USB identity

| Field | Prototype compatibility value | Rule |
|---|---|---|
| USB mode | composite Full Speed device | One configuration only |
| VID | `0x303A` | Prototype compatibility identity |
| PID | `0x8360` | Single runtime PID used by firmware and configurator |
| `bcdUSB` | `0x0200` | USB 2.0 Full Speed |
| `bcdDevice` | `0x0100` | Product hardware contract 1.0 |
| Manufacturer | `Custom Peripheral Project` | UTF-16 USB string |
| Product | `Wired Macro Pad V1 (Engineering)` | Development descriptor label |
| Serial | `CP01-` + 12 uppercase hex digits | Derived from the ESP32-S3 factory MAC; stable across ports and reboots |
| Product ID | `wired-macro-pad-v1` | Protocol identity |
| Hardware ID | `WMP-S3-REV-A`, `WMP-S3-MATRIX12-V1`, or `WMP-S3-MATRIX12-POWER-V2` | Must match the selected physical-board target and configuration |

`0x303A:0x8360` is locked as the development runtime identity. Firmware and the
desktop configurator must use this pair directly. The configurator must not
require a debug PID override. Host discovery still
confirms `product_id`, `hardware_id`, and `serial` with `HELLO` before treating
a port as this device. The ESP32-S3 ROM download identity remains separate and
is not part of this runtime contract.

The ESP32-S3 native USB pins are GPIO19 D- and GPIO20 D+. USB-OTG owns the
internal PHY while the runtime firmware is mounted; runtime firmware must not
assume simultaneous USB-Serial-JTAG use on the same PHY.

## Interfaces and endpoint budget

One configuration exposes three interfaces:

| Interface | Class | Endpoints | Max packet / interval |
|---:|---|---|---|
| 0 | CDC ACM control | EP1 IN notification | 8 bytes / 16 ms |
| 1 | CDC ACM data | EP2 OUT + EP2 IN bulk | 64 bytes |
| 2 | HID | EP3 IN interrupt | 16 bytes / 1 ms |

The device consumes three endpoint numbers: one IN-only and two bidirectional
or IN endpoints. EP0 uses 64-byte control packets. This is within ESP32-S3's
documented device limit of five IN/OUT endpoints plus one additional IN
endpoint. CDC is protocol-only: logs and human-readable console output are
forbidden on it in production builds.

The single HID interface uses report IDs:

| Report ID | Report |
|---:|---|
| 1 | Boot-style 8-byte keyboard input report |
| 2 | 16-bit consumer-control input report |
| 3 | 5-button, X/Y, vertical-wheel, horizontal-pan mouse input report |

V1 has no HID OUT endpoint and ignores keyboard LED output reports.

## BLE configuration transport

Matrix12 Power V2 firmware may expose the same WMP1 byte stream over an
encrypted BLE GATT connection. This is a transport alternative to USB CDC,
not a second protocol or a device-identity signal:

| Attribute | UUID | Contract |
|---|---|---|
| Service | `7e2f0001-7a91-4a5b-9c2d-6e8f4b524731` | BORING configuration service |
| RX | `7e2f0002-7a91-4a5b-9c2d-6e8f4b524731` | Write With Response; encrypted |
| TX | `7e2f0003-7a91-4a5b-9c2d-6e8f4b524731` | Indicate; encrypted CCC |

RX and TX carry raw WMP1 bytes fragmented to `ATT_MTU - 3`. A client enables
TX indications before sending `HELLO`; the device accepts RX only while that
subscription is active. Split and coalesced frames have the same semantics as
USB CDC. One transport owns the WMP parser at a time. Disabling indications or
disconnecting BLE clears its session and returns ownership to USB CDC.

Discovery name and service UUID are hints only. A production client must still
complete `HELLO`, `CAPABILITIES`, certificate retrieval and a fresh
`AUTH_CHALLENGE` before enabling configuration. USB remains the preferred
transport when both are present. `features.ble_configuration=true` declares
this service; absence means the device requires USB for configuration.

## Binary frame

All integers are unsigned little-endian. The header is exactly 20 bytes:

| Offset | Size | Field | V1 value / meaning |
|---:|---:|---|---|
| 0 | 4 | magic | ASCII `WMP1` (`57 4D 50 31`) |
| 4 | 1 | protocol major | `1` |
| 5 | 1 | protocol minor | `0` |
| 6 | 1 | message type | Command or `ACK`/`NACK` number below |
| 7 | 1 | flags | Bit 0 response, bit 1 error; other bits must be zero |
| 8 | 4 | request ID | Nonzero request correlation ID |
| 12 | 4 | payload length | `0..20480` bytes |
| 16 | 4 | payload CRC32 | IEEE CRC-32 of payload bytes; empty payload is `00000000` |

Payload is one UTF-8 JSON object. JSON must not contain NaN, Infinity, or
fractional values where the schema requires integers. Config digests use RFC
8785 JCS bytes and lowercase SHA-256 hex. CRC covers payload only, not header.

The stream parser must accept split and coalesced frames. On invalid magic it
scans forward to the next `WMP1`. It rejects reserved flag bits, request ID 0,
oversized payloads, invalid UTF-8/JSON, and CRC mismatch.

Device-side resynchronisation. Because the CRC does not cover the header, the
device never trusts the length of a frame whose CRC fails: it replies
`CRC_MISMATCH` for a nonzero request ID, steps one byte past that magic and
scans for the next `WMP1`, so a following valid request is still answered. A
frame whose CRC passes but whose header breaks a rule (reserved flag bits,
request ID 0, unsupported version) is dropped whole; request ID 0 is never
answered. An oversized length is answered `PAYLOAD_TOO_LARGE` and only the
magic is skipped. An incomplete frame is dropped one byte at a time, with the
same resync, once no byte has arrived for 500 ms on USB or 2 s on BLE, so a
corrupted length cannot stall the channel. If the receive buffer ever fills
without a complete frame, the oldest byte is dropped and the parser resyncs;
the session is kept.

`GET_STATUS.result.protocol_rx` reports parser counters since boot:
`bad_frames` (CRC, header or length rejections), `partial_timeouts`,
`resyncs` (times bytes were skipped to find the next magic) and `overflows`.
Older firmware omits this field. Within the device's
bounded replay window, a duplicate request ID with the same message type and
identical payload returns the exact cached response. Reusing a cached request
ID with different content returns `DUPLICATE_REQUEST_MISMATCH`. Engineering
Alpha retains up to four recent responses within a 24 KiB total response budget
and evicts the least-recently-used entry first.

### Client response parsing and resync

A host client treats one corrupt response frame as a recoverable link event,
not a lost connection. On CRC mismatch, oversized length, reserved flag bits or
request ID 0 it discards that frame, advances one byte past its magic (the
length field of a corrupt frame is not trusted) and scans for the next `WMP1`,
keeping any good frames that follow. A discarded response for the in-flight
request is handled as that request's timeout, so the command's normal retry
rule applies. The client closes the link only when discards cluster (five
inside ten seconds) or when a CRC-valid frame carries an incompatible major.

### Version compatibility

`protocol major` changes are breaking; a client and device must share the same
major. Within one major, any `protocol minor` is accepted in either direction:

- A minor bump may only add backward-compatible extensions: new optional
  response fields, new commands, new `CAPABILITIES` features. It must not
  change or remove existing fields or semantics.
- Clients ignore unknown response fields and decide whether a feature is
  available from `CAPABILITIES`, never from the minor number.
- Clients send requests with their own minor. Devices must accept requests
  whose minor is lower than or equal to their own (see `HELLO` below).
- Unknown fields inside `GET_CONFIG.result.config` remain governed by
  `config-schema.json` and `schema_version`, not by the protocol minor.

## Message types

Requests use flags `0x00`. Every valid request produces either `ACK` with flags
`0x01` or `NACK` with flags `0x03` and the same request ID.

| Value | Name | Request payload purpose |
|---:|---|---|
| `0x01` | `HELLO` | Client identity and supported protocol range |
| `0x02` | `CAPABILITIES` | Empty object; returns device limits/features |
| `0x03` | `PING` | Echoes caller `nonce` and device uptime |
| `0x04` | `AUTH_GET_CERTIFICATE` | Empty object; returns the public device certificate and issuer signature |
| `0x05` | `AUTH_CHALLENGE` | Signs one fresh 32-byte host nonce using the provisioned device identity |
| `0x10` | `GET_CONFIG` | Empty object; returns active config, generation and digest |
| `0x11` | `VALIDATE_CONFIG` | Candidate config without persistence |
| `0x12` | `SET_CONFIG` | Full config plus `base_generation` |
| `0x13` | `GET_STATUS` | Runtime and pending-activation status |
| `0x14` | `SET_PLATFORM` | Select `macos` or `windows_linux` and its built-in Profile |
| `0x15` | `BLE_SLOT_SELECT` | Select BLE host slot `1..3`; an empty slot enters pairing |
| `0x16` | `BLE_SLOT_CLEAR` | Forget one BLE host slot and immediately enter pairing on it |
| `0x17` | `GET_PROMPT_LIST` | Return occupied prompt IDs, names, and body byte counts |
| `0x18` | `GET_PROMPT` | Return one stored UTF-8 prompt |
| `0x19` | `SET_PROMPT` | Create or replace one stored UTF-8 prompt |
| `0x1A` | `DELETE_PROMPT` | Delete one stored prompt |
| `0x1B` | `GET_PROMPT_EVENT` | Poll for the next physical prompt-trigger event |
| `0x1C` | `BLE_NAME_GET` | Read saved, active and default Bluetooth names |
| `0x1D` | `BLE_NAME_SET` | Persist a Bluetooth name for the next normal reboot |
| `0x1E` | `GET_HOST_ACTION_EVENT` | USB-only poll for the next physical computer-task trigger |
| `0x20` | `CALIBRATION_START` | Axis/session parameters |
| `0x21` | `CALIBRATION_SAMPLE` | Request current bounded sample window |
| `0x22` | `CALIBRATION_CONFIRM` | Candidate calibration for normal config transaction |
| `0x23` | `CALIBRATION_CANCEL` | Cancel session without changing old calibration |
| `0x24` | `DIAGNOSTIC_START` | Start or renew exclusive physical-input capture |
| `0x25` | `DIAGNOSTIC_STOP` | Stop capture and wait for all controls to return neutral |
| `0x26` | `SET_LIGHTING_PREVIEW` | Replace the volatile lighting preview snapshot and refresh its lease |
| `0x27` | `CLEAR_LIGHTING_PREVIEW` | Clear volatile preview and restore active configuration lighting |
| `0x28` | `SET_AGENT_STATUS` | Replace six source-scoped Claude Code or Doubao status slots in RAM and renew a 5-second lease |
| `0x29` | `CLEAR_AGENT_STATUS` | Clear the named Agent status source, idempotently |
| `0x2A` | `NORMAL_AGENT_BEHAVIOR_GET` | Read the device-wide NORMAL Agent-key choice |
| `0x2B` | `NORMAL_AGENT_BEHAVIOR_SET` | Persist and apply the device-wide NORMAL Agent-key choice |
| `0x30` | `FACTORY_DEFAULT` | Current `base_generation` and confirmation token |
| `0x40` | `FW_BEGIN` | Declare image identity, version, size and SHA-256 |
| `0x41` | `FW_DATA` | Sequential base64 image chunk and byte offset |
| `0x42` | `FW_STATUS` | Empty object; returns update and rollback state |
| `0x43` | `FW_END` | Verify the received image and schedule reboot |
| `0x44` | `FW_ABORT` | Discard an in-progress receive transaction |
| `0x45` | `FW_CONFIRM` | Confirm the running image after host verification; cancels rollback |
| `0x50` | `SCREEN_ICON_GET` | Read committed home-icon metadata |
| `0x51` | `SCREEN_ICON_READ` | Read a pixel chunk bound to one resource revision |
| `0x52` | `SCREEN_ICON_BEGIN` | Begin a connection-bound image upload |
| `0x53` | `SCREEN_ICON_DATA` | Receive a sequential base64 pixel chunk |
| `0x54` | `SCREEN_ICON_COMMIT` | Persist and select the complete uploaded image |
| `0x55` | `SCREEN_ICON_ABORT` | Cancel an uncommitted upload on this connection |
| `0x56` | `SCREEN_ICON_RESET` | Select the default icon without resetting other settings |
| `0x57` | `SCREEN_GLYPH_LIST` | List independently customizable point-grid icons |
| `0x58` | `SCREEN_GLYPH_GET` | Read one effective icon, including default pixels |
| `0x59` | `SCREEN_GLYPH_SET` | Persist one icon without changing other icons |
| `0x5A` | `SCREEN_GLYPH_RESET` | Restore one icon to its built-in resource |
| `0x7E` | `ACK` | Successful response |
| `0x7F` | `NACK` | Failed response |

`CAPABILITIES.features.ble_host_slots` advertises the number of available BLE
host slots. When it is nonzero, `GET_STATUS.result.codex_micro` includes
`active_slot` and a three-entry `slots` array. Each entry contains `slot`,
`paired`, and `connected`. A configurator must not expose slot commands when
the capability is absent or zero.

`GET_STATUS.result.codex_micro.init_error` is the ESP-IDF error code returned
by the synchronous Codex transport initialization (`0` means success).
`ble_services_ready` reports whether the BLE HID service has completed startup;
it does not mean a computer is connected. These optional diagnostic fields do
not change pairing or input ownership. USB Vendor HID can remain available
after BLE initialization fails, provided its common transport locks exist.

`GET_STATUS.result.codex_micro.ble_recovery` reports BLE start-up recovery for
this boot. A failed BLE controller init is retried in place up to twice,
100 ms apart (a failed init powers the BT domain down). `result` is `none`
(first attempt succeeded), `recovered_in_place`, `failed` (retries exhausted;
BLE unavailable, USB unaffected) or `skipped_after_crash_loop`: the two
previous boots were reset by a watchdog or panic while BLE was starting, so
this boot leaves BLE off. BLE counts as starting from controller init until
the HID services are ready or start-up fails cleanly, which includes a
controller-task assert after init has returned; a boot that finishes BLE
start-up, or a power-on reset, clears the count. `attempts_in_place` is the number of retries,
`last_error` the last failing ESP-IDF error code, `reset_reason` this boot's
`esp_reset_reason()`, and `crash_loop_count` the guard count (0..2).

The optional read-only BLE runtime diagnostics under
`GET_STATUS.result.codex_micro` are held in RAM and reset at boot. `ble_slots`
contains three entries with `slot`, `paired`, `address_kind`, and
`identity_known`; `address_kind` is one of `none`, `public`, `random_static`,
`rpa`, or `random_other`, and no full Bluetooth address is returned. Firmware
that gives each newly paired host an address of its own adds `own_address` to
every entry: `true` when the device shows that slot's host a static random
address stored with the slot, `false` for a slot paired by older firmware on
the shared public address and for an empty slot. A host on the public address
can still reach the device while another such host is selected and is then
dropped; removing the device on that host and adding it again through an
empty slot moves it to an address of its own. The device's own addresses are
not returned in full either. Firmware from 2026-11-13 adds `own_address_tail`
to an entry whose `own_address` is `true`: the last two bytes of that address
as `"xx:xx"`, the end of the address the computer shows for the device, so the
two can be compared. The same firmware adds `ble_own_address`: `asked` is the
address the firmware last asked the stack to advertise on and `in_use` the one
the stack reports it is on, each `"public"` or a tail (`in_use` may be
`"unknown"`); `set_rejections` counts requests for a new address the stack
refused, `apply_failures` advertising starts found on another address than the
one asked for (advertising is then restarted), and `adopt_mismatches` hosts
recorded with another address than the one asked for. The three counters are
boot-local; any value above zero means the stack did not take an address when
asked, and a host is always recorded with the address that was in use.
`ble_rejects` reports the boot-local rejection `count`, `last_reason`
(`none`, `unknown_host`, `other_slot`, or `suspended`), and `last_slot`.
`ble_last_disconnect` reports the last HCI `reason` and `source` (`none`,
`standby`, `usb`, `slot_switch`, `reject`, or `stack`). `ble_last_slot_op`
reports the last `op` (`none`, `clear`, or `pair_save`), `slot`, numeric
`error`, whether it completed through the four-second clear `timeout` path,
and the boot-local `timeout_count`. Firmware with the pairing window adds
`select` and `auto_return` to `op`, and three more objects.
`ble_pairing_window` has `open`, the empty `slot` that waits for a new host,
the `return_slot` the device goes back to when the window ends without a
pairing (0 for none), the boot-local count of such `returns`, and
`active_persist_failures`. `ble_connect_time` has `last_ms` and `max_ms`: the
time from the start of advertising to the connection of a host that was
accepted. `ble_reject_reasons` splits the rejection count into `unknown_host`,
`other_slot`, and `suspended`. These fields never select a slot, accept a
peer, clear a bond, or write NVS.

An ACK payload is:

```json
{"command":"PING","result":{"nonce":"sample","uptime_ms":1234}}
```

A NACK payload is:

```json
{"command":"SET_CONFIG","error":{"code":10,"name":"GENERATION_CONFLICT","message":"base_generation does not match","details":{"current_generation":8}}}
```

## Stable error codes

| Code | Name | Meaning |
|---:|---|---|
| 1 | `INVALID_FRAME` | Header, flags, request ID, or framing is invalid |
| 2 | `CRC_MISMATCH` | Payload CRC is wrong |
| 3 | `PAYLOAD_TOO_LARGE` | Payload exceeds 20480 bytes |
| 4 | `INVALID_JSON` | Payload is not one valid UTF-8 JSON object |
| 5 | `UNKNOWN_COMMAND` | Message type is unknown |
| 6 | `UNSUPPORTED_PROTOCOL` | Protocol major differs, or the request minor is newer than the device |
| 7 | `UNSUPPORTED_SCHEMA` | Configuration schema is unsupported |
| 8 | `HARDWARE_MISMATCH` | Product or hardware ID does not match |
| 9 | `VALIDATION_FAILED` | Candidate violates schema or device capabilities |
| 10 | `GENERATION_CONFLICT` | `base_generation` is stale |
| 11 | `BUSY` | A mutually exclusive device operation is active |
| 12 | `STORAGE_FAILURE` | Persistent storage is unavailable or verification failed |
| 13 | `NOT_FOUND` | Requested object or calibration session does not exist |
| 14 | `TIMEOUT` | Bounded device-side operation timed out |
| 15 | `INTERNAL` | Non-recoverable internal error without a safer code |
| 16 | `READ_ONLY` | Device is running in read-only degraded mode |
| 17 | `DUPLICATE_REQUEST_MISMATCH` | Reused request ID has different request content |
| 18 | `DEVICE_IDENTITY_UNAVAILABLE` | Provisioned public identity or DS key material is missing or invalid |
| 19 | `DEVICE_SIGNING_FAILED` | The provisioned DS provider could not sign the challenge digest |
| 20 | `AUTHENTICATION_REQUIRED` | The command needs a session that completed HELLO and device authentication |

## Handshake

`HELLO` request fields are `client.name`, `client.version`,
`protocol.min_major`, `protocol.max_major`, and `protocol.max_minor`. The ACK
fixture locks the response shape. It includes USB/protocol identity, product
firmware version, exact `build_id`, schema, generation, active digest, and
compatibility (`read`, `write`, reason). Clients use `build_id`, not the product
version alone, to confirm that a requested development package is running.

`compatibility.reason` is `compatible`, `devkit_read_only` (`write=false`),
`safe_mode`, or `config_storage_unavailable`. `safe_mode` (stability item 6)
means the boot guard started this boot in safe mode: the stored config was not
read, the built-in defaults run read-only, BLE is off, and config writes fail
as below; firmware maintenance (`FW_*`) works. Restarting tries a normal boot.
The one config write safe mode accepts is an explicit `FACTORY_DEFAULT`
(`confirmation` `FACTORY_DEFAULT`; `base_generation` is required but not
compared): it removes the stored config (both slots, markers, OS choice and
per-connection OS preferences, BLE name, agent key behavior). Unlike the
normal `FACTORY_DEFAULT`, it keeps prompt slots and BLE pairings, and device
state such as power and timers. It ACKs
`{"command":"FACTORY_DEFAULT","result":{"state":"RESTARTING","safe_mode":true}}`
and restarts after 1.5 s; the next normal boot starts as a new device. An
unusable config partition is cleared by this request only. On the device,
holding Key 1 + Key 12 for 3 s in safe mode shows `DOWNLOAD / RELEASE / TO
START` and enters the ROM download mode on release.
`config_storage_unavailable` means the config partition could not be
used at boot on a confirmed image: the device runs read-only on the config it
read, or on the built-in defaults, until restart. It keeps `write=true` so
clients that gate firmware maintenance on `write` can still update it, but
`SET_CONFIG`, `FACTORY_DEFAULT` and other config writes fail with
`STORAGE_FAILURE`. A fresh, unconfirmed update in the same state fails its boot
instead and rolls back.

Since review A2 (2026-11-10) no automatic path writes over a stored config:
a slot that exists but cannot be used (damaged, other schema version, failed
digest) is left untouched; only a device with no stored slot at all gets the
built-in defaults written. If the marked active slot is fine and only the
other slot is unusable, the device runs normally and writes nothing at boot.
If the active config cannot be used, the device runs read-only on the best
readable slot or the defaults (`config_storage_unavailable` above). An NVS
partition that cannot be opened (no free pages, newer NVS format) is never
erased automatically any more, neither `config_nvs` nor the BLE bond
partition `nvs`; a fresh update rolls back, a confirmed image runs without it
(config read-only, BLE off) and reports it. `GET_STATUS.result.storage_recovery`
reports `ble_storage_unusable`, `config_storage_unusable`,
`config_storage_degraded`, `config_storage_error` (ESP-IDF error name, or
`none`), `config_using_defaults`, `stored_config_unusable` (a stored slot
exists but cannot be used and was left untouched) and
`unreadable_config_replaced` (always false now; kept for older clients).

Restart timing (F-05). Measured on Power V2 (`custom-candidate-20261030.04`,
CP01-E072A1D586EC, macOS host, 10 device-menu restarts, 2026-10-30): from the
USB CDC port disappearing to the first `HELLO` ACK took 3.02 s on average and
3.14 s at most. Almost all of it is the restart and USB re-enumeration (port
back after 2.98 s on average); `HELLO` was answered 19–76 ms after the port
reappeared, with no unanswered `HELLO` and no `BUSY` NACK. Clients should allow
at least 5 s after a restart before treating the device as missing. Matrix12
V1 has not been measured.

Version compatibility. Within one major, a minor only adds backward-compatible
extensions, so the device minor may be higher than the client's. The device
accepts request frames with its own major and any minor up to its own, and
answers `UNSUPPORTED_PROTOCOL` for a different major or a newer minor. `HELLO`
succeeds whenever the client's `min_major`..`max_major` range includes the
device major, even if `max_minor` is below the device minor; the response
still reports the device's own `protocol_minor`, and response frames carry the
device minor. Clients decide feature availability from `CAPABILITIES`, and any
field a newer minor adds is optional.

`CAPABILITIES` returns limits rather than relying on UI constants. All three
product targets lock 8 profiles, 256 bytes per macro, 4096 bytes of all macros,
and a 16384-byte canonical configuration object. Rev A advertises 15 logical
controls: `key.1` through `key.7`, `encoder.ccw`, `encoder.cw`,
`encoder.press`, `joystick.up`, `joystick.down`, `joystick.left`,
`joystick.right`, and `joystick.press`. Matrix12 V1 and Matrix12 Power V2
advertise the same controls plus `key.8` through `key.12`, for 20 controls
total. Clients must use the returned control list and RGB counts to render the
physical controls rather than assume one board layout.
Both Matrix12 targets report `status_rgb_count=0` because they have no separate
status strip, plus `agent_status_under_key_count=6` to declare that their first
six key lights are temporarily owned by the six Agent task states while connected.

The v1 configuration object retains exactly eight `lighting.status` entries as
compatibility storage, including on Matrix12 targets that report no physical
status strip; those entries are ignored on that hardware. `lighting.under_key`
must match the target capability exactly: 7 entries on Rev A and 12 entries on
both Matrix12 targets. The schema conditions these lengths on `hardware_id`.

`result.features` is an additive capability map within protocol 1.0. Clients
must ignore unknown feature keys and treat a missing key as unsupported/false;
adding a feature key therefore does not change the contract version.
`features.haptic_channels=true` declares independent vibration switches in
the existing configuration `haptic` object: `on_press` (keys, including encoder
and joystick presses and local save feedback), `on_encoder` (rotation),
`on_joystick` (direction/movement), and `on_task` (Codex/CC reminders and the
local Pomodoro alarm). The three new fields are optional booleans, defaulting
to `true` when absent; `on_press` retains its saved value. `enabled` is the
master switch. Channel settings apply in all operating modes and local menus.
`on_profile` is retained; profile-activation feedback requires it AND
`on_press`. Explicit on-device motor-strength audition is independent of the
channel switches and auditions the candidate level, including when the saved
master is off; level zero stops the motor. Ordinary output never bypasses the
master. Disabling `on_task` cancels an active reminder and consumes new task
events silently; enabling it never replays those events. Lights and task state
remain unaffected. Use the existing full `GET_CONFIG` / `SET_CONFIG` flow and
activation readback; there is no new message type, schema-version bump, or
RAM-only preview command. Clients must retain these fields when changing
other settings, and must not send them to firmware lacking this capability.

`features.quick_config` is true only when the target provides the on-device
menu for Profile selection, universal key presets, RGB brightness, haptic
strength and exit. All three product targets advertise true and the
development-board target advertises false. The flag does not imply arbitrary
on-device macro or modifier editing.
`features.lighting_preview=true` declares the volatile host preview commands
`SET_LIGHTING_PREVIEW` and `CLEAR_LIGHTING_PREVIEW`. A missing or false value
means that a client must keep lighting edits local until the normal confirmed
configuration transaction completes.
`features.ble_configuration=true` declares the encrypted BLE GATT transport
documented above. It does not replace device authentication, imply that an
unpaired device is trusted, or change the meaning of any WMP command.
`features.platform_selection` indicates that first-run platform selection,
`GET_STATUS.result.platform`, and `SET_PLATFORM` are available. A client must
not send `SET_PLATFORM` when the feature is absent or false.
`features.eda_mode` is true when the target provides the fixed JLCEDA Pro
operating mode. `features.claude_code_mode` was true on Matrix12 targets that
provided the fixed, platform-aware Claude Code shortcut layer. **Since the
firmware that follows 20261113.01 it is always false** (design decision, step one):
Claude Code is no longer part of the double-click mode cycle, which on Matrix12
switches Normal and Codex only, and Console 0.2.x greys its Claude Code tab out
on false. Claude is shown through the shared desktop status source
(`desktop_agent_status`, Normal mode only). The mode code itself stays until
step two, so a client must still tolerate `operating_mode` `claude_code` from
older firmware.
`features.claude_code_status` is a separate capability: true on Matrix12
Codex USB targets supporting the USB CDC Claude Code status bridge below.
Do not infer it from `claude_code_mode`. Old firmware without this capability
must not receive the new status commands. It stays true in step one so a
Console that still sends `claude_code` status keeps working.
`GET_STATUS.result.operating_mode` reports `normal`, `codex`, `eda`, or
`claude_code` on every build, including targets compiled without Codex
transport. Current Matrix12 firmware never enters `claude_code` through the
mode cycle, and a menu restart that remembered it returns to `normal`.

### NORMAL Agent-key behavior (Matrix12 Power V2)

`CAPABILITIES.result.features.normal_agent_key_behavior=true` enables
`NORMAL_AGENT_BEHAVIOR_GET` (0x2A) and `NORMAL_AGENT_BEHAVIOR_SET` (0x2B) on
targets with all six official Agent status keys. Missing/false means the client
must not send either command; `features.codex_agent_focus` alone does not imply
this preference is available. Both commands use the existing authenticated
configuration connection, ownership rules, ACK/NACK envelope and request-ID
replay behavior. They do not change the protocol or configuration schema version.

GET takes `{}` and returns `{"behavior":null}` when no choice has ever been
saved, or one of the two saved choices. SET takes exactly one field:

```json
{"behavior":"open_conversation"}
```

Only `open_conversation` and `status_only` are writable. A successful SET
returns `{"behavior":"open_conversation"}` or `{"behavior":"status_only"}`
after the independent device-level NVS preference is durably saved and the
runtime behavior has changed. SET does not require reboot. Invalid values,
including `null`, wrong types, missing or extra fields, return
`VALIDATION_FAILED` (9). Held controls or conflicting maintenance return
`BUSY` (11), leaving the previous behavior unchanged. A read/write failure
returns `STORAGE_FAILURE` (12), not an apparent unset choice or success; a
failed write retains the previous valid behavior. If an ACK is lost, GET
reveals whether SET persisted before the client decides to retry.

The preference is shared by physical Key1, Key2, Key4, Key5, Key6 and Key7
(Agent indexes 0–5), independent of profiles and ordinary `SET_CONFIG` or
configuration import. Factory Default restores the unset state. Unset preserves
the prior NORMAL Agent Vendor-event output but does **not** emit a foreground
notification. With explicit `open_conversation`, a fresh physical down edge
sends the official Agent event to that slot's currently displayed, connected
status source; release uses the same transport. No valid source means no
fallback to another host and no successful-press notification. Explicit
`status_only` consumes those keys without Vendor, keyboard, Consumer, Mouse,
macro, prompt or historical mapping output. Both choices leave status-light
updates intact. Existing local-menu, diagnostic and power-combination input
ownership takes precedence; CODEX and Claude Code mode behavior is unchanged.

The independent preference is not included in the portable full-config export.
Clients should confirm SET with GET and should not present an unset choice as
either of the two user-selectable behaviors. An official event sent to a host
does not prove the client selected a conversation.

### Codex Agent press foreground notification

`features.codex_agent_focus=true` advertises a fresh physical Agent-press
notification in `GET_STATUS.result.codex_agent_press`. It is independent of
Claude Code status and adds no WMP command or persistent configuration field.

```json
{"sequence": 12, "agent": 2, "transport": "usb"}
```

`sequence` is a RAM-only uint32 counter, initially zero. Each accepted physical
Agent down edge in CODEX mode, or in NORMAL mode with an explicit
`open_conversation` choice, advances it (skip zero on wrap), only after the
original `v.oai.hid` send succeeds. `agent` is zero-based 0–5, corresponding to
physical Key1, Key2, Key4, Key5, Key6, Key7. `transport` is `usb` or `ble`, the
transport used for the original Vendor event. Original task selection is unchanged.
Release/repeat events, NORMAL mode with an unset or `status_only` choice,
Claude Code mode, diagnostic capture and local input ownership never create
foreground notifications.

The latest notification replaces the previous one: all six ask the helper to
foreground the same desktop application, not to select a task itself. It expires
after 2000 ms; suppression, mode exit and disconnect invalidate it. An expired,
invalidated or other-transport notification returns `agent:null,transport:null`
while retaining its sequence. Before any press all fields are zero/null.
Status reads are non-destructive and cannot increment the counter. GET_STATUS
exposes an active Agent/transport only to the configuration connection on the
same transport as the original Vendor event. Changing the NORMAL behavior,
mode exit, input capture, output disconnect and shutdown invalidate any old
notification; reconnect cannot replay it.

The Console must use an authenticated connection, baseline the counter from
bootstrap/readback without activation, and act at most once per new sequence.
It must match the transport and either current CODEX mode or NORMAL mode with
explicit `open_conversation`, and discard notifications
during maintenance or input capture. Reconnection creates a new baseline, never
replays an old press. Missing capability/field means this convenience is unavailable,
not that existing Agent controls are disabled. The macOS helper foregrounds
ChatGPT only in response to a press; completion/status changes alone do not steal
focus. This is not permanent window pinning and does not imply host task IDs are
available through WMP.

### Claude Code status bridge (USB CDC)

`SET_AGENT_STATUS` (0x28) accepts exactly this complete snapshot shape:

```json
{"source":"claude_code","states":["working","idle","idle","idle","idle","idle"]}
```

`source` must be `claude_code`; `states` must contain exactly six strings, each
one of `idle`, `working`, `completed`, `approval`, `reply`, `error`. No extra
fields are accepted. Array indices 0–5 correspond to Agent keys
`key.1`, `key.2`, `key.4`, `key.5`, `key.6`, `key.7`, respectively.
The host owns stable session-to-slot assignment. For a single session use slot
0 and leave the rest `idle`. Reusing a slot for a different session requires
an `idle` snapshot for that slot before sending the new session state.

ACK payload:

```json
{"command":"SET_AGENT_STATUS","result":{"source":"claude_code","active":true,"lease_ms":5000}}
```

`CLEAR_AGENT_STATUS` (0x29) accepts exactly `{"source":"claude_code"}`.
It is idempotent; ACK has the same result shape with `active:false` and command
`CLEAR_AGENT_STATUS`. Unsupported targets return `READ_ONLY`; malformed
snapshots, unknown states, and unsupported sources return `VALIDATION_FAILED`.
The capability-gated `desktop` source has its own contract below.
Rejected requests do not change state or renew the lease. These commands do
not write configuration, generation, prompts, NVS, identity, or eFuse.

Send a full snapshot immediately on a change and every 2 seconds while the
bridge is healthy. Every update/heartbeat uses a fresh WMP request ID;
replaying an already-cached request only replays its ACK and does not renew
the lease. `GET_STATUS` and `PING` also do not renew it. After 5000 ms without
an accepted SET, USB detach, protocol session reset, explicit CLEAR, or reboot,
the source becomes inactive and all six states clear to `idle`. Closing a
client is not itself a USB detach: send CLEAR on orderly exit; on a crash the
lease bounds stale output to 5 seconds. BLE is not a CC status delivery path
in this version. Existing Codex Vendor HID USB/BLE reports remain unchanged.

`GET_STATUS.result.claude_code_status` is a read-only RAM snapshot:

```json
{"active":true,"selected":true,"lease_ms":5000,"states":["working","idle","idle","idle","idle","idle"]}
```

`active` means a live CC bridge lease, not that a task is executing or that
hardware is currently lit. `selected` means CC operating mode is selected.
All-idle with `active:true` is valid (healthy bridge with no active sessions).
CC mode renders only CC status; no live CC source means six Agent lights off,
never Codex fallback. NORMAL/CODEX preserve existing Codex status behavior
unless NORMAL is owned by the Doubao bridge below;
EDA preserves existing behavior. Nonselected sources may update stored state
but do not generate reminders, and selecting a source never replays old ones.

First snapshots after reset/expiry/CLEAR show state silently. Later transitions
to `completed` request one pulse; `approval`/`reply` two; `error` three.
Unchanged snapshots are silent. `idle`/`working` are silent and allow later
transitions to alert again. Concurrent reasons reuse the existing highest
priority coalescing (error > action required > completed), 250 ms pulses,
300 ms gaps on Power V2, and current user motor strength. Legacy Matrix12 V1
retains its 200 ms board pulse clamp (the scheduler still uses a 550 ms cycle);
this handoff's physical timing acceptance target is Power V2, not legacy V1.
Switching between CC and other
modes cancels the old source's running/pending reminder; CC expiry/CLEAR does
the same. Existing power, diagnostic, motor-disabled and local haptic-editing
suppression stays in force; suppressed reminders are dropped, not queued for
later. Status receipt does not turn on an explicitly powered-off device.
Lights retain the existing Agent status brightness pipeline and do not alter
the six independently configurable non-Agent keys.

`completed` means the host reports the end of a response/turn, not proof that
the user's complete task has passed acceptance. The host must handle session
closure, interruption, continued Stop hooks and background work before making
that assertion. Hooks are notification-only: they must not approve permissions.

### Desktop Agent status source (USB or BLE WMP, NORMAL)

Desktop apps (Doubao, Claude, WorkBuddy and others) show their tasks on Agent
keys 1, 2, 4, 5, 6 and 7 through one generic source, `desktop`. Console
observes the apps, assigns their conversations to the six slots and leases
them; the firmware only lights, reminds, keeps the lease and reports which
slot was pressed. It never knows which app owns a slot, so adding an app only
changes Console. The Claude Code operating mode (`claude_code` source) and the
Codex Vendor HID path are unchanged. The former `doubao` source and its
`doubao_*` capabilities and GET_STATUS objects are removed; Doubao uses
`desktop`.

Require `features.desktop_agent_status=true`. This is an additive source on
the existing commands, not a new operating mode. The firmware requires a HELLO
session on the currently claimed USB or BLE transport; Console verifies device
identity with the existing authentication flow.

`SET_AGENT_STATUS` (0x28) accepts exactly:

```json
{"source":"desktop","states":["working","completed","unknown","idle","idle","idle"],"completion_seq":[0,3,0,0,0,0]}
```

Both arrays have exactly six entries in physical key order 1, 2, 4, 5, 6, 7.
States are `unknown`, `idle`, `standby`, `working`, `completed`, `approval`,
`reply`, `error` and `codex`:
- `standby`: the slot holds a conversation the user has already seen (Console
  decides). It shows Codex's standby white.
- `codex`: the slot is yielded to Codex. Its light shows the Codex task color
  for that slot (or the ordinary Codex fallback), its key follows the ordinary
  NORMAL Codex routing, and only Codex reminders apply to it. Six `codex`
  slots equal no ownership.

On firmware advertising `features.desktop_task_navigation=true`, the request
may also contain `"encoder_navigation":true` (see below). Counters are unsigned
32-bit integers. Other fields, wrong lengths, fractional, negative,
out-of-range counters and unrecognized states fail with `VALIDATION_FAILED`
without changing state or extending the lease. SET outside NORMAL, or without a
HELLO session, returns `READ_ONLY`. `CLEAR_AGENT_STATUS` (0x29) accepts exactly
`{"source":"desktop"}` from any mode. It is idempotent and never clears Claude
Code state. ACK results are `{"source":"desktop","active":true,"lease_ms":5000}`
for SET and the same object with `active:false` for CLEAR.

Lease. Send a snapshot on every change and heartbeat unchanged snapshots at
most every 2 seconds, each with a new request ID. GET_STATUS, PING and replayed
ACKs never renew the 5-second lease. On expiry, CLEAR or protocol session reset
all six states become `unknown`, pending reminders and key events are discarded
and ownership is released, so the original NORMAL key routing and Codex lights
resume. No state is written to NVS. Console sends CLEAR when it exits normally.

Lights in NORMAL with a live lease:

| State | Light |
|---|---|
| `unknown` | dim white |
| `idle` | off (no conversation on this slot) |
| `standby` | white, as Codex standby |
| `working` | blue 0x304FFE |
| `completed` | green 0x00FF4C |
| `approval`, `reply` | orange 0xFF6D00 |
| `error` | red 0xFF0033 |
| `codex` | the Codex light for this slot |

Brightness follows the existing Agent light setting. A completed slot stays
green until Console sends `standby`; the firmware has no completion timeout.
Console should send `unknown` sparingly, because it is also white.

Reminders. The first snapshot after reset, CLEAR, lease expiry or selection
establishes a silent baseline. Then, per slot:
- a higher `completion_seq` while `completed` requests one pulse. Equal or
  lower counters never replay, including `completed -> unknown -> completed`.
  Console increments a counter only for a newly verified real completion; at
  UINT32_MAX it must CLEAR and start a new baseline;
- entering `approval` or `reply` requests two pulses; switching between the
  two does not remind again;
- entering `error` requests three pulses;
- `working`, `idle`, `standby` and `unknown` rearm the slot. `completed` does
  not.
Reminders of `codex` slots come from Codex under its own rules. All reminders
of one scheduler cycle merge into the highest priority (error > action needed >
completion) under the existing haptic strength, task-reminder switch and
suppression rules; suppressed reminders are dropped, not replayed. A real
state change wakes the status lights from idle standby once; unchanged
heartbeats do not. Explicit power-off and USB charge standby keep their
existing behavior.

Firmware from 2026-11-12 no longer enters idle standby: the minutes saved on
the device stay in NVS and are ignored, the local settings menu has no standby
page, and the screen and Bluetooth stay on. `GET_STATUS.result.action_engine.
idle_standby_active` remains in the contract and is always `false`. USB
standby, power-off and the idle dimming of lights and screen are unchanged.

GET_STATUS adds:

```json
{"desktop_agent_status":{"source":"desktop","active":true,"selected":true,"lease_ms":5000,"states":["working","completed","unknown","idle","idle","idle"],"completion_seq":[0,3,0,0,0,0],"encoder_navigation":false},"desktop_agent_press":{"source":"desktop","sequence":7,"agent":2,"transport":"ble"}}
```

`active` denotes a valid lease, not an executing task. `selected` is true when
the lease is active in NORMAL. Each new down edge of a non-`codex` Agent key
records a boot-scoped nonzero `sequence` (skipping zero on wrap) and zero-based
`agent` 0..5. This path bypasses the Codex connection, Vendor HID send success
and the NORMAL Agent-key preference, and emits no Codex key command. A Codex
key already held when a lease arrives is still released to Codex. Held,
repeated and release edges never produce another event; local input
suppression still wins. Only the latest event is kept, for 2 seconds; reads do
not consume or extend it. After expiry, CLEAR, mode change, or when read from a
transport other than the one owning the lease, `agent` and `transport` are null
while `sequence` remains for baselining. The firmware reports only which slot
was pressed: Console decides which conversation to open and must verify the
target conversation identity before treating a switch as successful.

`GET_STATUS.result.codex_micro.status_age_ms` is the time in milliseconds since
Codex last sent task status over USB or BLE, or null before the first one this
boot. Console uses it to tell whether Codex is active when deciding how to
share the six lights.

Over BLE, Console polls GET_STATUS for key events. The polling interval will be
reported by the firmware once measured (design decision); until then 250 ms is the
working value. Keys outside the six Agent positions retain their configured HID
routing. Power V2 factory profiles use Right Alt + Space on key 8 to start or
stop voice input and Enter on double-click (Matrix12 V1 keeps Space).

### Desktop encoder task navigation

With `"encoder_navigation":true` in the live lease in NORMAL, clockwise
rotation advances one slot and counterclockwise retreats one, wrapping over the
six slots; the firmware consumes the rotation instead of emitting wheel
reports. Without it, after CLEAR or expiry, and in local menus, input capture
and other modes the encoder keeps its ordinary routing. The opt-in is volatile.

`GET_STATUS.result.desktop_navigation` carries the current input snapshot:

```json
{"source":"desktop","sequence":9,"anchor_sequence":7,"offset":2,"agent":4,"transport":"usb"}
```

`sequence` is a boot-scoped uint32 counter incremented for each Agent key down
and encoder step, skipping zero on wrap; CLEAR preserves it but discards pending
input. `anchor_sequence` identifies the most recent Agent key, whose zero-based
slot is `agent`; `offset` accumulates later encoder steps modulo six. Without
an anchor `agent` is null. After two seconds without input, lease loss, input
suppression or reads on another transport, `agent` and `transport` are null;
`sequence` and `offset` remain for baselining. Console applies only fresh
input: a new anchor selects `(agent + offset) modulo 6`, otherwise the offset
difference gives relative steps.

`features.firmware_update` gates the `FW_*` commands. The client must also obey
`limits.firmware_chunk_bytes` and `limits.firmware_image_bytes`.

`CAPABILITIES.result.features.device_authentication` is `true` only when the
device has loaded a complete provisioned identity and its signing provider is
ready. The sibling `CAPABILITIES.result.device_authentication` object always
describes protocol version `1` and signature algorithm
`rsa-pss-sha256-salt32`, so development software can understand the protocol
without treating an unprovisioned sample as authenticated.

## Bluetooth device name

`CAPABILITIES.result.features.ble_name=true` enables `BLE_NAME_GET` and
`BLE_NAME_SET`; `result.limits.ble_name_max_utf8_bytes` is `24`. Missing/false
capability means unsupported: clients must not send these commands. Shared
constants are `WMP_BLE_NAME_MAX_UTF8_BYTES=24u` and
`WMP_BLE_NAME_DEFAULT="Boring Mist"`.

GET accepts exactly `{}`; SET accepts exactly `{"name":"我的键盘"}`. Both
return an ACK with the usual `command` and the same result fields:

```json
{"command":"BLE_NAME_SET","result":{"saved_name":"我的键盘","active_name":"Boring Mist","default_name":"Boring Mist","restart_required":true}}
```

`saved_name` is the effective persisted name for the next boot, falling back
to `default_name` when no preference is stored. `active_name` is the name
used for BLE initialization in this boot, not a host's cached display name.
`restart_required` is exactly `saved_name != active_name`. Saving the active
name again clears it. Restore default sends SET with GET's `default_name`;
no separate reset command is added. An unchanged saved name succeeds without
rewriting NVS.

Names contain 1–24 UTF-8 bytes (excluding the storage NUL terminator), must
decode to Unicode scalar values, and are never truncated, trimmed or
normalized. Reject C0 controls U+0000–U+001F, DEL/C1 U+007F–U+009F, and line/
paragraph separators U+2028/U+2029 anywhere. Leading and trailing whitespace
use this exact Unicode White_Space set: U+0009–U+000D, U+0020, U+0085, U+00A0,
U+1680, U+2000–U+200A, U+2028, U+2029, U+202F, U+205F, U+3000. Other internal
whitespace is permitted. Chinese, English, digits and ordinary symbols are
accepted within the byte limit. Invalid fields, types, UTF-8, lengths or
characters return existing `VALIDATION_FAILED` (9). Invalid JSON framing
retains the existing `INVALID_JSON` behavior.

SET ACK means persistence succeeded; it does not reboot, disconnect BLE or
change the active advertisement. The saved name takes effect at the next
normal reboot. With USB continuously supplying a powered-off Power V2, a
qualified user power-on also performs one normal software restart when the
saved and boot-loaded names differ. An unchanged name uses the usual fast
wake. An in-progress configuration or firmware transaction defers that
restart; GET continues to report `restart_required=true` until a later
restart applies the name. Reconnecting alone does not guarantee activation;
host name caches may lag even after activation. `active_name` is the name
loaded by BLE initialization, not proof of an on-air advertisement. Storage failure returns
`STORAGE_FAILURE` (12), or `INTERNAL` (15) for an internal failure, never a
success ACK. Mutually exclusive writes, including upgrade/factory reset,
use `BUSY` (11). Writes run on the existing serialized main-loop/storage
path. Existing HELLO, USB/BLE transport and console device-authentication
rules apply unchanged; these commands introduce no authentication bypass.

SET is queued by the receive callback and acknowledged only after main-loop
persistence. There is one pending name write: another SET returns `BUSY`.
An identical pending request ID/payload waits for the original response;
reusing that ID with different content returns `DUPLICATE_REQUEST_MISMATCH`.
Completed requests use the existing response replay cache. GET does not wait
for a queued SET, so clients must wait for SET's ACK before confirmation GET.
A fresh HELLO, transport release or session reset cancels an unexecuted name
write without persisting it or sending a response into the new session. This
is a session-cancellation exception to the usual request/response rule; after
reconnecting, GET establishes whether the old write had already completed.

This is a single device preference shared by all slots and operating modes,
outside configuration JSON, generations and configuration digest semantics.
Power cycles, ordinary OTA, Profile changes and configuration imports preserve
it. Successful factory-default activation restores the default name; failed
activation must retain the saved name. Resetting this preference does not
clear identity or pairing. USB identity, BLE addresses, service/characteristic
UUIDs and HID behavior remain unchanged; the nickname is not device identity.

## Device authentication

Device authentication is an offline, one-way proof from a BORING device to the
official console. It does not encrypt USB CDC, authenticate the host, or stop
ordinary HID input when authentication is absent or fails.

`AUTH_GET_CERTIFICATE` (`0x04`) accepts exactly `{}` and returns:

```json
{"command":"AUTH_GET_CERTIFICATE","result":{"certificate":{"version":1,"issuer_key_id":"BORING-PRODUCTION-ROOT-1","product_id":"wired-macro-pad-v1","hardware_id":"WMP-S3-MATRIX12-POWER-V2","serial":"CP01-AABBCCDDEEFF","public_key_algorithm":"rsa-3072","public_key_spki":"base64url DER SubjectPublicKeyInfo"},"issuer_signature":"base64url signature","signature_algorithm":"rsa-pss-sha256-salt32"}}
```

The certificate object contains exactly the seven fields shown above. The
issuer signs its RFC 8785 JCS UTF-8 bytes using RSA-3072 PSS with SHA-256, MGF1
SHA-256 and a fixed 32-byte salt. `public_key_spki` and all signatures are
unpadded base64url. The decoded public key must be 3072-bit RSA with exponent
65537.

`AUTH_CHALLENGE` (`0x05`) accepts exactly one field, `nonce`. It must be the
43-character unpadded base64url representation of exactly 32 bytes. The device
constructs this restricted object:

```json
{"domain":"BORING-WMP1-DEVICE-AUTH-V1","nonce":"43-character base64url nonce","serial":"certificate serial"}
```

The device hashes the object's JCS UTF-8 bytes with SHA-256 and signs that
digest through its hardware-backed RSA DS provider using the same
`rsa-pss-sha256-salt32` parameters. It returns:

```json
{"command":"AUTH_CHALLENGE","result":{"nonce":"same nonce","signature_algorithm":"rsa-pss-sha256-salt32","signature":"base64url signature"}}
```

The host generates a new nonce for every physical/serial connection, verifies
the issuer signature first, then verifies this challenge signature and the
shared identity fields. The device stores no authenticated-host session state.
Malformed shapes, padded/invalid base64url and wrong nonce length return
`VALIDATION_FAILED`. Missing/corrupt identity returns
`DEVICE_IDENTITY_UNAVAILABLE`; a DS signing failure returns
`DEVICE_SIGNING_FAILED`. Replayed WMP request IDs continue to use the existing
bounded replay rules, but an old signature cannot satisfy a different nonce.
`features.prompt_storage` advertises the independent UTF-8 prompt store, while
`features.prompt_trigger_usb` advertises the USB helper event path. Clients
must obey `limits.prompt_slots`, `limits.prompt_name_bytes`,
`limits.prompt_body_bytes`, and `limits.all_prompt_body_bytes`.
`features.firmware_signature_required` reports whether the running boot chain
requires signed application images. The current Engineering Alpha build
reports false; its updater still performs image-format validation, product and
hardware binding, version matching, and full-image SHA-256 verification.

Macro byte limits apply to the deterministic encoded step stream, excluding
the macro ID and display name. Every step costs one opcode byte. `press`,
`release`, and `tap` add one HID-usage byte (2 bytes total); `delay` adds one
unsigned 16-bit millisecond value (3 bytes total); and `text` adds one unsigned
16-bit byte count plus its printable-ASCII bytes (`3 + text byte length`). The
256-byte limit applies to each macro, and the 4096-byte limit is the sum of all
macro step streams.

## Configuration transaction

`GET_CONFIG` ACK returns `generation`, `digest`, and `config`.
`VALIDATE_CONFIG` returns field errors without persisting. `SET_CONFIG` requires
the full configuration, its JCS digest, and `base_generation`.

The exact v1 request objects are:

```json
{"config": {"...":"full schema-v1 config"}, "digest":"lowercase sha256"}
{"config": {"...":"full schema-v1 config"}, "digest":"lowercase sha256", "base_generation":1}
{"platform":"macos"}
{"base_generation":1, "confirmation":"FACTORY_DEFAULT"}
```

`GET_STATUS.result.stack_diagnostics` is an optional, read-only runtime field:

```json
{"usb":{"task":"TinyUSB","min_free_bytes":4440,"samples":8},"ble":null}
```

Each transport is `null` until sampled. `min_free_bytes` is the callback task's
lifetime minimum free stack reported by ESP-IDF, in **bytes**, not FreeRTOS
words. `samples` counts completed HELLO, AUTH_GET_CERTIFICATE, AUTH_CHALLENGE,
GET_CONFIG and GET_STATUS handlers. A status response includes observations
through the previous completed handler; its own sample is available on the
next request. Sampling and serialization use the existing transport mutex.
Reconnect/configuration reset does not reset the task's lifetime watermark.
Older firmware may omit this field; missing data does not prove safe headroom.
This is not an interrupt-stack measurement or a worst-case static call graph.

`GET_STATUS.result.liveness` (stability item 2) is read-only:
`supervising`, `task_watchdog` (the liveness supervisor is subscribed to the
task watchdog), `previous_stall` (`none`, `main_loop` or `usb_config_rx`:
the channel that stalled before the last reset), `previous_stall_uptime_ms`,
and `channels.<name>` (`main_loop`, `usb_config_rx`, `usb_stack`,
`ble_host`) with `armed`, `resets`, `beats`, `max_gap_ms` (longest time
between beats while armed, for setting timeouts from measurements) and
`overdue` (late episodes this boot). Only `main_loop` (armed once the loop
runs) and `usb_config_rx` reset: a stall stops the supervisor feeding the
task watchdog, which resets the device (reset reason `task_watchdog`).
`usb_stack` and `ble_host` are probed ESP-IDF tasks and only report late
answers (review A3). Start-up is not watched.

`GET_STATUS.result.safe_mode` (stability items 5 and 6) is read-only:
`active`, `reason` (`none`; `crash_loop`; `manual`: Key 1 + Key 12 held from
power-up for 3 s, checked before board start-up, confirmed on release, for
that boot only; `low_memory`: the previous boot kept the largest free
internal DMA block below 4096 bytes for 10 s during its first 60 s and
restarted once into safe mode; `ble_no_memory`: the previous boot's BLE
start-up failed with `ESP_ERR_NO_MEM`), `abnormal_resets` (panics and
watchdog resets in a row, including the one before this boot; it belongs to
the running image and a different image starts from zero), `limit` (3),
`reset_reason` of this boot (`power_on`, `software`, `panic`,
`task_watchdog`, `interrupt_watchdog`, `watchdog`, `brownout`, `usb`,
`cpu_lockup`, ...), `nvs_mirror` and `stable_cleared` (this boot ran 60 s
normally and cleared the count). Reaching the limit starts the next boot in
safe mode; that boot stores limit - 1, so a restart tries a normal boot and
one more abnormal reset returns to safe mode.

`GET_STATUS.result.power_button` (Power V2, read-only, since boot): `presses`,
`short_presses`, `long_presses` (shutdown or wake requests raised), `ignored`
(long presses the application dropped) with `last_ignored` (`none`,
`calibration`, `diagnostic_capture`, `shutdown_active`), `wake_mode` (OFF
standby: the 6.5 s wake hold applies) and `armed` (a release was seen since
start-up). It shows where a knob hold that did not shut down went.

`GET_STATUS.result.codex_micro.advertising` adds `backoff_level` (watchdog
restarts since advertising last worked), `backoff_delay_ms` (3000 three
times, then 10000, 30000 and 60000) and `input_restarts` (restarts brought
forward by a key press) and `stale_flags` (start or retry flags cleared
because they were stuck for 10 s) (stability item 3).

`GET_STATUS.result.heap` is an optional, read-only runtime field (added
2026-11-09 after a returned unit exhausted its DMA-capable internal heap):

```json
{"internal_dma":{"free":41200,"largest":15872,"minimum":20480},
 "internal":{"free":61344,"largest":30720,"minimum":40960},
 "spiram":{"free":7900000,"largest":7800000,"minimum":7700000},
 "reserve_held":true,"reserve_bytes":8192,
 "low_memory":{"threshold_bytes":4096,"active":false,"events":0,"last":null}}
```

Pools are `heap_caps` readings in bytes for `MALLOC_CAP_INTERNAL|MALLOC_CAP_DMA`,
`MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT` and, on PSRAM builds, `MALLOC_CAP_SPIRAM`:
current free size, largest free block and the lowest free size since boot.
`reserve_held` reports the emergency internal block the firmware keeps for the
OTA image check and the AUTH signature and hands back right before them.
`low_memory` samples the DMA-capable internal pool every 2 s. An episode starts
when its largest free block drops below `threshold_bytes` and ends above twice
that value. `events` and `last` survive software resets and are cleared by a
power-on reset; `last` is `null` or
`{"boots_ago":0,"uptime_ms":123456,"free":3000,"largest":2048}`, where
`boots_ago` counts software resets since that episode. `checkpoints` lists up
to 20 readings in recording order, each
`{"label":"config_store","uptime_ms":812,"internal_dma_free":61000,"internal_dma_largest":30720,"internal_free":64000}`:
one after each start-up step (`app_entry`, `board`, `config_store`, `stores`,
`action_engine`, `firmware_update`, `device_identity`, `usb_service`,
`codex_micro_ble`, `ble_config`, `main_loop`) and one at the first
`usb_mounted`, `ble_first_connect` and `ble_first_auth` after boot. The
difference between consecutive entries is what that step took. Older firmware
omits this field.

`low_memory.persisted` is `{"events":N,"last":null|{"uptime_ms":..,"free":..,"largest":..}}`:
episodes counted across boots and power loss, kept in NVS (`wmp_diag`/`low_memory`)
and written from the main loop when an episode starts.

`GET_STATUS.result.codex_micro` (BLE diagnostics) adds two optional fields:

- `ble_slot_history`: the last 8 slot operations, newest first, persisted in
  NVS on every operation. Each item is
  `{"op":"select"|"clear"|"pair_save","slot":2,"source":"key"|"protocol"|"pairing"|"unknown","error":0,"timeout":false,"uptime_ms":1234,"boots_ago":0}`;
  `boots_ago` counts boots since the operation.
- `advertising`: `{"starts","start_failures","last_start_status","stops","retries_exhausted","active","starting","data_configured","scan_response_configured","peer_filter_ready","peer_filter_state","slow"}`,
  counters since boot plus the current state of the advertising state machine.

Request fingerprints (the request-ID replay check) use an allocation-free
SHA-256, so a device no longer answers `NACK INTERNAL "could not fingerprint
request"` when its internal heap is exhausted.

`GET_CONFIG` and `GET_STATUS` use `{}`. `FACTORY_DEFAULT` always stages the
built-in default into the inactive slot; it does not erase the currently active
slot before verification. `GET_STATUS.result.pending` is either `null` or an
object containing the candidate `generation` and `digest`, and
`inputs_neutral` reports whether activation can occur.
The destructive command is exposed by BORING Console only, after two explicit
user confirmations. After activation, firmware clears prompt slots and BLE
pairings before restarting.
`GET_STATUS.result.platform` is `unselected`, `macos`, `windows_linux`, or
`custom`. `custom` preserves a valid configuration created before platform
metadata existed, and is also used when a full custom configuration is
installed before first-run system selection. `SET_PLATFORM` accepts only
`macos` or `windows_linux`; it selects the matching built-in Codex Profile and,
when that changes the active Profile, uses the same neutral-input transaction
as `SET_CONFIG`.
On Matrix12 Power V2, explicit platform selection also remembers the OS for the
current connection: USB when mounted, otherwise the active BLE slot (1–3).
Each connection has an independent preference. This reuses the existing
activation transaction; a pending selection retains its originating connection
even if the transport changes before activation. Selecting the already-active
Profile still persists a previously unrecorded preference without changing the
configuration generation. Ordinary `SET_CONFIG` / Profile switching does not
overwrite these preferences. Clearing a BLE peer forgets only its slot's OS;
factory default clears all four preferences. A missing matching built-in
Profile is rejected without resetting or replacing the active configuration.
`GET_STATUS.result.platform` continues to report the active OS, not the full
per-connection preference table; no command payload or HID report changed.
`GET_STATUS.result.activation_failed` is normally false. After five failed NVS
activation attempts with bounded backoff it becomes true; the pending slot is
retained for diagnosis and activation is retried after reboot.

`GET_STATUS.result.battery` optionally reports the latest fuel-gauge SOC as the
**display percentage** (integer 0–100). The CW2015 runs on its factory battery
model, so a full cell at rest reads only about 97 percent; firmware after
20261113.01 therefore shows the raw SOC unchanged up to 80, stretches 80..96 to
80..100, and shows 100 from 96 up (`battery_display_percent`, the same number as
on the screen, the BLE battery service and the Codex `device.status`). The raw
SOC is not reported separately; `battery_voltage_mv` below and the low-battery
guard keep the chip's own values. `battery_valid` is true only when that sample
is valid; otherwise `battery` is null. Older firmware may omit these fields and
reports the plain rounded SOC.
The handler reads the existing cached sample, without a new I2C poll or a
separate USB/BLE connection. `is_charging` is null on the current board because
the MCU has no verified charger-status signal. Neither USB host connectivity
nor a 100 percent SOC value establishes whether charge current is flowing.

`GET_STATUS.result.battery_voltage_mv` reports the cached CW2015 cell voltage
(integer millivolts, null when no valid sample). `GET_STATUS.result.power_guard`
reports the Power V2 low-battery guard: `state` is `normal`, `low`, `critical`,
`boot_blocked` or `locked_standby`; `low_battery_lock` is true after a
protective shutdown until a boot or recovery sees a healthy cell;
`brownout_resets` and `low_battery_shutdowns` are NVS counters that survive
reboots. Older firmware omits these fields.

`locked_standby` also covers a low cell on USB: when a USB host is mounted and
the cell stays below 3.55 V for 10 s, firmware shows the empty-battery icon and
parks in dark USB standby (lights, display backlight, haptics and HID off; the
USB configuration channel stays up) without setting `low_battery_lock` or
counting a shutdown. Both kinds of locked standby resume the saved ON session
once the cell holds at or above 3.70 V for 30 s. Until then the power button
only shows the empty-battery icon. Clients should present `locked_standby` and
`boot_blocked` as "battery too low, charging", not as a connection fault.

`GET_STATUS.result.usb_standby` reports USB standby periods since this boot.
On USB power the Power V2 power-off pulse cannot remove MCU power, so a user
OFF parks in USB standby and a later ON is a fast wake without a reset; only
FW_STATUS `current_boot_sequence` shows real reboots. Fields: `active`;
`entries` and `wakes` (counters since boot, RAM only); `last_entry_reason`
(`none`, `power_button`, `low_battery`, `usb_low_battery` or `boot`);
`last_wake_reason` (`none`, `power_button` or `battery_recovered`);
`last_change_ms` and `uptime_ms` on the same since-boot clock. A battery
splash shown while parked belongs to the same period. A wake that restarts
is not counted as a wake: the reboot resets the trace. A wake restarts once to
apply a saved BLE name (above) or when the active or pending configuration
still needs a boot-time migration (for example a Power V2 haptic strength
below 60 percent), unless a configuration or firmware transaction is in
progress. Older firmware omits this object.

`GET_STATUS.result.active_controls` is a unique array of the stable control IDs
currently held or classified as active. It is empty when all physical controls
are released. `GET_STATUS.result.action_engine` reports the read-only host-output
state, local device page and local quick-config flags. These fields are for live
diagnosis and do not stage or activate configuration.

`GET_STATUS.result.joystick_suspect` is a read-only boolean. It becomes true
when any joystick direction remains continuously active for 10 seconds. While
true, firmware isolates joystick direction output from local actions and host
reports. A real centered sample clears it. Reading the field never calibrates
the joystick or writes configuration.

Targets with an analog joystick add the optional read-only
`GET_STATUS.result.joystick_diagnostics` object. It contains `raw_x`, `raw_y`,
`filtered_x`, `filtered_y`, `center_x`, `center_y`, `minimum_x`, `maximum_x`,
`minimum_y`, `maximum_y`, `deadzone_x`, `deadzone_y`, `directions`,
`radial_active`, `radial_valid`, and `radial_angle_turns`. `directions` is a
bit mask: left `0x01`, right `0x02`, up `0x04`, down `0x08`. Reading these
values never starts calibration and never changes the active configuration.
Power V2 also reports cumulative `sample_count`, `adc_failure_count`, `spikes_rejected` (isolated raw samples at least 300 counts away from the median of the last three, which are kept out of the position filter and therefore out of the direction events; the `raw_*` values and the window still show them), the
`sample_timestamp_ms` of the last successful ADC pair, and
`recent_sample_age_ms`. The two time fields are `null` before the first
successful pair. Failed reads retain the previous raw/filtered values but do
not advance the success count or timestamp. `window` contains `start_ms`,
`end_ms`, `sample_count`, and raw/filtered X/Y `min`, `max`, `mean` values for
successful samples since the preceding `GET_STATUS`; `start_ms`/`end_ms` are
`null` and the extrema/means are absent when the window has no samples.
The window is consumed by the status reader; clients needing an uninterrupted
observation must use one status-polling consumer.
Power V2 adds `GET_STATUS.result.input_event_losses` with cumulative `board`,
`encoder_isr`, and `diagnostic_protocol` counters. These count, respectively,
full board event ring drops, full encoder edge queue drops, and overwritten
diagnostic capture events. Compare each counter with its value at the start
of a test; any increase means that test's event evidence is incomplete.
The authoritative Power V2 response example is
`fixtures/status-matrix12-power-v2-v1.json`.

The device validates in RAM, writes and verifies the inactive slot, then waits
for neutral physical inputs. If activation is not immediate it ACKs with
`state=PENDING_ACTIVATION`, candidate generation/digest, and active control IDs.
The pending-slot marker is persisted with the candidate, so a power loss or
restart preserves the pending transaction and still waits for neutral inputs
before changing the active-slot marker.
The client polls `GET_STATUS` every 250 ms and never sends a second SET. After
10 seconds without a confirmed result the client enters Unknown. A disconnect
or lost ACK is resolved by comparing generation and digest after reconnect; no
automatic write retry is allowed.

### NORMAL single/double keyboard gesture

Firmware that advertises `key_gesture` in `CAPABILITIES.result.actions` accepts
this additive schema-v1 action; the wire and schema versions remain unchanged.
Clients must use the live action list before offering or applying it. Older
firmware and existing capability fixtures do not imply gesture support.

```json
{"type":"key_gesture","usage":104,"modifiers":[],"double_usage":40,"double_modifiers":[]}
```

`usage` and `double_usage` are required keyboard usages in `4..231`. Their
optional modifier arrays contain at most eight distinct usages in `224..231`;
omitted arrays mean no modifiers. The action is supported only on MATRIX12
ordinary keys `key.8` through `key.12`, in NORMAL mode. CODEX/CC official
controls retain their mode-specific behavior unless the explicit CODEX voice override below is present.

The first click waits for the firmware double-click window to expire before
emitting its keyboard chord. A double click emits only the `double_usage`
chord, without a preceding single-click chord. Both gestures produce an
ordinary HID keystroke over the active keyboard transport, timed like a
person typing the chord: modifiers go down first, the key follows 20 ms later
and is held for 60 ms, then the key and, 20 ms later, the modifiers are
released (about 100 ms in total; a chord without modifiers is a 60 ms key
press). Hotkey listeners can miss a chord that lasts a single USB poll. A
mode, link or configuration reset releases a keystroke in progress at once.
They do not hold a modifier across clicks. Existing ordinary `key` actions retain
their normal press/release behavior.

Gesture timing and limits (A-16): the double-click window is 300 ms
(`KEY_GESTURE_WINDOW_MS`); a single click is emitted only after that window
expires, so single-click start/stop always carries up to 300 ms of latency. A
`key_gesture` never holds a key. Hold-to-talk is a plain held `key` action and
cannot also double-click to send on the same key; it sends with a separate key
(Normal Key 11 / Key 9). "Hold to talk, double-click to send" on one key needs a
new firmware action and is not part of protocol v1.

Power V2 factory defaults use this gesture on Key 8: Right Alt + Space
(usage 44, modifier 230) starts or stops voice input, double-click sends Enter.
Matrix12 V1 keeps Space.

The example uses F13 for a separately associated voice tool and Enter for
the focused application's send action. It does not configure Typeless or
prove that the application accepts either shortcut. Firmware does not track
recording or transcription state, wait for transcription, or focus Codex.
The user starts speech with one click, ends it with another, waits until the
text is visible in the intended Codex input, and then double-clicks to send.
Double-clicking while speech is active is not an end-and-send operation.

### CODEX voice key choice (Matrix12)

Firmware advertising `CAPABILITIES.result.features.codex_voice=true` accepts an
optional `codex_voice` action on each profile. It may be a `key_gesture`; for
example `"codex_voice":{"type":"key_gesture","usage":228,"double_usage":40}`.
Firmware that additionally advertises `features.codex_voice_hold=true` also
accepts a regular `key`, such as `"codex_voice":{"type":"key","usage":104}`,
to emit key down while the physical key is held and key up when it is released.
Omitting this field selects the existing official Codex dictation action (ACT10).
There is no change to normal `mappings`, other official controls, or CC mode.
Rev A and assembly-alignment firmware do not support this field.

Only CODEX key 8 uses this override. `key_gesture` uses the existing single/double
keyboard pulse routing; `key` uses the held-key routing. Both use USB/BLE without
requiring the official Codex agent link. Mode changes, transport loss, pending
config activation, and device-local input ownership cancel pending gestures or
release a held key through the existing reset paths.
Switching back to official dictation removes `codex_voice` from that profile.

Power V2 firmware advertising `CAPABILITIES.result.features.global_voice=true`
accepts optional root `voice_action` (`key` or `key_gesture`). Once the user
explicitly saves it, physical Key 8 emits that one action in Normal and Codex
across all Profiles; old per-Profile Key 8 and `codex_voice` values remain in
the config but no longer route those two modes. Normal Key 11 emits Enter as
the separate send key, while Codex Key 11 keeps its existing send action.
Claude Code Key 8 holds Command+D (macOS) or Ctrl+D (Windows) for dictation while the key is held. Omitting `voice_action`
keeps legacy behavior until the user selects or records the shared action.

Clients must check the live capability before adding the field, then use the
normal validate/write/readback transaction. Old firmware is not upgraded by a
Console update. Before rolling firmware back to a version that rejects this
field, apply an official-voice configuration without the field and retain a
configuration backup. The existing schema/wire versions remain 1.

## Computer-task trigger flow

Firmware advertising `features.host_action_usb=true`, `limits.host_actions=255`
and `host_action` in `actions` accepts `{"type":"host_action","action_id":1,"task_token":"0123456789abcdef0123456789abcdef"}`.
The reference is an integer from 1 through the advertised limit. `task_token` is
an independent task UUID encoded as exactly 32 lowercase hexadecimal characters.
The slot number is not task identity: the host matches device serial, action_id
and task_token before execution. Creating a new task generates a fresh token;
editing an existing task preserves it. Reusing a cleared slot on another computer
therefore cannot trigger a stale task remembered by the first computer.
These fields are not prompt IDs and store no prompt text.
The host resolves the device serial from the current HELLO session.

Initial placement is Matrix12 `key.8` through `key.12`, intersected with the
advertised `controls`. REV-A and assembly-alignment firmware do not support this
action. Only NORMAL mode executes it, once on press; release does nothing.
CODEX/CC official keys, rotary controls, joystick and gesture bindings stay under
their existing owners. GET_CONFIG returns the mapping unchanged through the usual
configuration transaction; unsupported firmware must not be given this action.

Over USB, poll `GET_HOST_ACTION_EVENT` (`0x1E`) with `{}`:

```json
{"command":"GET_HOST_ACTION_EVENT","result":{"poll_after_ms":100,"event":{"event_id":1,"action_id":1,"task_token":"0123456789abcdef0123456789abcdef"}}}
```

An empty queue returns `event:null`. BLE requests return `READ_ONLY` and never
mark the listener alive or consume events. The RAM queue has eight entries;
triggers require polling within 1500 ms, and overflow rejects the new trigger.
Events are FIFO, with nonzero boot-scoped event IDs. On USB session reset,
diagnostic capture, mode change or configuration activation, pending events are
discarded. A poll after listener expiry also discards old events before renewing
the listener, so background restarts cannot replay delayed side effects.
The existing request-ID replay behavior applies to retransmitted requests.

The host deduplicates within the connected device session, binds each event to
the applied local task snapshot and reports missing/offline/failed tasks. There
is no prompt storage dependency, Unicode insertion, or fallback prompt paste.
Existing prompt commands and their consumers retain their previous semantics.

## Prompt storage and trigger flow

Prompts are stored independently from the 16 KiB device configuration. There
are 12 slots (`prompt_id` 1 through 12); each has a non-empty UTF-8 name of at
most 48 bytes and a non-empty UTF-8 body of at most 4096 bytes. The firmware
stores the exact UTF-8 text, including Chinese and line breaks. It does not try
to emit that text through keyboard HID, because keyboard HID cannot reliably
insert arbitrary Unicode without host input-method assumptions.

The request shapes are:

```json
{}
{"prompt_id":1}
{"prompt_id":1,"name":"总结","body":"请总结以下内容：\n"}
{"prompt_id":1}
```

They correspond to `GET_PROMPT_LIST`, `GET_PROMPT`, `SET_PROMPT`, and
`DELETE_PROMPT`. A config mapping may use
`{"type":"prompt","prompt_id":1}`. Pressing that control queues an event
only while the host helper is actively polling. Otherwise the device shows an
error feedback page and sends no keyboard text.

The helper polls `GET_PROMPT_EVENT` with `{}` about every 100 ms. Its response
contains `poll_after_ms` and either `event:null` or
`event:{"event_id":1,"prompt_id":1}`. After receiving an event, the helper
uses `GET_PROMPT`, then inserts the returned body through the operating
system's Unicode text API. The helper must keep polling; after 1500 ms without
a poll the firmware treats it as offline. Events are consumed once and are not
replayed after reconnect.

Factory reset clears both the normal configuration and this prompt store. The
prompt store is a dedicated flash partition, so the first firmware installation
that introduces it requires a full partition-table flash; later application
updates may continue through the normal OTA path.

## Joystick calibration transaction

Calibration uses one device-local session. The configurator first asks the user
to release the joystick, sends `CALIBRATION_START` with `{}`, and keeps it at
rest during the returned `center_window_ms` (currently 500 ms). The firmware
continues sampling while suppressing joystick direction, radial and press
output so calibration cannot move the host UI.

`CALIBRATION_START` returns a nonzero `session_id`, `state=CENTERING`, the raw
sample, `center_window_ms`, and `timeout_ms`. After the center window,
`CALIBRATION_SAMPLE` requests use:

```json
{"session_id":1}
```

The response state becomes `CAPTURING` and contains `raw`, `center`, `minimum`,
`maximum`, `travel_complete`, and `ready_to_confirm`. `travel_complete` means
both sides of both axes were reached. The configurator then asks the user to
release the stick to center, move it away, and release it once more. Each return
must remain near center for 200 ms; `ready_to_confirm` becomes true only when
both returns and the resulting center/deadzone are usable, with a fresh reading
currently inside that deadzone. Older firmware omits
`ready_to_confirm` and retains its original confirmation behavior.
`CALIBRATION_CONFIRM` uses:

```json
{"session_id":1,"base_generation":8}
```

Confirmation is rejected until the candidate is ready and the current raw
sample is back inside the calculated deadzone. A successful
confirmation writes `calibrated=true`, center, endpoints and deadzones through
the same inactive-slot and neutral-input activation transaction as
`SET_CONFIG`; existing inversion and filter settings are preserved. Its result
is `PENDING_ACTIVATION` with generation and digest.

`CALIBRATION_CANCEL` uses `{"session_id":1}` and discards the session without
changing the active configuration. USB detach/reset also cancels the session,
and an abandoned session expires after the advertised timeout. A stale or
unknown session returns `NOT_FOUND`; a concurrent configuration write or second
calibration returns `BUSY`.

## Diagnostic input capture

Product targets advertise
`CAPABILITIES.result.features.diagnostic_capture=true`. A client starts an
exclusive physical-input capture session with `DIAGNOSTIC_START {}`. The first
start is accepted only when every control is neutral, configuration activation
and joystick calibration are inactive, the local UI is closed, and host output
is enabled. Starting releases all Keyboard, Consumer, Mouse and Codex output
before diagnostic ownership begins. A successful response is:

```json
{"command":"DIAGNOSTIC_START","result":{"active":true,"timeout_ms":3000}}
```

While capture is active, every logical press, release, encoder step and
joystick direction updates `GET_STATUS.result.diagnostic_capture` and is not
routed to mappings, macros, prompts, mode changes, local shortcuts or a host:

```json
{
  "active": true,
  "timeout_ms": 3000,
  "event_sequence": 18,
  "last_control": "encoder.cw",
  "last_pressed": true,
  "events": [
    {"sequence": 17, "timestamp_ms": 120000, "control": "encoder.ccw", "pressed": true},
    {"sequence": 18, "timestamp_ms": 120080, "control": "encoder.cw", "pressed": true}
  ]
}
```

`last_control` uses the stable control IDs already returned by `CAPABILITIES`.
Before the first event, `last_control` and `last_pressed` are both `null`.
Every accepted physical edge increments `event_sequence`. Repeating
`DIAGNOSTIC_START {}` renews the three-second lease without clearing the
sequence or queued events. `events` drains up to 16 queued events in FIFO
order per status response; unread events remain for the next response.
`timestamp_ms` is device uptime when the Action Engine published the event
to the protocol, not an ADC conversion timestamp. The legacy `last_control`
and `last_pressed` fields describe the last event in that response, or the
latest event if the batch is empty. A queue loss counter increase invalidates
claims of a complete press/release cycle.

`DIAGNOSTIC_STOP {}` is idempotent and returns
`{"command":"DIAGNOSTIC_STOP","result":{"active":false}}`. A stop, lease
expiry, USB detach or protocol-session reset ends active capture immediately,
but host output remains suppressed until every physical control is neutral.
If the device local UI takes ownership, diagnostic capture yields to that
existing local owner. Diagnostic capture and joystick calibration are mutually
exclusive; a conflicting start returns `BUSY`.

## Volatile lighting preview

Clients may use lighting preview only when
`CAPABILITIES.result.features.lighting_preview=true`. The
`SET_LIGHTING_PREVIEW` payload is an independent complete preview snapshot,
not the configuration Schema's `lighting` object:

```json
{"enabled":true,"brightness":80,"under_key":[{"r":0,"g":0,"b":0},{"r":0,"g":0,"b":0},{"r":0,"g":48,"b":64},{"r":0,"g":0,"b":0},{"r":0,"g":0,"b":0},{"r":0,"g":0,"b":0},{"r":0,"g":0,"b":0},{"r":0,"g":79,"b":152},{"r":24,"g":0,"b":48},{"r":0,"g":40,"b":32},{"r":40,"g":16,"b":0},{"r":32,"g":32,"b":0}]}
```

The example above is a 12-entry Matrix12 snapshot. The object contains exactly
`enabled`, `brightness`, and `under_key`.
`brightness` is an integer from 0 through 100. Every RGB channel is an integer
from 0 through 255, and the array length must equal
`features.under_key_rgb_count`. It intentionally omits `status`, whose colors
remain owned by the device state machine. On Matrix12, the existing Codex
status render continues to override the six Agent-status key positions.
Power V2 keeps its product output ceiling at device value 80. BORING Console
shows the same draggable 0–4 levels as the device: level 0 sends `enabled:false`
while preserving the last brightness, and levels 1–4 write `10/20/40/80`.
Level 4 therefore sends `brightness:80` without exposing the hardware limit in
the ordinary UI.

A validated request replaces the previous RAM-only snapshot, refreshes its
five-second lease, and returns:

```json
{"command":"SET_LIGHTING_PREVIEW","result":{"state":"ACTIVE"}}
```

The preview does not write NVS and does not change config generation, digest,
pending, or active configuration. `CLEAR_LIGHTING_PREVIEW` uses `{}`, is
idempotent, restores active-configuration lighting, and returns:

```json
{"command":"CLEAR_LIGHTING_PREVIEW","result":{"state":"CLEARED"}}
```

Malformed snapshots return `VALIDATION_FAILED`; only a successfully validated
SET refreshes the lease. While the device-local lighting page owns the output,
SET returns `BUSY` and CLEAR remains allowed. USB unmount/session reset, mode
change, entry into local lighting settings, config activation, or lease expiry
clears the preview. Neither command uses generation, digest, or a session ID.

## Firmware update transaction

Normal updates run over the existing USB CDC interface; the device writes only
the inactive OTA application slot. A package contains `wired_macro_pad.bin`
and `firmware-manifest.json`. Before transfer, both host and device require the
manifest `product_id` and `hardware_id` to match the connected unit. The first
image chunk also contains a fixed-offset WMP descriptor, preventing a valid Rev
A image from being installed on either Matrix12 target, and preventing Matrix12
V1 and Matrix12 Power V2 images from being installed on each other.

The exact request objects are:

```json
{"product_id":"wired-macro-pad-v1","hardware_id":"WMP-S3-MATRIX12-POWER-V2","version":"0.3.0-alpha.1","size":921648,"sha256":"64 lowercase hex characters"}
{"offset":0,"data":"base64 image bytes"}
{}
{}
{}
```

These correspond in order to `FW_BEGIN`, `FW_DATA`, `FW_STATUS`, `FW_END`, and
`FW_ABORT`. `FW_DATA` is strictly sequential. Because the normal WMP1 replay
cache returns the original ACK for an identical request ID and payload, a host
may retry a lost chunk ACK without writing the bytes twice. After reconnecting
to the same still-running device, the host may resume only when reported size,
SHA-256 and version all match its package; replacing a different interrupted
transaction requires an explicit abort.

`FW_END` succeeds only after the declared byte count, SHA-256, ESP image
validation, project name and version all match. It selects the inactive slot
and reboots after a short ACK grace period. Removing power during transfer
leaves the previous slot selected; the next update starts the inactive slot
again.

Host confirmation. A newly installed image boots in the bootloader's
pending-verify state and stays there until a host proves the configuration
channel works; it no longer confirms itself when its services start. Any reset
while pending (crash, watchdog, brownout, power loss or a restart command)
returns to the previous slot. There is no device-side timeout: an unconfirmed
image keeps running until the next reset. Two signals confirm it:

- Explicit: `FW_CONFIRM` (`0x45`) with
  `{"build_id":"exact WMP build_id","running_partition":"ota_0|ota_1"}`. Both
  must match the running image, otherwise `VALIDATION_FAILED`. The session must
  have completed `HELLO` and, when `CAPABILITIES.features.device_authentication`
  is true, a successful `AUTH_CHALLENGE`; otherwise `AUTHENTICATION_REQUIRED`.
  The ACK is
  `{"command":"FW_CONFIRM","result":{"was_pending":true,"rollback_pending":false,"confirm_source":"host","confirm_error":0,"health_ready":true,"confirm_in_ms":0}}`.
  Repeating it, or sending it for an image that is not pending, succeeds with
  no side effect. A failed otadata write answers `STORAGE_FAILURE`.
- Automatic, for hosts that predate `FW_CONFIRM`: on USB, a `GET_STATUS` in a
  session that completed `HELLO` approves the image before the response is
  built; if the health dwell below is already complete, that response reports
  `rollback_pending` false. Device
  authentication is not required on this path, because the Windows updater
  verifies with HELLO, PING, GET_CONFIG and GET_STATUS only; the Console
  bootstrap confirms the same way before it polls `FW_STATUS`. BLE sessions
  never confirm automatically.

Health gate (stability item 4). Either signal only approves the image. It is
confirmed once the device has also been healthy without a break for 10 s: main
loop running, USB mounted and not in safe mode. BLE readiness is reported, not
required. Approval lasts for the boot, so a host that
asked once is enough: the main loop confirms when the dwell completes, and
hosts see `rollback_pending` turn false in `FW_STATUS` (Console polls it) or a
repeated `FW_CONFIRM`. Until then `FW_CONFIRM` answers `rollback_pending:
true`, `health_ready: false` and `confirm_in_ms` (time left if the device
stays healthy, `-1` while unhealthy). Without a host's approval the image is
never confirmed, so an image whose USB configuration channel does not work
rolls back on the next reset (users cannot reach ROM download mode).
`GET_STATUS.result.firmware_health` reports `healthy`, `ble_ready`,
`approved`, `dwell_ms` and `ready_in_ms` (`-1` while unhealthy).
`FW_STATUS.result.firmware_update.confirm_wait` reports `reason` (`none`,
`not_approved`, `safe_mode`, `usb_not_mounted`, `health_dwell`), `approved`,
`ready_in_ms`, `ble_ready` and `stuck` (approved but still not confirmed
after 120 s; the device does not roll back by itself, the next reset does).

`CAPABILITIES.result.features.firmware_host_confirm` is true when the device
accepts `FW_CONFIRM`. `FW_STATUS.result.firmware_update.rollback_pending` stays
true until confirmation; `confirm_source` is `pending`, `not_required` (the
image was not pending at boot, for example a factory flash), `session` or
`host`, and `current_confirm_error` holds the last confirmation result. Older
firmware omits `confirm_source` and confirms during startup.

ROM download without the BOOT key (stability item 9). The BOOT key is on the
back of the PCB. There is deliberately no protocol command for it: without
secure boot and flash encryption any host program could use one to read the
whole flash (BLE bond keys, config, prompts). The ROM download mode is reached
only from safe mode on the device (see Safe mode), by a physical key
confirmation. Recovery tools then write only the application (`ota_0` at
`0x10000`) and `otadata`, never `0x0` or `0x8000`, and keep `nvs`,
`config_nvs`, `prompt_nvs` and `esp_secure_cert`
(`tools/flash_current_device.py --app-only`).

Changing the legacy single-app partition table to this A/B layout is a
one-time factory migration and therefore still requires ROM download mode
(BOOT/RESET or an equivalent fixture). Later application updates do not.

## Custom NORMAL home icon (screen icon v1)

`CAPABILITIES.result.features.custom_home_icon=true` advertises all seven
`SCREEN_ICON_*` commands. The corresponding `result.screen_icon` object is:

```json
{"version":1,"target":"normal_home","width":128,"height":128,"format":"rgb565_le","total_bytes":32768,"max_chunk_bytes":1024,"session_timeout_ms":15000}
```

Only an implemented target advertises this feature; `display=true` alone is
not sufficient. A missing or false feature means no icon commands may be sent.
This capability is additive, does not change protocol 1.0 or configuration
Schema v1, and does not bypass the existing authenticated client trust policy.
USB CDC and encrypted BLE configuration use the same requests, responses,
transport-owner rules and errors, not a separate image connection.

### Representation and exact payload fields

The image is 128 by 128 pixels, top row first and left to right, two bytes per
pixel: **RGB565 little-endian**, exactly 32768 bytes. R5/G6/B5 red `0xF800`
is `00 F8`, green `0x07E0` is `E0 07`, blue `0x001F` is `1F 00`. Firmware
converts to the existing panel-buffer byte order once, not twice. The client
corrects image orientation, composites transparency on black, and fills outside
`(2x+1-128)^2 + (2y+1-128)^2 <= 125^2` with black. The resource contains no
PNG/JPEG header, filename, path, checksum or signature. Existing WMP CRC and
NVS integrity remain in effect; persistence is confirmed by direct byte readback.

All fields listed below are required. Numbers are JSON integers (not booleans,
fractions, or numeric strings). `revision` and `base_revision` are uint32 values
0..4294967295. Revision zero is the initial default. Successful COMMIT and RESET
each advance it by one; it never wraps. At exhaustion mutation returns
`STORAGE_FAILURE` without changing the active icon. A resource revision is not
configuration `generation`. `upload_id` is an opaque nonzero uint32 valid only
within its originating transport connection/epoch; clients must not infer a
device identity or revision from it. Unknown extra request fields are rejected
with `VALIDATION_FAILED`.

| Command | Exact request object fields | ACK `result` fields |
|---|---|---|
| `SCREEN_ICON_GET` | Empty `{}` | Metadata below |
| `SCREEN_ICON_READ` | `revision`, `offset` (0..32767), `length` (1..1024); `offset+length <= 32768` | `revision`, `offset`, `data` |
| `SCREEN_ICON_BEGIN` | `base_revision`, `target="normal_home"`, `format="rgb565_le"`, `width=128`, `height=128`, `total_bytes=32768` | `upload_id`, `next_offset=0`, `max_chunk_bytes=1024` |
| `SCREEN_ICON_DATA` | `upload_id`, `offset` (0..32767), `data` | `next_offset` (0..32768) |
| `SCREEN_ICON_COMMIT` | `upload_id` | Metadata below |
| `SCREEN_ICON_ABORT` | `upload_id` | `aborted=true` |
| `SCREEN_ICON_RESET` | `base_revision` | Metadata below |

`data` is standard RFC 4648 base64 with padding, no whitespace or URL-safe
alphabet; its decoded size is 1..1024 bytes and cannot exceed the remaining
image range. Chunks need not align with rows or two-byte pixels. Clients use
the smaller of `max_chunk_bytes` and the active transport's payload capacity.
READ of the default source returns `NOT_FOUND`, not manufactured pixel bytes.
READ with a stale revision returns `GENERATION_CONFLICT` before mixing images.

Metadata has exactly `revision`, `source` (`default` or `custom`), `target`,
`format`, `width`, `height`, and `total_bytes`. Target/format/dimensions are the
same for both sources; default has `total_bytes=0`, custom has 32768. GET
reports only committed state, including while a new upload is in RAM.

### Session, persistence and retry semantics

BEGIN checks `base_revision` against the current committed resource and reserves
one RAM upload. A second BEGIN while an upload or an incompatible maintenance
transaction is active returns `BUSY`. Icon mutation is mutually exclusive with
OTA, Factory Default, configuration activation and calibration; use the existing
maintenance owner, not a parallel input or transport framework.

DATA appends at `next_offset`. An already-received range with identical bytes
is accepted without appending and returns the current `next_offset`, even with
a new request ID. Different bytes in an already-received range, partial overlap
with the unreceived range, a forward gap, invalid base64, zero bytes and an
oversize chunk return `VALIDATION_FAILED`; none advance the receive position.
COMMIT requires all 32768 bytes and rechecks `base_revision`. It writes the
inactive NVS record, reads bytes back, then commits the active selector and
incremented revision. Only then does it ACK and notify the display. A failure
returns `STORAGE_FAILURE` and preserves the previous active image. Successful
COMMIT consumes the upload. RESET selects `source=default`, increments the
revision even if already default, and does not delete configuration or prompts.

The upload inactivity lease is 15000 ms, refreshed only by newly processed
successful BEGIN or DATA (including a verified duplicate range). Ordinary
GET/READ/PING/status/Agent heartbeat traffic does not renew it. Cached response
replay does not renew it either. At the deadline the upload is cancelled and
RAM freed. A command for the just-expired upload on the same connection returns
`TIMEOUT`; an unknown or replaced identifier returns `NOT_FOUND`.

Connection loss or USB/BLE transport takeover cancels the old uncommitted
upload and clears its connection's replay cache. A freshly processed valid
HELLO with a new request ID also establishes a new session, cancels any old
upload and clears old replay entries before caching its own response. An
identical already-cached HELLO (same request ID and payload) is instead a retry:
it replays the ACK without cancelling an upload or resetting the session.
Reconnecting clients must therefore issue a fresh HELLO request ID, not reuse
the previous cached handshake. New owners can BEGIN; old connection identifiers
cannot be resumed. Closing and reopening a host serial port alone is not a
reliable device-side disconnect indication with the current CDC callbacks, so
fresh HELLO, explicit ABORT and the independently polled lease are required.
Lease polling must
run even when joystick calibration is inactive. Chunks must yield the shared
connection to status, prompt events and background helper heartbeats.

ABORT cancels only the matching uncommitted upload on the same connection.
Repeating the most recently successful ABORT on that connection is successful
even with a new request ID, until a new BEGIN/session replaces its cancellation
record. A successful new BEGIN clears both the prior ABORT and expired-upload
markers; those older identifiers then return `NOT_FOUND`. This never cancels a
later upload. Successful COMMIT cannot be undone
by ABORT; a new ABORT request for that consumed upload returns `NOT_FOUND`.
All commands also inherit the bounded WMP request-ID response cache: an identical
cached request replays its original ACK/NACK; changed payload for that cached ID
returns `DUPLICATE_REQUEST_MISMATCH`. Once evicted, an old mutation must not be
treated as an unlimited idempotency token. RESET with an old base revision
returns `GENERATION_CONFLICT`, rather than incrementing again.

Resource version conflicts use the existing error code 10 with
`error.details.current_revision` (not `current_generation`):

```json
{"command":"SCREEN_ICON_RESET","error":{"code":10,"name":"GENERATION_CONFLICT","message":"base_revision does not match","details":{"current_revision":8}}}
```

Other existing errors are `VALIDATION_FAILED` (9), `BUSY` (11),
`STORAGE_FAILURE` (12), `NOT_FOUND` (13) and `TIMEOUT` (14); no new error codes
are introduced. Invalid fields are rejected without changing active state.

After COMMIT has been sent, cancellation can no longer promise rollback. If
COMMIT or RESET's response is lost, the outcome is **unknown**, not failure or
cancellation. Do not replay either mutation across reconnect. Reauthenticate
the same device, GET its metadata, and for a custom candidate READ all chunks
at that revision and compare the 32768 bytes directly. For RESET, default
source with zero total bytes confirms the current result; do not READ it.
Mismatch shows the actual device resource and permits an explicit new upload.

### Example exchange and display scope

Every request uses its command message type and a nonzero WMP request ID;
ACK/NACK echo that ID using type 0x7E/0x7F and flags 0x01/0x03. For example:

```json
{"request":{"type":80,"request_id":1,"payload":{}},"ack":{"command":"SCREEN_ICON_GET","result":{"revision":0,"source":"default","target":"normal_home","format":"rgb565_le","width":128,"height":128,"total_bytes":0}}}
{"request":{"type":82,"request_id":2,"payload":{"base_revision":0,"target":"normal_home","format":"rgb565_le","width":128,"height":128,"total_bytes":32768}},"ack":{"command":"SCREEN_ICON_BEGIN","result":{"upload_id":1,"next_offset":0,"max_chunk_bytes":1024}}}
{"request":{"type":83,"request_id":3,"payload":{"upload_id":1,"offset":0,"data":"APjgBx8A"}},"ack":{"command":"SCREEN_ICON_DATA","result":{"next_offset":6}}}
```

Continue sequential DATA until 32768 bytes before COMMIT. The executable shared
`fixtures/screen-icon-v1.json` supplies a deterministic RGB image recipe, complete
32-chunk upload/readback expansion, request/result field bounds and negative
cases. The short six-byte wire example above is a byte-order example, not a
complete image and cannot be committed alone.

The device-level icon persists across Profiles, reboot and normal OTA; only
NORMAL idle HOME may display it. BOOT, CODEX/CC identifiers, mode/connection
feedback, menus, prompt wheel, battery, timers and alarms retain priority.
Successful replacement/reset redraws an already-idle NORMAL home; it does not
wake an off screen or interrupt another page. Full Factory Default clears the
icon through the existing reset flow without touching device identity.

## Independent point-grid icons (screen glyphs v1)

This extension is separate from the full-color `normal_home` image above.
Only `features.custom_glyph_icons=true` with
`screen_glyphs={"version":1,"format":"alpha8","count":28}` advertises it.
Clients must not send glyph commands to older firmware without this capability.

The stable IDs, dimensions, resource IDs and editability are listed in
`fixtures/screen-glyph-v1.json`. There are 26 editable icons; `warning` and
`error` remain readable but cannot be overwritten or reset by the client.
An ID identifies a reusable resource, not a page: changing it affects each
place that already uses that resource. Layout, text, state colors, timing and
interaction do not change. No page-sized overlays are introduced.

| Command | Exact request fields | Result |
| --- | --- | --- |
| `SCREEN_GLYPH_LIST` | `{}` | `version`, `format`, `icons` (ID/resource ID/width/height/editable) |
| `SCREEN_GLYPH_GET` | `id` | Effective pixels and metadata below |
| `SCREEN_GLYPH_SET` | `id`, `base_revision`, `data` | Effective pixels and metadata after persistence |
| `SCREEN_GLYPH_RESET` | `id`, `base_revision` | Built-in pixels and advanced revision |

GET/SET/RESET results have exactly `id`, `revision`, `source`, `width`,
`height`, `format`, `data`. `source` is `default` or `custom`; default GET also
returns pixels, so a client can show the actual built-in design. Pixel `data`
is strict padded RFC4648 base64 of `width*height` alpha8 bytes, row-major,
top-left first, with 0 transparent and 255 full state-color intensity. Maximum
size is 225 bytes. Original row-mask off-cells are zero; on-cells use the
original level or 255. Fixed grid dimensions are not the 128x128 LCD size.

Each ID has its own uint32 revision starting at zero. SET or RESET advances
only that ID; mismatched `base_revision` returns `GENERATION_CONFLICT` with
`details.current_revision`. No revision wrap is allowed. Unknown IDs, extra
fields, bad pixel length/base64 and read-only mutations return
`VALIDATION_FAILED`. Unsupported capability returns `NOT_FOUND`; absent HELLO
and concurrent config/OTA/calibration/home-image transactions return `BUSY`
for mutations. Persistent write/read failure returns `STORAGE_FAILURE`.

One atomic blob contains that ID's revision, source and pixels in the
`prompt_nvs` partition's separate `screen_glyph` namespace. Reset writes a
default record for that ID, not a namespace erase. Prompt records, ordinary
configuration, device identity and `screen_icon` are untouched. Existing full
Factory Default clears this namespace too. No partition/eFuse change is needed.

SET/RESET acknowledge only after persistent readback; the client additionally
GETs the same ID and compares the actual pixels. A lost ACK is an unknown
outcome: GET resolves it; never blindly replay a SET across reconnects. A
failed write may have persisted an atomic new blob; reload/read actual state
before a new mutation. The display task copies changed RAM snapshots without
waiting for flash writes and uses the previous frame while a writer is busy.

## Fixture rules

- `fixtures/manifest.json` is the inventory and expected result source.
- `*-frame.json` carries payload text and complete expected frame hex.
- Positive config fixtures must pass `config-schema.json` and use the digest in
  the manifest.
- Negative fixtures identify the stable error name expected from both sides.
- Both implementations must reproduce the same frame bytes, canonical config
  bytes, CRC32, and SHA-256 values before changing this contract.
