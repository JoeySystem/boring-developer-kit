# Wired Macro Pad shared protocol v1

Status: frozen for development prototypes

Contract version: `1.0.0`  
Wire protocol: `1.0`  
Configuration schema: `1`

This directory is the executable shared contract between firmware and desktop
software. The Vault plans explain intent; this file and its fixtures lock the
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
oversized payloads, invalid UTF-8/JSON, and CRC mismatch. Within the device's
bounded replay window, a duplicate request ID with the same message type and
identical payload returns the exact cached response. Reusing a cached request
ID with different content returns `DUPLICATE_REQUEST_MISMATCH`. Engineering
Alpha retains up to four recent responses within a 24 KiB total response budget
and evicts the least-recently-used entry first.

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
| `0x28` | `SET_AGENT_STATUS` | Replace six Claude Code status slots in RAM and renew a 5-second lease |
| `0x29` | `CLEAR_AGENT_STATUS` | Clear the Claude Code status source, idempotently |
| `0x2A` | `NORMAL_AGENT_BEHAVIOR_GET` | Read the device-wide NORMAL Agent-key choice |
| `0x2B` | `NORMAL_AGENT_BEHAVIOR_SET` | Persist and apply the device-wide NORMAL Agent-key choice |
| `0x2C` | `SET_CODEX_USAGE` | Show a volatile Codex quota snapshot on the idle MIST screen |
| `0x2D` | `CLEAR_CODEX_USAGE` | Clear the volatile Codex quota snapshot |
| `0x30` | `FACTORY_DEFAULT` | Current `base_generation` and confirmation token |
| `0x40` | `FW_BEGIN` | Declare image identity, version, size and SHA-256 |
| `0x41` | `FW_DATA` | Sequential base64 image chunk and byte offset |
| `0x42` | `FW_STATUS` | Empty object; returns update and rollback state |
| `0x43` | `FW_END` | Verify the received image and schedule reboot |
| `0x44` | `FW_ABORT` | Discard an in-progress receive transaction |
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
| 6 | `UNSUPPORTED_PROTOCOL` | Protocol major is incompatible |
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

## Handshake

`HELLO` request fields are `client.name`, `client.version`,
`protocol.min_major`, `protocol.max_major`, and `protocol.max_minor`. The ACK
fixture locks the response shape. It includes USB/protocol identity, product
firmware version, exact `build_id`, schema, generation, active digest, and
compatibility (`read`, `write`, reason). Clients use `build_id`, not the product
version alone, to confirm that a requested development package is running.

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
operating mode. `features.claude_code_mode` is true when the Matrix12 target
provides the fixed, platform-aware Claude Code shortcut layer.
`features.claude_code_status` is a separate capability: true on Matrix12
Codex USB targets supporting the USB CDC Claude Code status bridge below.
Do not infer it from `claude_code_mode`. Old firmware without this capability
must not receive the new status commands.
`GET_STATUS.result.operating_mode` reports `normal`, `codex`, `eda`, or
`claude_code` on every build, including targets compiled without Codex
transport.

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

### Codex quota on the MIST screen

`CAPABILITIES.features.codex_usage_display` is `true` only on a MIST Matrix12
Power V2 build implementing this extension. A Console must check the capability
before sending these commands. Existing official firmware does not advertise it.

`SET_CODEX_USAGE` (0x2C) accepts exactly:

```json
{"source":"codex","weekly_remaining":94,"five_hour_remaining":null}
```

Both percentages are integers from 0 through 100 or `null` when unavailable.
The command displays the remaining percentages on the idle NORMAL or CODEX
HOME page. Other pages, Claude Code mode, and active animations retain their
existing rendering. The data is held
only in RAM, expires 600 seconds after the last accepted SET, and is cleared on
protocol session reset or reboot. Each SET needs a fresh request ID to renew
the lease. `CLEAR_CODEX_USAGE` (0x2D) accepts exactly `{"source":"codex"}` and
clears the display idempotently. Both commands ACK with
`{"active":true|false,"lease_ms":600000}` in `result`. Unsupported targets
return `READ_ONLY`; invalid payloads return `VALIDATION_FAILED`.

The Console checks the local Codex app-server every 60 seconds, sends a full
snapshot when values change and every 120 seconds while enabled, connected,
and the app-server has fresh data. It sends
CLEAR on disable, unavailable data, and orderly exit. This feature does not
write device settings or change the Codex input transport. It works over the
Console's authenticated USB or BLE configuration connection.

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
snapshots, unknown states, and any other source return `VALIDATION_FAILED`.
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
never Codex fallback. NORMAL/CODEX preserve existing Codex status behavior;
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

`GET_STATUS.result.battery` optionally reports the latest fuel-gauge SOC rounded
to an integer percentage (0–100). `battery_valid` is true only when that sample
is valid; otherwise `battery` is null. Older firmware may omit these fields.
The handler reads the existing cached sample, without a new I2C poll or a
separate USB/BLE connection. `is_charging` is null on the current board because
the MCU has no verified charger-status signal. Neither USB host connectivity
nor a 100 percent SOC value establishes whether charge current is flowing.

`GET_STATUS.result.active_controls` is a unique array of the stable control IDs
currently held or classified as active. It is empty when all physical controls
are released. `GET_STATUS.result.action_engine` reports the read-only host-output
state, local device page and local quick-config flags. These fields are for live
diagnosis and do not stage or activate configuration.

Targets with an analog joystick add the optional read-only
`GET_STATUS.result.joystick_diagnostics` object. It contains `raw_x`, `raw_y`,
`filtered_x`, `filtered_y`, `center_x`, `center_y`, `minimum_x`, `maximum_x`,
`minimum_y`, `maximum_y`, `deadzone_x`, `deadzone_y`, `directions`,
`radial_active`, `radial_valid`, and `radial_angle_turns`. `directions` is a
bit mask: left `0x01`, right `0x02`, up `0x04`, down `0x08`. Reading these
values never starts calibration and never changes the active configuration.
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
ordinary HID key press and release over the active keyboard transport; they
do not hold a modifier across clicks. Existing ordinary `key` actions retain
their normal press/release behavior.

The example uses F13 for a separately associated voice tool and Enter for
the focused application's send action. It does not configure Typeless or
prove that the application accepts either shortcut. Firmware does not track
recording or transcription state, wait for transcription, or focus Codex.
The user starts speech with one click, ends it with another, waits until the
text is visible in the intended Codex input, and then double-clicks to send.
Double-clicking while speech is active is not an end-and-send operation.

### CODEX voice key choice (Matrix12)

Firmware advertising `CAPABILITIES.result.features.codex_voice=true` accepts an
optional `codex_voice` action on each profile. It must be a `key_gesture`; for
example `"codex_voice":{"type":"key_gesture","usage":228,"double_usage":40}`.
Omitting this field selects the existing official Codex dictation action (ACT10).
There is no change to normal `mappings`, other official controls, or CC mode.
Rev A and assembly-alignment firmware do not support this field.

Only CODEX key 8 uses this override. It uses the existing single/double keyboard
pulse routing and USB/BLE transport, without requiring the official Codex agent
link. Mode changes, transport loss, pending config activation, and device-local
input ownership cancel pending gestures through the existing reset paths.
Switching back to official dictation removes `codex_voice` from that profile.

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
`maximum`, and `travel_complete`. The configurator samples repeatedly while the
user moves the stick around its full edge, then asks the user to release it back
to center. `CALIBRATION_CONFIRM` uses:

```json
{"session_id":1,"base_generation":8}
```

Confirmation is rejected until both sides of both axes have sufficient travel
and the current raw sample is back inside the calculated deadzone. A successful
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
  "last_pressed": true
}
```

`last_control` uses the stable control IDs already returned by `CAPABILITIES`.
Before the first event, `last_control` and `last_pressed` are both `null`.
Every accepted physical edge increments `event_sequence`. Repeating
`DIAGNOSTIC_START {}` renews the three-second lease without clearing the
sequence or latest event.

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
and reboots after a short ACK grace period. Bootloader rollback remains armed
until the new application initializes the board, configuration store, action
engine, USB stack and Codex services. A crash before that confirmation returns
to the previous application slot. Removing power during transfer leaves the
previous slot selected; the next update starts the inactive slot again.

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
