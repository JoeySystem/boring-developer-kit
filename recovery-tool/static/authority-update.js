"use strict";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));
const terminalJobStatuses = new Set(["passed", "failed", "action-required", "retryable"]);
const activeRepairPhases = new Set(["ota", "post-ota", "calibrating", "verifying-joystick"]);
const cancellableCalibrationPhases = new Set(["calibrating", "readback-needed", "direction-verification"]);
const MAX_POLL_FAILURES = 5;
// Same threshold the firmware uses to accept one side of travel
// (JOYSTICK_CALIBRATION_MIN_SIDE_TRAVEL in joystick_calibration_model.h).
const MIN_SIDE_TRAVEL = 400;
// Raw ADC distance drawn at the edge of the ring.
const STICK_DRAW_RANGE = 1800;
const STICK_RADIUS_PX = 120;
const DIRECTIONS = [
  { key: "up", label: "上", bit: 1 << 2 },
  { key: "down", label: "下", bit: 1 << 3 },
  { key: "left", label: "左", bit: 1 << 0 },
  { key: "right", label: "右", bit: 1 << 1 },
];
const CHECK_SVG = '<svg viewBox="0 0 24 24"><polyline points="5 12.5 10 17.5 19 7"></polyline></svg>';

const state = {
  token: "", authority: null, devices: [], readiness: "waiting", blockReason: null,
  scanMessage: "", repair: null, job: null, calibration: null, eventCursor: 0,
  pollTimer: null, scanTimer: null, calibrationTimer: null, clockTimer: null,
  pollFailures: 0, stalled: false,
};

// ---------- helpers ----------

// Two-step confirmation for destructive buttons: the first click arms the
// button for 3 s, the second click runs the action. Keyboard activation is
// ignored so a stray Enter/Space cannot trigger it.
function armedClick(button, armedLabel, action) {
  return (event) => {
    if (event.detail === 0) return;
    if (button.dataset.armed === "1") {
      clearTimeout(Number(button.dataset.armTimer));
      button.dataset.armed = "";
      button.classList.remove("armed");
      button.textContent = button.dataset.label;
      action();
      return;
    }
    button.dataset.label = button.textContent;
    button.dataset.armed = "1";
    button.classList.add("armed");
    button.textContent = typeof armedLabel === "function" ? armedLabel() : armedLabel;
    button.dataset.armTimer = String(setTimeout(() => {
      button.dataset.armed = "";
      button.classList.remove("armed");
      button.textContent = button.dataset.label;
    }, 3000));
  };
}

function formatDuration(totalSeconds = 0) {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

function formatSeconds(value) {
  return typeof value === "number" ? `${value.toFixed(1)} 秒` : "—";
}

function firmwareVersion() {
  const buildId = state.authority?.build_id || "";
  return buildId.match(/^\d{8}\.\d+/)?.[0] || buildId || "—";
}

function repairElapsedSeconds(repair) {
  if (typeof repair?.timings_s?.total_s === "number") return repair.timings_s.total_s;
  const started = Date.parse(repair?.started_at || "");
  return Number.isNaN(started) ? state.job?.elapsed_seconds || 0 : (Date.now() - started) / 1000;
}

function fillDetails(container, rows) {
  container.replaceChildren(...rows.flatMap(([label, value]) => {
    const term = document.createElement("dt");
    const description = document.createElement("dd");
    term.textContent = label;
    description.textContent = value ?? "—";
    return [term, description];
  }));
}

function fillTiles(container, rows) {
  container.replaceChildren(...rows.map(([label, value]) => {
    const tile = document.createElement("div");
    const term = document.createElement("dt");
    const description = document.createElement("dd");
    term.textContent = label;
    description.textContent = value ?? "—";
    tile.append(term, description);
    return tile;
  }));
}

function setItemState(element, itemState) {
  element.dataset.state = itemState;
  const mark = element.querySelector(":scope > .mark");
  if (mark) mark.innerHTML = itemState === "done" ? CHECK_SVG : "";
}

function timingRows(repair) {
  const timings = repair?.timings_s || {};
  return [
    ["固件更新（含重启读回）", formatSeconds(timings.ota_s)],
    ["清空蓝牙槽位", formatSeconds(timings.slot_check_and_clear_s)],
    ["摇杆行程采集", formatSeconds(timings.calibration_capture_s)],
    ["校准保存与读回", formatSeconds(timings.calibration_save_readback_s)],
    ["四向验收", formatSeconds(timings.direction_verification_s)],
    ["总耗时", formatSeconds(timings.total_s)],
    ["记录文件", repair?.session_receipt_path || "未写入"],
  ];
}

async function request(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.method && options.method !== "GET") {
    headers["Content-Type"] = "application/json";
    headers["X-Authority-Updater-Token"] = state.token;
  }
  const response = await fetch(path, { ...options, headers, cache: "no-store" });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `请求失败（${response.status}）`);
  return data;
}

function showError(message = "") {
  const banner = $("#errorBanner");
  banner.hidden = !message;
  banner.textContent = message;
}

function repairActive() {
  return Boolean(state.repair && activeRepairPhases.has(state.repair.phase));
}

// ---------- which step and view ----------

function currentStep() {
  const phase = state.repair?.phase;
  if (!phase) return 1;
  if (phase === "ota" || phase === "post-ota") return 2;
  if (phase === "calibrating" || phase === "verifying-joystick") return 3;
  if (phase === "completed") return 4;
  // failed: the server keeps the progress it reached (ota < 65 <= calibration).
  return (Number(state.repair.progress) || 0) >= 65 ? 3 : 2;
}

function renderSteps() {
  const step = currentStep();
  const failed = state.repair?.phase === "failed";
  const completed = state.repair?.phase === "completed";
  $$("#steps li").forEach((item) => {
    const number = Number(item.dataset.step);
    let itemState = "pending";
    if (number < step || (completed && number === 4)) itemState = "done";
    else if (number === step) itemState = failed ? "failed" : "current";
    item.className = itemState;
    const mark = item.querySelector(".num");
    mark.innerHTML = itemState === "done" ? CHECK_SVG : String(number);
    if (itemState === "current") item.setAttribute("aria-current", "step");
    else item.removeAttribute("aria-current");
  });
}

function showView(id) {
  ["viewConnect", "viewFirmware", "viewCalibrate", "viewDone", "viewFailed"].forEach((view) => {
    $(`#${view}`).hidden = view !== id;
  });
  const stage = $("#stage");
  stage.classList.toggle("success", id === "viewDone");
  stage.classList.toggle("failure", id === "viewFailed");
}

// ---------- views ----------

function renderHeader() {
  $("#testBadge").hidden = state.authority?.acceptance_test !== true;
  const chip = $("#deviceChip");
  const runtime = state.devices.find((device) => device.kind === "runtime");
  let chipState = state.readiness;
  let text = "未连接";
  if (state.repair?.serial) {
    chipState = "ready";
    text = state.repair.serial;
  } else if (state.readiness === "ready" && runtime) {
    text = `已连接 · ${runtime.device}`;
  } else if (state.readiness === "blocked") {
    text = "无法开始";
  }
  chip.className = `device-chip ${chipState}`;
  $("#deviceChipText").textContent = text;
  $("#deviceChipText").classList.toggle("mono", Boolean(state.repair?.serial));
  $("#exitButton").disabled = Boolean(state.repair?.phase === "ota" || state.job?.status === "running");
}

function renderConnect() {
  const view = $("#viewConnect");
  const runtime = state.devices.find((device) => device.kind === "runtime");
  view.classList.toggle("waiting", state.readiness === "waiting");
  view.classList.toggle("blocked", state.readiness === "blocked");
  $("#connectTitle").textContent = state.readiness === "ready"
    ? "已识别 1 台 BORING MIST"
    : state.readiness === "blocked" ? "暂时无法开始" : "请连接 BORING MIST";
  $("#connectMessage").textContent = state.readiness === "ready"
    ? "点击“开始更新”后，工具会依次自动完成下面三件事，期间请保持 USB 连接。"
    : state.scanMessage || "请将 BORING MIST 正常开机，并用支持数据传输的 USB 线连接。工具会自动识别。";
  $("#devicePort").textContent = runtime ? `${runtime.device} · ${runtime.vid_pid}` : "—";
  $("#firmwareVersion").textContent = firmwareVersion();
  const validationLabels = {
    built: "未验证（验收测试）", "sample-verified": "已通过样机验证", rc: "候选版本", released: "正式版本",
  };
  fillDetails($("#techDetails"), [
    ["Build ID", state.authority?.build_id || "—"],
    ["固件状态", validationLabels[state.authority?.validation_state] || "—"],
    ["源码提交", state.authority?.source_commit || "—"],
    ["设备端口", runtime ? `${runtime.device} · ${runtime.vid_pid}` : "—"],
  ]);
}

function transferBytes() {
  const events = state.job?.allEvents || [];
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const match = events[index].message?.match(/正在传输固件 (\d+)\/(\d+) 字节/);
    if (match) return { current: Number(match[1]), total: Number(match[2]) };
  }
  return null;
}

// What the operator must (not) do at each firmware stage.
const FIRMWARE_GUIDANCE = {
  transfer: { tone: "warn", lead: "不需要操作，等待自动完成即可。",
    text: "正在传输固件，请勿拔线。写入期间窗口无法关闭。" },
  readback: { tone: "warn", lead: "设备正在重启，通常 5–15 秒。",
    text: "请勿拔线。设备重启后工具会自动读回版本。" },
  replug: { tone: "action", lead: "固件已经写入，只差重新识别设备。",
    text: "请拔下 USB 线，等 2 秒再插回。工具会自动继续，不需要重新烧录。" },
  slots: { tone: "warn", lead: "不需要操作，等待自动完成即可。",
    text: "正在清空蓝牙槽位，请勿拔线。" },
  "voice-key": { tone: "warn", lead: "不需要操作，请勿按键或拨动摇杆。",
    text: "正在把第 8 键设为单击说话、双击发送，请勿拔线。" },
};

function renderFirmware() {
  const repair = state.repair;
  const job = state.job || {};
  const postOta = repair.phase === "post-ota";
  const replug = !postOta && job.stage === "replug";
  const readingBack = !postOta && (job.stage === "verify" || replug);
  const percent = postOta ? 95 : Math.round(Math.min(100, Number(job.progress) || 0) * 0.9);
  $("#firmwareTitle").textContent = postOta
    ? (repair.step === "voice-key" ? "正在设置第 8 键"
      : repair.ota_skipped ? "已是目标固件，正在清空蓝牙槽位" : "正在清空蓝牙槽位")
    : replug ? "请拔下 USB 再插回"
    : readingBack ? "正在重启并读回版本" : "正在写入固件";
  const guidance = FIRMWARE_GUIDANCE[
    postOta ? (repair.step === "voice-key" ? "voice-key" : "slots") : replug ? "replug" : readingBack ? "readback" : "transfer"];
  $("#firmwareLead").textContent = guidance.lead;
  $("#firmwareCallout").className = `callout ${guidance.tone}`;
  $("#firmwareCalloutText").textContent = guidance.text;
  $("#firmwarePercent").textContent = `${percent}%`;
  $("#firmwareFill").style.width = `${percent}%`;
  $("#elapsedTime").textContent = `已用时 ${formatDuration(repairElapsedSeconds(repair))}`;
  const bytes = transferBytes();
  $("#transferBytes").textContent = bytes
    ? `${Math.round(bytes.current / 1024)} / ${Math.round(bytes.total / 1024)} KB` : "";
  const items = Object.fromEntries($$("#firmwareSteps li").map((item) => [item.dataset.key, item]));
  setItemState(items.transfer, postOta || readingBack ? "done" : "active");
  setItemState(items.readback, postOta ? "done" : readingBack ? "active" : "pending");
  setItemState(items.slots, postOta ? "active" : "pending");
  $("#eventList").replaceChildren(...(job.allEvents || []).slice(-12).map((event) => {
    const item = document.createElement("li");
    item.textContent = event.message;
    return item;
  }));
}

function sideTravel(sample, orientation) {
  const center = sample?.center;
  const minimum = sample?.minimum;
  const maximum = sample?.maximum;
  if (!center || !minimum || !maximum) return null;
  const flipX = orientation?.flip_x !== false;
  const flipY = orientation?.flip_y !== false;
  return {
    left: flipX ? maximum.x - center.x : center.x - minimum.x,
    right: flipX ? center.x - minimum.x : maximum.x - center.x,
    up: flipY ? maximum.y - center.y : center.y - minimum.y,
    down: flipY ? center.y - minimum.y : maximum.y - center.y,
  };
}

function placeDot(dx, dy) {
  const dot = $("#stickDot");
  const length = Math.hypot(dx, dy);
  const scale = length > 1 ? 1 / length : 1;
  dot.style.transform = `translate(${dx * scale * STICK_RADIUS_PX}px, ${dy * scale * STICK_RADIUS_PX}px)`;
}

function setDirectionStates(states) {
  DIRECTIONS.forEach(({ key }) => {
    $(`.dir-chip[data-dir="${key}"]`).dataset.state = states[key];
    setItemState($(`#calDirections li[data-dir="${key}"]`), states[key]);
  });
}

function renderCalibrate() {
  const calibration = state.calibration;
  const verifying = state.repair?.phase === "verifying-joystick"
    || calibration?.phase === "direction-verification";
  const saving = ["confirming", "readback-needed"].includes(calibration?.phase);
  const pills = Object.fromEntries($$("#calPills li").map((item) => [item.dataset.key, item]));
  pills.capture.dataset.state = verifying || saving ? "done" : "active";
  pills.save.dataset.state = verifying ? "done" : saving ? "active" : "pending";
  pills.verify.dataset.state = verifying ? "active" : "pending";
  $("#stick").classList.remove("idle");

  if (!calibration) {
    $("#calTitle").textContent = "正在准备校准";
    $("#calHint").textContent = "请松开摇杆，稍候。";
    setDirectionStates({ up: "pending", down: "pending", left: "pending", right: "pending" });
    $("#calReturn").hidden = true;
    $("#calRaw").textContent = "原始读数 —";
    $("#stick").classList.add("idle");
    placeDot(0, 0);
    return;
  }

  if (verifying) {
    const verification = calibration.directionVerification || {};
    const completed = new Set(verification.completed || []);
    const next = verification.next_direction;
    const states = {};
    DIRECTIONS.forEach(({ key }) => {
      states[key] = completed.has(key) ? "done" : key === next ? "active" : "pending";
    });
    setDirectionStates(states);
    const nextLabel = DIRECTIONS.find((item) => item.key === next)?.label;
    $("#calTitle").textContent = verification.complete
      ? "四向验收通过"
      : verification.awaiting_neutral ? "松手回中" : nextLabel ? `推向${nextLabel}` : "依次推向上、下、左、右";
    $("#calHint").textContent = verification.message
      || "推到底后松手回中，每个方向一次。";
    $("#calReturn").hidden = true;
    const bits = Number(verification.directions) || 0;
    const dx = (bits & 2 ? 1 : 0) - (bits & 1 ? 1 : 0);
    const dy = (bits & 8 ? 1 : 0) - (bits & 4 ? 1 : 0);
    placeDot(dx * 0.9, dy * 0.9);
    $("#calRaw").textContent = calibration.idleNeutralPassed === false
      ? "静止检查未通过" : "静止检查通过";
    $("#calNoteText").textContent = "验收期间原 Profile 动作已暂停，摇杆不会向电脑发送按键。";
    return;
  }

  if (saving) {
    $("#calTitle").textContent = calibration.phase === "confirming" ? "正在保存并读回" : "正在只读核对";
    $("#calHint").textContent = calibration.phase === "confirming"
      ? "请松手，手离开摇杆，正在保存并做静止检查。"
      : "保存回包中断，正在只读核对结果，不会重复保存。";
    setDirectionStates({ up: "done", down: "done", left: "done", right: "done" });
    setItemState($("#calReturn"), "done");
    $("#calReturn").hidden = false;
    $("#stick").classList.add("idle");
    placeDot(0, 0);
    return;
  }

  const sample = calibration.sample || {};
  const travel = sideTravel(sample, calibration.orientation);
  const reached = Object.fromEntries(DIRECTIONS.map(({ key }) => [
    key, Boolean(sample.travel_complete || (travel && travel[key] >= MIN_SIDE_TRAVEL)),
  ]));
  const next = DIRECTIONS.find(({ key }) => !reached[key]);
  const capturing = sample.state === "CAPTURING";
  const states = {};
  DIRECTIONS.forEach(({ key }) => {
    states[key] = reached[key] ? "done" : capturing && next?.key === key ? "active" : "pending";
  });
  setDirectionStates(states);

  const returnRow = $("#calReturn");
  returnRow.hidden = false;
  setItemState(returnRow, sample.ready_to_confirm ? "done" : sample.travel_complete ? "active" : "pending");

  if (!capturing) {
    $("#calTitle").textContent = "先松开摇杆";
    $("#calHint").textContent = "设备正在记录中心位置，请不要碰摇杆。";
  } else if (sample.ready_to_confirm) {
    $("#calTitle").textContent = "采集完成";
    $("#calHint").textContent = "正在自动保存，请松手，手离开摇杆。";
  } else if (sample.travel_complete) {
    $("#calTitle").textContent = "再推开一次，然后松手";
    $("#calHint").textContent = "向任意方向推开再松手，静置约 1 秒不要碰。";
  } else {
    $("#calTitle").textContent = `推到最${next.label}边`;
    $("#calHint").textContent = "推到底后松手，让摇杆自己回中，停约 1 秒。";
  }

  const raw = sample.raw;
  const center = sample.center || { x: 2048, y: 2048 };
  if (raw) {
    const flipX = calibration.orientation?.flip_x !== false;
    const flipY = calibration.orientation?.flip_y !== false;
    const dx = ((raw.x - center.x) * (flipX ? -1 : 1)) / STICK_DRAW_RANGE;
    const dy = ((raw.y - center.y) * (flipY ? -1 : 1)) / STICK_DRAW_RANGE;
    placeDot(dx, dy);
    $("#calRaw").textContent = `原始读数 X ${raw.x} · Y ${raw.y}`;
  } else {
    placeDot(0, 0);
    $("#calRaw").textContent = "原始读数 —";
  }
  $("#calNoteText").textContent = "采集完成后会自动保存并读回，然后进入四向验收。验收期间摇杆不会向电脑发送按键。";
}

function renderDone() {
  const repair = state.repair;
  const total = repair.timings_s?.total_s;
  $("#doneSummary").textContent = typeof total === "number"
    ? `固件、蓝牙槽位和摇杆验收全部通过 · 总耗时 ${formatDuration(total)}`
    : "固件、蓝牙槽位和摇杆验收全部通过";
  const cleared = repair.cleared_slots || [];
  fillTiles($("#doneDetails"), [
    ["设备序列号", repair.serial],
    ["固件", repair.ota_skipped ? `${firmwareVersion()} · 原本已是目标版本` : `${firmwareVersion()} · 已读回`],
    ["蓝牙槽位", cleared.length ? `已清空槽位 ${cleared.join("、")}` : "原本为空，无需清空"],
    ["第 8 键", repair.voice_key?.changed ? "已设为单击说话、双击发送" : "原本已是单击说话、双击发送"],
    ["摇杆", "静止及上下左右通过"],
  ]);
  fillDetails($("#doneTimings"), timingRows(repair));
}

function renderFailed() {
  const repair = state.repair;
  const stepLabels = { slots: "清空蓝牙槽位", "voice-key": "设置第 8 键", "calibration-start": "进入摇杆校准" };
  $("#failedStage").textContent = `停在：${stepLabels[repair.step]
    || (currentStep() === 3 ? "校准摇杆" : "更新固件")}`;
  $("#failedMessage").textContent = repair.message || "未知原因";
  const events = (state.job?.allEvents || []).slice(-15);
  $("#failedLog").replaceChildren(...(events.length ? events : [{ message: "没有记录到操作日志" }])
    .map((event) => {
      const item = document.createElement("li");
      const time = event.time ? new Date(event.time).toLocaleTimeString("zh-CN", { hour12: false }) : "";
      item.textContent = time ? `${time}  ${event.message}` : event.message;
      return item;
    }));
  fillDetails($("#failedTimings"), timingRows(repair));
}

function renderFooter() {
  const button = $("#primaryButton");
  const hint = $("#primaryHint");
  const phase = state.repair?.phase;
  const calibration = state.calibration;
  $("#cancelCalibrationButton").hidden = !(calibration && cancellableCalibrationPhases.has(calibration.phase));

  if (phase === "completed") {
    button.disabled = false;
    button.textContent = "更新下一台";
    hint.textContent = "拔下这台，接入下一台后点击。全部完成后点右上角“退出”。";
  } else if (phase === "failed") {
    button.disabled = false;
    button.textContent = "重新开始";
    hint.textContent = "重新开始会从固件更新做起。";
  } else if (phase === "ota" || phase === "post-ota") {
    button.disabled = true;
    button.textContent = "正在更新…";
    hint.textContent = "固件写入完成后会自动进入摇杆校准。";
  } else if (phase === "calibrating" || phase === "verifying-joystick") {
    button.disabled = true;
    const verifying = phase === "verifying-joystick";
    button.textContent = verifying ? "四个方向完成后自动结束" : "采集完成后自动保存";
    hint.textContent = state.stalled ? "读取中断，请检查 USB 后取消校准。" : "";
  } else if (state.readiness === "ready") {
    button.disabled = false;
    button.textContent = "开始更新";
    hint.textContent = "一次只连接 1 台设备。点击即确认上面的操作范围。";
  } else {
    button.disabled = true;
    button.textContent = state.blockReason === "port-busy" ? "等待串口释放" : "等待设备";
    hint.textContent = "一次只连接 1 台设备。";
  }
}

function render() {
  renderHeader();
  renderSteps();
  const phase = state.repair?.phase;
  if (!phase) {
    renderConnect();
    showView("viewConnect");
  } else if (phase === "ota" || phase === "post-ota") {
    renderFirmware();
    showView("viewFirmware");
  } else if (phase === "calibrating" || phase === "verifying-joystick") {
    renderCalibrate();
    showView("viewCalibrate");
  } else if (phase === "completed") {
    renderDone();
    showView("viewDone");
  } else {
    renderFailed();
    showView("viewFailed");
  }
  renderFooter();
}

// ---------- server state ----------

function calibrationFromServer(calibration) {
  if (!calibration) return null;
  return {
    phase: calibration.phase,
    port: calibration.port,
    serial: calibration.serial,
    sessionId: calibration.session_id,
    baseGeneration: calibration.base_generation,
    sample: calibration.sample,
    activation: calibration.activation,
    idleNeutralPassed: calibration.idle_neutral_passed,
    directionVerification: calibration.direction_verification,
    receiptPath: calibration.receipt_path,
    confirmationError: calibration.confirmation_error,
    orientation: calibration.orientation,
  };
}

function applyBootstrap(data, { keepEvents }) {
  state.authority = data.authority;
  state.devices = data.devices || state.devices;
  state.readiness = data.readiness || state.readiness;
  state.blockReason = data.block_reason || null;
  state.scanMessage = data.message || state.scanMessage;
  state.repair = data.repair;
  state.calibration = calibrationFromServer(data.calibration);
  if (data.latest_job) {
    const previous = keepEvents ? state.job?.allEvents || [] : [];
    state.job = { ...data.latest_job, allEvents: previous.concat(data.latest_job.events || []) };
    state.eventCursor = data.latest_job.event_cursor || state.eventCursor;
  }
}

async function restoreServerState() {
  applyBootstrap(await request("/api/authority-update/bootstrap"), { keepEvents: true });
}

async function safeRestore() {
  try {
    await restoreServerState();
  } catch (error) {
    showError(error.message);
  }
}

function recordPollFailure(error) {
  state.pollFailures += 1;
  if (state.pollFailures < MAX_POLL_FAILURES) return false;
  clearTimeout(state.calibrationTimer);
  state.stalled = true;
  showError(
    `连续 ${state.pollFailures} 次读取设备失败：${error.message}。`
    + "请检查 USB 连接，然后点“取消校准”，再重新开始。",
  );
  render();
  return true;
}

// ---------- calibration polling ----------

function calibrationPayload() {
  if (!state.calibration) return null;
  return {
    port: state.calibration.port || state.repair?.port,
    expected_serial: state.calibration.serial,
    session_id: state.calibration.sessionId,
  };
}

function scheduleCalibrationSample(delay = 250) {
  clearTimeout(state.calibrationTimer);
  state.calibrationTimer = setTimeout(sampleCalibration, delay);
}

function scheduleDirectionVerification(delay = 250) {
  clearTimeout(state.calibrationTimer);
  state.calibrationTimer = setTimeout(sampleDirectionVerification, delay);
}

async function sampleCalibration() {
  const payload = calibrationPayload();
  if (!payload || state.calibration.phase !== "calibrating") return;
  try {
    const data = await request("/api/authority-update/calibration/sample", {
      method: "POST", body: JSON.stringify(payload),
    });
    state.pollFailures = 0;
    state.calibration.sample = data.sample;
    render();
    if (data.sample.ready_to_confirm) {
      await confirmCalibration();
    } else {
      scheduleCalibrationSample(data.sample.owner_state === "acquiring" ? 250 : 120);
    }
  } catch (error) {
    showError(error.message);
    await safeRestore();
    render();
    if (state.calibration?.phase === "calibrating" && !recordPollFailure(error)) {
      scheduleCalibrationSample(600);
    }
  }
}

async function confirmCalibration() {
  const payload = calibrationPayload();
  if (!payload || !state.calibration.sample?.ready_to_confirm) return;
  clearTimeout(state.calibrationTimer);
  state.calibration.phase = "confirming";
  render();
  try {
    await request("/api/authority-update/calibration/confirm", {
      method: "POST", body: JSON.stringify(payload),
    });
    await restoreServerState();
    render();
    scheduleDirectionVerification();
  } catch (error) {
    await safeRestore();
    render();
    if (state.calibration?.phase === "readback-needed") {
      await reconcileCalibration();
    } else {
      showError(error.message);
    }
  }
}

async function reconcileCalibration() {
  const payload = calibrationPayload();
  if (!payload || state.calibration?.phase !== "readback-needed") return;
  try {
    const data = await request("/api/authority-update/calibration/reconcile", {
      method: "POST", body: JSON.stringify(payload),
    });
    await restoreServerState();
    render();
    if (data.outcome !== "not-saved" && state.calibration?.phase === "direction-verification") {
      scheduleDirectionVerification();
    }
  } catch (error) {
    showError(error.message);
    await safeRestore();
    render();
  }
}

async function sampleDirectionVerification() {
  const payload = calibrationPayload();
  if (!payload || state.calibration.phase !== "direction-verification") return;
  try {
    const data = await request("/api/authority-update/calibration/verify-direction", {
      method: "POST", body: JSON.stringify(payload),
    });
    state.pollFailures = 0;
    showError();
    if (data.direction_verification.complete) {
      await restoreServerState();
      render();
      scheduleScan();
    } else {
      state.calibration.directionVerification = data.direction_verification;
      state.calibration.receiptPath = data.receipt_path;
      render();
      scheduleDirectionVerification();
    }
  } catch (error) {
    showError(error.message);
    if (!recordPollFailure(error)) scheduleDirectionVerification(600);
  }
}

async function cancelCalibration() {
  const payload = calibrationPayload();
  if (!payload) return;
  clearTimeout(state.calibrationTimer);
  try {
    await request("/api/authority-update/calibration/cancel", {
      method: "POST", body: JSON.stringify(payload),
    });
    showError();
  } catch (error) {
    showError(error.message);
  }
  state.stalled = false;
  state.pollFailures = 0;
  await safeRestore();
  render();
  scheduleScan();
}

// ---------- repair flow ----------

async function resetForNextDevice() {
  await request("/api/authority-update/repair/reset", { method: "POST", body: "{}" });
  showError();
  state.repair = null;
  state.calibration = null;
  state.job = null;
  state.eventCursor = 0;
  state.pollFailures = 0;
  state.stalled = false;
}

async function startRepair() {
  const device = state.devices.find((item) => item.kind === "runtime");
  if (!device || state.readiness !== "ready" || repairActive()) return;
  showError();
  $("#primaryButton").disabled = true;
  clearInterval(state.scanTimer);
  try {
    const data = await request("/api/authority-update/repair/start", {
      method: "POST", body: JSON.stringify({ port: device.device }),
    });
    state.repair = data.repair;
    state.calibration = null;
    state.pollFailures = 0;
    state.stalled = false;
    state.job = { id: data.repair.job_id, status: "queued", progress: 0, allEvents: [] };
    state.eventCursor = 0;
    render();
    pollJob();
  } catch (error) {
    showError(error.message);
    await scan();
    scheduleScan();
  }
}

async function advanceRepair() {
  const data = await request("/api/authority-update/repair/advance", {
    method: "POST", body: JSON.stringify({ job_id: state.repair.job_id }),
  });
  state.repair = data.repair;
  state.calibration = calibrationFromServer(data.calibration);
  render();
  if (state.calibration?.phase === "calibrating") scheduleCalibrationSample(250);
}

async function pollJob() {
  clearTimeout(state.pollTimer);
  if (!state.job) return;
  try {
    const data = await request(`/api/authority-update/jobs/${state.job.id}?after_event=${state.eventCursor}`);
    const previous = state.job.allEvents || [];
    state.job = {
      ...data,
      allEvents: data.events_reset ? (data.events || []) : previous.concat(data.events || []),
    };
    state.eventCursor = data.event_cursor || state.eventCursor;
    render();
    if (!terminalJobStatuses.has(data.status)) {
      state.pollTimer = setTimeout(pollJob, 500);
      return;
    }
    try {
      await advanceRepair();
    } catch (error) {
      showError(error.message);
      await safeRestore();
      render();
    }
    if (state.repair?.phase === "failed") scheduleScan();
  } catch (error) {
    showError(error.message);
    state.pollTimer = setTimeout(pollJob, 1200);
  }
}

async function scan() {
  if (repairActive()) return;
  try {
    const data = await request("/api/authority-update/scan");
    state.authority = data.authority;
    state.devices = data.devices || [];
    state.readiness = data.readiness;
    state.blockReason = data.block_reason || null;
    state.scanMessage = data.message || "";
    render();
  } catch (error) {
    showError(error.message);
  }
}

function scheduleScan() {
  clearInterval(state.scanTimer);
  if (!repairActive()) state.scanTimer = setInterval(scan, 1500);
}

async function handlePrimaryAction() {
  const phase = state.repair?.phase;
  try {
    if (phase === "completed") {
      await resetForNextDevice();
      render();
      await scan();
      scheduleScan();
      return;
    }
    if (phase === "failed") {
      await resetForNextDevice();
      await scan();
      if (state.readiness === "ready") await startRepair();
      else scheduleScan();
      render();
      return;
    }
    await startRepair();
  } catch (error) {
    showError(error.message);
  }
}

async function exitTool() {
  clearTimeout(state.calibrationTimer);
  clearTimeout(state.pollTimer);
  clearInterval(state.scanTimer);
  clearInterval(state.clockTimer);
  try {
    await request("/api/authority-update/shutdown", { method: "POST", body: "{}" });
    document.body.innerHTML = '<main class="closed"><h1>工具已退出</h1><p>可以关闭此窗口。</p></main>';
  } catch (error) {
    showError(error.message);
  }
}

async function bootstrap() {
  try {
    const data = await request("/api/authority-update/bootstrap");
    state.token = data.token;
    applyBootstrap(data, { keepEvents: false });
    render();
    if (state.repair?.phase === "ota" && state.job) pollJob();
    else if (state.calibration?.phase === "calibrating") scheduleCalibrationSample(200);
    else if (state.calibration?.phase === "readback-needed") reconcileCalibration();
    else if (state.calibration?.phase === "direction-verification") scheduleDirectionVerification(200);
    else scheduleScan();
  } catch (error) {
    showError(error.message);
    $("#primaryButton").disabled = true;
  }
}

$("#primaryButton").addEventListener("click", handlePrimaryAction);
$("#exitButton").addEventListener("click", armedClick(
  $("#exitButton"),
  () => (state.calibration ? "再点一次：取消校准并退出" : "再点一次退出"),
  exitTool,
));
$("#cancelCalibrationButton").addEventListener("click", armedClick(
  $("#cancelCalibrationButton"),
  "再点一次确认取消",
  cancelCalibration,
));
// Keep the elapsed time moving while firmware is written.
state.clockTimer = setInterval(() => {
  if (state.repair?.phase === "ota" || state.repair?.phase === "post-ota") {
    $("#elapsedTime").textContent = `已用时 ${formatDuration(repairElapsedSeconds(state.repair))}`;
  }
}, 1000);

bootstrap();
