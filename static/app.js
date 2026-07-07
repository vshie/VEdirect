/* global Chart */

/** API + fetch base: derived from where app.js was loaded (not location.pathname — BlueOS may use "/"). */
function appRootUrl() {
  const el = document.querySelector('script[src*="static/app.js"]');
  if (el && el.src) {
    try {
      const u = new URL(el.src);
      let path = u.pathname.replace(/\/static\/app\.js(\?.*)?$/i, "");
      if (!path.endsWith("/")) path += "/";
      return u.origin + path;
    } catch (_) {
      /* fall through */
    }
  }
  const p = location.pathname.endsWith("/") ? location.pathname : `${location.pathname}/`;
  return `${location.origin}${p}`;
}

const APP_ROOT = appRootUrl();

function apiUrl(path) {
  const rel = path.startsWith("/") ? path.slice(1) : path;
  return new URL(rel, APP_ROOT).href;
}

function $(sel) {
  return document.querySelector(sel);
}

const METRIC_LABELS = {
  battery_voltage_V: "Battery voltage (V)",
  battery_current_A: "Battery current (A)",
  pv_voltage_V: "PV voltage (V)",
  pv_power_W: "PV power (W)",
  load_current_A: "Load current (A)",
  charge_state_code: "Charge state",
  mppt_state_code: "Tracker state",
  error_code: "Error code",
  yield_total_kWh: "Yield total (kWh)",
  yield_today_kWh: "Yield today (kWh)",
  max_power_today_W: "Max power today (W)",
  battery_temp_C: "Battery temp (°C)",
};

const CHARGE_STATES = {
  0: "Off", 1: "Low power", 2: "Fault", 3: "Bulk", 4: "Absorption",
  5: "Float", 6: "Storage", 7: "Equalize (manual)", 9: "Inverting",
  11: "Power supply", 245: "Starting-up", 246: "Repeated absorption",
  247: "Auto equalize / Recondition", 248: "BatterySafe", 252: "External control",
};

const MPPT_STATES = {
  0: "Off", 1: "Voltage/current limited", 2: "MPP tracking",
};

const ERROR_CODES = {
  0: "No error", 2: "Battery voltage too high", 17: "Charger too hot",
  18: "Charger over-current", 19: "Charger current reversed",
  20: "Bulk time limit exceeded", 21: "Current sensor issue",
  26: "Terminals overheated", 33: "Input voltage too high",
  34: "Input current too high", 38: "Input shutdown (battery over-voltage)",
  39: "Input shutdown (current during off)", 65: "Lost communication",
  66: "Sync config issue", 67: "BMS connection lost", 68: "Network misconfigured",
  116: "Calibration data lost", 117: "Incompatible firmware", 119: "Settings invalid",
};

function fmtNum(v, digits) {
  if (v === "" || v === null || v === undefined) return "—";
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(digits == null ? 2 : digits) : String(v);
}

function tabInit() {
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.dataset.tab;
      document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b === btn));
      document.querySelectorAll(".panel").forEach((p) => {
        p.classList.toggle("active", p.id === `panel-${id}`);
      });
      if (id === "dash") loadChart();
    });
  });
}

async function fetchJSON(path, opts) {
  const r = await fetch(apiUrl(path), opts);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

function setCard(id, value) {
  const el = $(id);
  if (el) el.textContent = value;
}

async function refreshStatus() {
  try {
    const s = await fetchJSON("api/status");
    const d = s.decoded || {};
    const online = s.connected && s.fresh;

    const dot = $("#conn-dot");
    const ct = $("#conn-text");
    if (dot) dot.className = `dot ${online ? "dot-good" : "dot-bad"}`;
    if (ct) {
      if (online) {
        ct.textContent = `Connected · ${s.serial_port} · ${s.frames_received} frames`;
      } else if (s.connected) {
        ct.textContent = `Port open, no fresh data (${s.seconds_since_last_frame ?? "?"}s)`;
      } else {
        ct.textContent = s.reader_error || "Disconnected";
      }
    }

    setCard("#c-pv-power", fmtNum(d.pv_power_W, 0));
    setCard("#c-pv-voltage", fmtNum(d.pv_voltage_V, 2));
    setCard("#c-batt-voltage", fmtNum(d.battery_voltage_V, 2));
    setCard("#c-batt-current", fmtNum(d.battery_current_A, 2));
    setCard("#c-load-current", fmtNum(d.load_current_A, 2));
    // Load power is not sent by the controller; derive it from the battery
    // voltage and the load output current (the load draws from the battery).
    const bv = Number(d.battery_voltage_V);
    const la = Number(d.load_current_A);
    const loadW = Number.isFinite(bv) && Number.isFinite(la) ? bv * la : null;
    setCard("#c-load-power", loadW == null ? "—" : loadW.toFixed(1));
    setCard("#c-load-state", d.load_state != null ? d.load_state : "—");
    setCard("#c-yield-today", fmtNum(d.yield_today_kWh, 2));
    setCard("#c-max-today", fmtNum(d.max_power_today_W, 0));
    setCard("#c-yield-total", fmtNum(d.yield_total_kWh, 2));

    const cs = d.charge_state_code;
    setCard("#c-charge-state", cs != null && cs !== "" ? (CHARGE_STATES[cs] || `Code ${cs}`) : "—");
    const mppt = d.mppt_state_code;
    setCard("#c-mppt-state", mppt != null && mppt !== "" ? (MPPT_STATES[mppt] || `Code ${mppt}`) : "—");
    const err = d.error_code;
    const errEl = $("#c-error");
    if (errEl) {
      errEl.textContent = err != null && err !== "" ? (ERROR_CODES[err] || `Code ${err}`) : "—";
      errEl.classList.toggle("err-active", Number(err) > 0);
    }

    const parts = [];
    if (s.last_csv_error) parts.push(`CSV: ${s.last_csv_error}`);
    const me = s.last_mavlink_errors || [];
    if (me.length) parts.push(`MAVLink: ${me.join(" · ")}`);
    else if (s.mavlink_enabled === false) parts.push("MAVLink push disabled");
    parts.push(`${s.rows_logged || 0} rows logged`);
    const sl = $("#status-line");
    if (sl) sl.textContent = parts.join("  ·  ");
  } catch (e) {
    const ct = $("#conn-text");
    if (ct) ct.textContent = "Status error";
    const dot = $("#conn-dot");
    if (dot) dot.className = "dot dot-bad";
  }
}

let chart;
let chartLeft = "load_current_A";
let chartRight = "pv_power_W";
let chartWindow = 20;

function parseTS(row) {
  const t = row.timestamp_utc;
  if (!t) return null;
  const d = new Date(t);
  return Number.isFinite(d.getTime()) ? d : null;
}

async function loadChart() {
  if (typeof Chart === "undefined") return;
  const { points } = await fetchJSON(`api/history?minutes=${chartWindow}`);
  const labels = [];
  const left = [];
  const right = [];
  for (const p of points) {
    const d = parseTS(p);
    if (!d) continue;
    labels.push(d.toLocaleTimeString());
    const lv = p[chartLeft];
    const rv = p[chartRight];
    left.push(lv === "" || lv == null ? null : Number(lv));
    right.push(rv === "" || rv == null ? null : Number(rv));
  }

  const data = {
    labels,
    datasets: [
      {
        label: METRIC_LABELS[chartLeft] || chartLeft,
        data: left,
        borderColor: "#f2a33c",
        backgroundColor: "rgba(242,163,60,0.1)",
        tension: 0.2,
        spanGaps: true,
        pointRadius: 0,
        yAxisID: "yLeft",
      },
      {
        label: METRIC_LABELS[chartRight] || chartRight,
        data: right,
        borderColor: "#2f9e5b",
        backgroundColor: "rgba(47,158,91,0.1)",
        tension: 0.2,
        spanGaps: true,
        pointRadius: 0,
        yAxisID: "yRight",
      },
    ],
  };

  const canvas = $("#chart");
  if (chart) chart.destroy();
  chart = new Chart(canvas, {
    type: "line",
    data,
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        x: {
          grid: { color: "#e2e8ee" },
          ticks: { color: "#5c6b7a", maxRotation: 45, autoSkip: true, maxTicksLimit: 12 },
        },
        yLeft: {
          position: "left",
          grid: { color: "#e2e8ee" },
          ticks: { color: "#c47d20" },
          title: { display: true, text: METRIC_LABELS[chartLeft] || chartLeft, color: "#5c6b7a", font: { size: 11 } },
        },
        yRight: {
          position: "right",
          grid: { drawOnChartArea: false },
          ticks: { color: "#2f9e5b" },
          title: { display: true, text: METRIC_LABELS[chartRight] || chartRight, color: "#5c6b7a", font: { size: 11 } },
        },
      },
      plugins: { legend: { labels: { color: "#1a2332" } } },
    },
  });
}

function fillMetricSelect(sel, columns, selected) {
  sel.innerHTML = "";
  for (const col of columns) {
    const opt = document.createElement("option");
    opt.value = col;
    opt.textContent = METRIC_LABELS[col] || col;
    if (col === selected) opt.selected = true;
    sel.appendChild(opt);
  }
}

async function chartControlsInit() {
  let columns = ["load_current_A", "pv_power_W"];
  try {
    const m = await fetchJSON("api/metrics");
    if (Array.isArray(m.numeric_columns) && m.numeric_columns.length) columns = m.numeric_columns;
  } catch (_) { /* use fallback */ }

  const leftSel = $("#chart-left");
  const rightSel = $("#chart-right");
  const winSel = $("#chart-window");
  fillMetricSelect(leftSel, columns, chartLeft);
  fillMetricSelect(rightSel, columns, chartRight);
  if (winSel) winSel.value = String(chartWindow);

  async function persist(patch) {
    try {
      await fetchJSON("api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
    } catch (_) { /* non-fatal */ }
  }

  leftSel.addEventListener("change", () => {
    chartLeft = leftSel.value;
    persist({ chart_left_metric: chartLeft });
    loadChart();
  });
  rightSel.addEventListener("change", () => {
    chartRight = rightSel.value;
    persist({ chart_right_metric: chartRight });
    loadChart();
  });
  if (winSel) {
    winSel.addEventListener("change", () => {
      chartWindow = Number(winSel.value);
      persist({ chart_window_minutes: chartWindow });
      loadChart();
    });
  }
}

async function loadSettingsForm() {
  const s = await fetchJSON("api/settings");
  // Seed chart selections from persisted settings.
  if (s.chart_left_metric) chartLeft = s.chart_left_metric;
  if (s.chart_right_metric) chartRight = s.chart_right_metric;
  if (s.chart_window_minutes) chartWindow = Number(s.chart_window_minutes);

  const form = $("#settings-form");
  for (const [k, v] of Object.entries(s)) {
    const el = form.elements.namedItem(k);
    if (!el) continue;
    if (el.type === "checkbox") {
      el.checked = Boolean(v);
    } else if (v === null || v === undefined) {
      el.value = "";
    } else {
      el.value = String(v);
    }
  }
}

function settingsInit() {
  $("#settings-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const form = ev.target;
    const msg = $("#save-msg");
    const body = {};
    const data = new FormData(form);
    for (const [k, raw] of data.entries()) body[k] = raw;
    body.baud_rate = Number(body.baud_rate);
    body.poll_interval_s = Number(body.poll_interval_s);
    body.stale_after_s = Number(body.stale_after_s);
    body.mavlink_header_system_id = Number(body.mavlink_header_system_id);
    body.mavlink_header_component_id = Number(body.mavlink_header_component_id);
    body.mavlink_enabled = form.elements.mavlink_enabled.checked;
    body.emit_heartbeat = form.elements.emit_heartbeat.checked;

    try {
      await fetchJSON("api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      msg.textContent = "Saved.";
    } catch (e) {
      msg.textContent = `Error: ${e.message}`;
    }
    setTimeout(() => { msg.textContent = ""; }, 4000);
  });
}

function wireCsvDownloadLink() {
  const a = $("#csv-dl");
  if (a) a.href = apiUrl("api/download/csv");
}

/** Embed the BlueOS File Browser (port 7777) scoped to this extension's data dir. */
function wireFileBrowser() {
  const iframe = $("#file-browser");
  const link = $("#fb-link");
  const fallback = $("#fb-fallback");
  // /usr/blueos/extensions/vedirect is bound to /data, and BlueOS serves it at
  // :7777/files/extensions/vedirect (same convention other extensions use).
  const url = `${location.protocol}//${location.hostname}:7777/files/extensions/vedirect`;
  if (iframe) iframe.src = url;
  if (link) link.href = url;
  // If it can't load (e.g. dev/local, no BlueOS core), reveal the direct link.
  if (iframe && fallback) {
    let loaded = false;
    iframe.addEventListener("load", () => { loaded = true; });
    setTimeout(() => { if (!loaded) fallback.classList.remove("hidden"); }, 4000);
  }
}

async function boot() {
  tabInit();
  settingsInit();
  wireCsvDownloadLink();
  wireFileBrowser();
  await loadSettingsForm();
  await chartControlsInit();
  refreshStatus();
  loadChart();
  setInterval(refreshStatus, 2000);
  setInterval(() => {
    const dash = $("#panel-dash");
    if (dash && dash.classList.contains("active")) loadChart();
  }, 15000);
}

boot();
