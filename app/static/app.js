"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

let token = "";
try { token = localStorage.getItem("rr_token") || ""; } catch { /* storage unavailable */ }

let pollTimer = null;
let appLoaded = false;
let settingsDirtyUntil = 0;

async function api(path, opts = {}) {
  const headers = {};
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (token) headers["Authorization"] = "Bearer " + token;
  const res = await fetch("/api" + path, {
    method: opts.method || (opts.body !== undefined ? "POST" : "GET"),
    headers,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 401) {
    show("token");
    throw new Error("API token required");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch { /* not json */ }
    throw new Error(detail);
  }
  const type = res.headers.get("content-type") || "";
  return type.includes("json") ? res.json() : res.blob();
}

function toast(msg, ms = 3500) {
  const el = $("#toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (el.hidden = true), ms);
}

function show(which) {
  $("#token-card").hidden = which !== "token";
  $("#setup-card").hidden = which !== "setup";
  $("#app").hidden = which !== "app";
}

function setConn(text, cls) {
  const el = $("#conn");
  el.textContent = text;
  el.className = "pill " + (cls || "");
}

function banner(msg) {
  const el = $("#banner");
  el.textContent = msg || "";
  el.hidden = !msg;
}

const fmt = (v, suffix = "") => (v === null || v === undefined ? "—" : String(v).replaceAll("_", " ") + suffix);

// ---------------------------------------------------------------- health / routing

async function checkHealth() {
  let h;
  try {
    h = await (await fetch("/api/health")).json();
  } catch {
    setConn("server offline", "bad");
    return;
  }
  if (h.token_required && !token) { show("token"); setConn("locked", "warn"); return; }

  if (h.state === "logged_out" || h.state === "reauth_required") {
    banner(h.state === "reauth_required" ? h.error : "");
    setConn(h.state === "logged_out" ? "not logged in" : "login required", "warn");
    show("setup");
    prefillEmail();
    return;
  }
  banner(h.state === "error" ? h.error : (h.cloud_unauthorized ? "Roborock cloud rejected the stored login. Local control may still work; log out and in again if commands fail." : ""));
  if (h.state === "connecting") setConn("connecting…", "warn");
  else if (h.device_connected) setConn(h.local_connection ? "local" : "cloud", "ok");
  else setConn(h.state === "ready" ? "robot offline" : h.state, "bad");

  show("app");
  if (h.state === "ready" && h.device_connected && !appLoaded) {
    appLoaded = true;
    loadAll();
  }
}

async function prefillEmail() {
  if ($("#email").value) return;
  try {
    const info = await api("/auth/info");
    if (info.default_email) $("#email").value = info.default_email;
  } catch { /* ignore */ }
}

// ---------------------------------------------------------------- data

async function refreshStatus() {
  try {
    const s = await api("/status");
    $("#state").textContent = fmt(s.state);
    $("#battery").textContent = s.battery ?? "—";
    $("#area").textContent = fmt(s.clean_area_m2, " m²");
    $("#time").textContent = s.clean_time_s == null ? "—" : Math.round(s.clean_time_s / 60) + " min";
    $("#mode").textContent = fmt(s.cleaning_mode);
    $("#fan").textContent = fmt(s.fan_speed);
    $("#water").textContent = fmt(s.water_level);
    $("#route").textContent = fmt(s.mop_route);
    const err = [s.error, s.dock_error && "dock: " + s.dock_error].filter(Boolean).join(" · ");
    $("#error").textContent = err.replaceAll("_", " ");
    $("#error").hidden = !err;
    if (Date.now() > settingsDirtyUntil) syncSettings(s);
  } catch (e) {
    $("#state").textContent = "unavailable";
  }
}

function syncSettings(s) {
  const current = { cleaning_mode: s.cleaning_mode, fan_speed: s.fan_speed, water_level: s.water_level, mop_route: s.mop_route };
  $$("select[data-setting]").forEach((sel) => {
    const v = current[sel.dataset.setting];
    if (v && [...sel.options].some((o) => o.value === v)) sel.value = v;
  });
}

async function loadDevice() {
  try {
    const d = await api("/devices");
    const dev = d.devices.find((x) => x.duid === d.selected);
    if (dev) $("#device-name").textContent = `${dev.name} · ${dev.model}`;
  } catch { /* ignore */ }
}

async function loadRooms() {
  const box = $("#rooms");
  try {
    const rooms = await api("/rooms");
    box.innerHTML = "";
    if (!rooms.length) { box.innerHTML = '<span class="muted">No rooms on the current map.</span>'; return; }
    for (const r of rooms) {
      const label = document.createElement("label");
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.value = r.segment_id;
      label.append(cb, document.createTextNode(r.name));
      box.append(label);
    }
  } catch (e) {
    box.innerHTML = "";
    box.append(Object.assign(document.createElement("span"), { className: "muted", textContent: e.message }));
  }
}

async function loadOptions() {
  try {
    const opts = await api("/options");
    $$("select[data-setting]").forEach((sel) => {
      const values = opts[sel.dataset.options] || [];
      sel.innerHTML = "";
      sel.disabled = !values.length;
      for (const v of values) sel.append(new Option(v.replaceAll("_", " "), v));
      sel.closest("label").hidden = !values.length;
    });
  } catch { /* ignore */ }
}

async function loadConsumables() {
  const box = $("#consumables");
  try {
    const items = await api("/consumables");
    box.innerHTML = "";
    for (const c of items) {
      const row = document.createElement("div");
      row.className = "consumable";
      const left = c.unit === "s" ? `${c.remaining_hours} h left` : `${Math.max(c.lifetime - c.used, 0)} cycles left`;
      row.innerHTML = `<span></span><span class="muted"></span><span></span><div class="bar"><span></span></div>`;
      row.children[0].textContent = c.name;
      row.children[1].textContent = `${c.remaining_percent}% · ${left}`;
      if (c.resettable) {
        const btn = document.createElement("button");
        btn.className = "link";
        btn.textContent = "Reset";
        btn.onclick = async () => {
          if (!confirm(`Reset the ${c.name.toLowerCase()} counter?`)) return;
          await run(() => api(`/consumables/${c.key}/reset`, { body: {} }), `${c.name} reset`);
          loadConsumables();
        };
        row.children[2].append(btn);
      }
      const bar = row.querySelector(".bar span");
      bar.style.width = c.remaining_percent + "%";
      if (c.remaining_percent <= 10) bar.classList.add("low");
      box.append(row);
    }
    if (!items.length) box.innerHTML = '<span class="muted">No data.</span>';
  } catch (e) {
    box.textContent = e.message;
  }
}

async function loadSummary() {
  try {
    const s = await api("/summary");
    const last = s.last_clean || {};
    const rows = [
      ["Total time", fmt(s.total_clean_time_h, " h")],
      ["Total area", fmt(s.total_clean_area_m2, " m²")],
      ["Cleans", fmt(s.clean_count)],
      ["Last clean", last.start ? new Date(last.start).toLocaleString() : "—"],
      ["Last area", fmt(last.area_m2, " m²")],
      ["Last duration", last.duration_s == null ? "—" : Math.round(last.duration_s / 60) + " min"],
    ];
    $("#summary").innerHTML = "";
    for (const [k, v] of rows) {
      const div = document.createElement("div");
      div.innerHTML = "<dt></dt><dd></dd>";
      div.children[0].textContent = k;
      div.children[1].textContent = v;
      $("#summary").append(div);
    }
  } catch { /* ignore */ }
}

async function loadMap() {
  const img = $("#map");
  const msg = $("#map-msg");
  msg.textContent = "Loading map…";
  msg.hidden = false;
  try {
    const blob = await api("/map.png");
    if (img.src) URL.revokeObjectURL(img.src);
    img.src = URL.createObjectURL(blob);
    img.hidden = false;
    msg.hidden = true;
  } catch (e) {
    msg.textContent = "Map unavailable: " + e.message;
  }
}

function loadAll() {
  loadDevice();
  refreshStatus();
  loadOptions();
  loadRooms();
  loadConsumables();
  loadSummary();
  loadMap();
}

// ---------------------------------------------------------------- actions

async function run(fn, okMsg) {
  try {
    await fn();
    if (okMsg) toast(okMsg);
    setTimeout(refreshStatus, 1500);
  } catch (e) {
    toast("Failed: " + e.message, 6000);
  }
}

$$("button[data-cmd]").forEach((btn) =>
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    await run(() => api("/" + btn.dataset.cmd, { body: {} }), btn.textContent + " sent");
    btn.disabled = false;
  })
);

$("#clean-rooms").addEventListener("click", async () => {
  const rooms = [...$$("#rooms input:checked")].map((cb) => cb.value);
  if (!rooms.length) { toast("Select at least one room"); return; }
  await run(() => api("/clean/rooms", { body: { rooms, repeat: Number($("#repeat").value) } }), "Room clean started");
});

$$("select[data-setting]").forEach((sel) =>
  sel.addEventListener("change", async () => {
    settingsDirtyUntil = Date.now() + 8000;
    await run(() => api("/settings", { body: { [sel.dataset.setting]: sel.value } }), "Setting updated");
  })
);

$("#map-refresh").addEventListener("click", loadMap);

$("#token-form").addEventListener("submit", (e) => {
  e.preventDefault();
  token = $("#token-input").value.trim();
  try { localStorage.setItem("rr_token", token); } catch { /* ignore */ }
  checkHealth();
});

$("#email-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = e.submitter;
  btn.disabled = true;
  try {
    await api("/auth/request-code", { body: { email: $("#email").value.trim() } });
    $("#code-form").hidden = false;
    $("#code").focus();
    toast("Code sent — check your email");
  } catch (err) {
    toast(err.message, 6000);
  } finally {
    btn.disabled = false;
  }
});

$("#code-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = e.submitter;
  btn.disabled = true;
  try {
    await api("/auth/login", { body: { email: $("#email").value.trim(), code: $("#code").value.trim() } });
    toast("Logged in — connecting to the robot…");
    $("#code-form").hidden = true;
    appLoaded = false;
    checkHealth();
  } catch (err) {
    toast(err.message, 6000);
  } finally {
    btn.disabled = false;
  }
});

$("#logout").addEventListener("click", async () => {
  if (!confirm("Remove the stored Roborock login from this server?")) return;
  await run(() => api("/auth/logout", { body: {} }), "Logged out");
  appLoaded = false;
  checkHealth();
});

$("#mcp-url").textContent = location.origin + "/mcp";

// ---------------------------------------------------------------- polling

function tick() {
  checkHealth().then(() => {
    if (!$("#app").hidden && appLoaded) refreshStatus();
  });
}
tick();
pollTimer = setInterval(tick, 5000);
