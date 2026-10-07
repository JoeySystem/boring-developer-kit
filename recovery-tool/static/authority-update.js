"use strict";

const $ = (selector) => document.querySelector(selector);
const terminalJobStatuses = new Set(["passed", "failed", "action-required", "retryable"]);
const state = {
  token: "",
  authority: null,
  devices: [],
  readiness: "waiting",
  blockReason: null,
  job: null,
  eventCursor: 0,
  pollTimer: null,
  scanTimer: null,
};

function formatDuration(totalSeconds = 0) {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

function replaceChildrenWithTextRows(container, rows) {
  container.replaceChildren(...rows.map((text) => {
    const item = document.createElement("li");
    item.textContent = text;
    return item;
  }));
}

function renderResultDetails(rows) {
  const details = $("#resultDetails");
  details.replaceChildren();
  rows.forEach(([label, value]) => {
    const term = document.createElement("dt");
    const description = document.createElement("dd");
    term.textContent = label;
    description.textContent = value || "—";
    details.append(term, description);
  });
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

function renderAuthority() {
  const authority = state.authority;
  if (!authority) return;
  $("#releaseId").textContent = authority.release_id;
  $("#buildId").textContent = `Build ID：${authority.build_id || "未提供"}`;
  $("#validationState").textContent = "官方签名恢复包";
}

function renderDevice(message = "") {
  const card = $("#deviceCard");
  card.className = `device-card ${state.readiness}`;
  const runtime = state.devices.find((device) => device.kind === "runtime");
  $("#deviceTitle").textContent = state.readiness === "ready"
    ? "已发现设备 USB 接口"
    : state.readiness === "blocked" ? "当前设备不可开始" : "请接入 1 台设备";
  $("#deviceMessage").textContent = message || "工具会自动扫描。";
  $("#devicePort").hidden = !runtime;
  $("#devicePort").textContent = runtime ? `${runtime.device} · ${runtime.vid_pid}` : "";
  const idle = !state.job || terminalJobStatuses.has(state.job.status);
  $("#primaryButton").disabled = state.readiness !== "ready" || !idle;
  if (state.readiness === "ready") {
    $("#primaryButton").textContent = state.job?.status === "retryable"
      ? "重新恢复固件"
      : "恢复官方固件";
  } else {
    $("#primaryButton").textContent = state.blockReason === "port-busy"
      ? "等待串口释放"
      : "等待设备";
  }
}

function renderJob() {
  const job = state.job;
  const progressCard = $("#progressCard");
  const resultCard = $("#resultCard");
  if (!job) {
    progressCard.hidden = true;
    resultCard.hidden = true;
    return;
  }
  progressCard.hidden = false;
  const progress = Math.max(0, Math.min(100, Number(job.progress) || 0));
  let progressTitle = "正在刷入并回读验证";
  if (job.status === "passed") progressTitle = "更新完成";
  else if (job.status === "retryable") progressTitle = "串口被其他程序占用";
  else if (job.status === "failed") progressTitle = "更新未完成";
  $("#progressTitle").textContent = progressTitle;
  $("#progressPercent").textContent = `${progress}%`;
  $("#elapsedTime").textContent = `已用时 ${formatDuration(job.elapsed_seconds)}`;
  $("#progressFill").style.width = `${progress}%`;
  $("#progressMessage").textContent = job.message || "请保持 USB 连接。";
  const events = job.allEvents || [];
  replaceChildrenWithTextRows(
    $("#eventList"),
    events.slice(-8).map((event) => event.message),
  );

  const done = terminalJobStatuses.has(job.status);
  resultCard.hidden = !done;
  if (done) {
    const success = job.status === "passed";
    resultCard.className = `result-card ${success ? "success" : "failure"}`;
    let resultTitle = "更新未通过验证";
    if (success) resultTitle = "已刷入并精确回读确认";
    else if (job.status === "retryable") resultTitle = "关闭占用程序后可立即重试";
    $("#resultTitle").textContent = resultTitle;
    $("#resultMessage").textContent = success
      ? "固件已恢复，官方来源已登记。请先退出本工具，再打开 Boring Console，通过 USB 使用。"
      : (job.message || "请按提示处理后重试。");
    const result = (job.results || [])[0] || {};
    const runtime = result.runtime || {};
    const compactMac = String(result.mac || "").replaceAll(":", "").replaceAll("-", "");
    renderResultDetails(success ? [
      ["设备序列号", runtime.serial || (compactMac ? `CP01-${compactMac}` : "—")],
      ["固件 Build ID", runtime.build_id || state.authority?.build_id || "—"],
      ["运行分区", runtime.running_partition || "已验证"],
      ["启动确认", runtime.rollback_pending === false ? "已确认可正常启动" : "已验证"],
    ] : []);
  }
}

async function bootstrap() {
  try {
    const data = await request("/api/authority-update/bootstrap");
    state.token = data.token;
    state.authority = data.authority;
    state.devices = data.devices || [];
    state.readiness = data.readiness;
    state.blockReason = data.block_reason || null;
    if (data.latest_job) {
      state.job = { ...data.latest_job, allEvents: data.latest_job.events || [] };
      state.eventCursor = data.latest_job.event_cursor || 0;
    }
    renderAuthority();
    renderDevice(data.message);
    renderJob();
    if (data.busy && state.job) pollJob();
    else scheduleScan();
  } catch (error) {
    showError(error.message);
    $("#primaryButton").disabled = true;
  }
}

async function scan() {
  if (state.job && !terminalJobStatuses.has(state.job.status)) return;
  try {
    const data = await request("/api/authority-update/scan");
    state.authority = data.authority;
    state.devices = data.devices || [];
    state.readiness = data.readiness;
    state.blockReason = data.block_reason || null;
    renderAuthority();
    renderDevice(data.message);
    showError();
  } catch (error) {
    showError(error.message);
  }
}

function scheduleScan() {
  clearInterval(state.scanTimer);
  state.scanTimer = setInterval(scan, 1500);
}

async function startUpdate() {
  const device = state.devices.find((item) => item.kind === "runtime");
  if (!device || state.readiness !== "ready") return;
  showError();
  $("#primaryButton").disabled = true;
  try {
    const data = await request("/api/authority-update/jobs", {
      method: "POST",
      body: JSON.stringify({ port: device.device }),
    });
    state.job = { id: data.job_id, status: "queued", progress: 0, allEvents: [] };
    state.eventCursor = 0;
    clearInterval(state.scanTimer);
    renderJob();
    pollJob();
  } catch (error) {
    showError(error.message);
    await scan();
  }
}

async function pollJob() {
  clearTimeout(state.pollTimer);
  try {
    const data = await request(`/api/authority-update/jobs/${state.job.id}?after_event=${state.eventCursor}`);
    const previous = state.job?.allEvents || [];
    state.job = {
      ...data,
      allEvents: data.events_reset ? (data.events || []) : previous.concat(data.events || []),
    };
    state.eventCursor = data.event_cursor || state.eventCursor;
    renderJob();
    renderDevice();
    if (!terminalJobStatuses.has(data.status)) {
      state.pollTimer = setTimeout(pollJob, 500);
    } else {
      await scan();
      scheduleScan();
    }
  } catch (error) {
    showError(error.message);
    state.pollTimer = setTimeout(pollJob, 1200);
  }
}

async function exitTool() {
  try {
    await request("/api/authority-update/shutdown", { method: "POST", body: "{}" });
    document.body.innerHTML = '<main class="closed"><h1>工具已退出</h1><p>可以关闭此页面。</p></main>';
  } catch (error) {
    showError(error.message);
  }
}

$("#scanButton").addEventListener("click", scan);
$("#primaryButton").addEventListener("click", startUpdate);
$("#exitButton").addEventListener("click", exitTool);

bootstrap();
