(() => {
  "use strict";

  // -----------------------------
  // Tiny DOM helpers
  // -----------------------------
  const $ = (id) => document.getElementById(id);
  const qs = (sel, root = document) => root.querySelector(sel);

  function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "class") node.className = v;
      else if (k === "dataset") Object.assign(node.dataset, v);
      else if (k === "text") node.textContent = v;
      else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
      else if (v === null || typeof v === "undefined") continue;
      else node.setAttribute(k, String(v));
    }
    for (const c of children || []) {
      if (c === null || typeof c === "undefined") continue;
      if (typeof c === "string") node.appendChild(document.createTextNode(c));
      else node.appendChild(c);
    }
    return node;
  }

  function renderHealthBadge(score, flags = []) {
    const value = Number(score);
    const safeScore = Number.isFinite(value) ? Math.max(0, Math.min(100, Math.round(value))) : null;
    const cls = safeScore === null ? "" : safeScore >= 80 ? "ok" : safeScore >= 50 ? "warn" : "error";
    const tooltip = Array.isArray(flags) && flags.length ? flags.join(", ") : "No flags";
    return el(
      "span",
      {
        class: `badge ${cls}`.trim(),
        title: tooltip,
        text: safeScore === null ? "-" : `${safeScore}`,
      },
      []
    );
  }

  function clampInt(value, fallback = 0) {
    const n = Number.parseInt(String(value), 10);
    return Number.isFinite(n) ? n : fallback;
  }

  function clampFloat(value, fallback = 1.0) {
    const n = Number.parseFloat(String(value));
    return Number.isFinite(n) ? n : fallback;
  }

  // -----------------------------
  // Auth token storage
  // -----------------------------
  const AUTH_KEY = "scada_admin_auth_v1";

  const authStore = {
    _read(storage) {
      try {
        const raw = storage.getItem(AUTH_KEY);
        if (!raw) return null;
        const obj = JSON.parse(raw);
        if (!obj || typeof obj !== "object") return null;
        if (!obj.access_token || !obj.refresh_token) return null;
        return obj;
      } catch {
        return null;
      }
    },
    get() {
      return this._read(sessionStorage) || this._read(localStorage);
    },
    set(tokens, remember) {
      const payload = {
        access_token: tokens.access_token,
        refresh_token: tokens.refresh_token,
        token_type: tokens.token_type || "bearer",
        remember: !!remember,
        saved_at: new Date().toISOString(),
      };
      if (remember) {
        localStorage.setItem(AUTH_KEY, JSON.stringify(payload));
        sessionStorage.removeItem(AUTH_KEY);
      } else {
        sessionStorage.setItem(AUTH_KEY, JSON.stringify(payload));
        localStorage.removeItem(AUTH_KEY);
      }
    },
    clear() {
      sessionStorage.removeItem(AUTH_KEY);
      localStorage.removeItem(AUTH_KEY);
    },
    accessToken() {
      const a = this.get();
      return a?.access_token || null;
    },
    refreshToken() {
      const a = this.get();
      return a?.refresh_token || null;
    },
  };

  // -----------------------------
  // API client (with refresh-on-401)
  // -----------------------------
  const api = {
    _refreshInFlight: null,

    async safeJson(res) {
      try {
        return await res.json();
      } catch {
        return null;
      }
    },

    errorMessage(data, fallback = "Request failed") {
      if (!data) return fallback;
      if (typeof data === "string") return data;
      if (typeof data.detail === "string") return data.detail;
      if (Array.isArray(data.detail)) {
        // pydantic validation errors
        return data.detail.map((e) => e.msg || e.message || "Invalid input").join("; ");
      }
      return fallback;
    },

    async refresh() {
      if (this._refreshInFlight) return this._refreshInFlight;

      const refresh_token = authStore.refreshToken();
      if (!refresh_token) throw new Error("No refresh token");

      this._refreshInFlight = (async () => {
        const res = await fetch("/auth/refresh", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token }),
        });

        if (!res.ok) {
          const data = await this.safeJson(res);
          throw new Error(this.errorMessage(data, "Session expired"));
        }

        const data = await res.json();
        // Refresh token rotation: store the NEW refresh token returned by the server.
        authStore.set({ access_token: data.access_token, refresh_token: data.refresh_token }, authStore.get()?.remember);
        return data.access_token;
      })();

      try {
        return await this._refreshInFlight;
      } finally {
        this._refreshInFlight = null;
      }
    },

    async request(method, url, opts = {}) {
      const { json, query, headers = {}, skipAuth = false, _retry = false } = opts || {};
      const u = new URL(url, window.location.origin);

      if (query && typeof query === "object") {
        for (const [k, v] of Object.entries(query)) {
          if (v === null || typeof v === "undefined") continue;
          u.searchParams.set(k, String(v));
        }
      }

      const h = new Headers(headers || {});
      if (json !== undefined) h.set("Content-Type", "application/json");
      if (!skipAuth) {
        const token = authStore.accessToken();
        if (token) h.set("Authorization", `Bearer ${token}`);
      }

      const res = await fetch(u.toString(), {
        method,
        headers: h,
        body: json !== undefined ? JSON.stringify(json) : undefined,
      });

      if (res.status === 401 && !skipAuth && !_retry) {
        try {
          await this.refresh();
          return await this.request(method, url, { ...opts, _retry: true });
        } catch {
          authStore.clear();
          if (window.location.pathname !== "/admin-panel/login") {
            window.location.href = "/admin-panel/login";
          }
          throw new Error("Not authenticated");
        }
      }

      const contentType = res.headers.get("Content-Type") || "";
      const isJson = contentType.includes("application/json");
      const data = isJson ? await this.safeJson(res) : await res.text();

      if (!res.ok) {
        throw new Error(this.errorMessage(data, `HTTP ${res.status}`));
      }

      return data;
    },

    get(url, opts) {
      return this.request("GET", url, opts);
    },
    post(url, opts) {
      return this.request("POST", url, opts);
    },
    put(url, opts) {
      return this.request("PUT", url, opts);
    },
    patch(url, opts) {
      return this.request("PATCH", url, opts);
    },
    delete(url, opts) {
      return this.request("DELETE", url, opts);
    },
  };

  // -----------------------------
  // UI primitives
  // -----------------------------
  let toastTimer = null;

  function toast(message, kind = "ok") {
    const t = $("toast");
    if (!t) return;

    t.textContent = message;
    t.classList.remove("hidden");
    t.classList.remove("ok", "error");
    if (kind === "error") t.classList.add("error");
    else t.classList.add("ok");

    if (toastTimer) window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => t.classList.add("hidden"), 3200);
  }

  function setStatus(elOrId, message = "", kind = "") {
    const node = typeof elOrId === "string" ? $(elOrId) : elOrId;
    if (!node) return;
    node.textContent = message || "";
    node.classList.remove("ok", "error");
    if (kind) node.classList.add(kind);
  }

  function openDialog(idOrNode) {
    const dlg = typeof idOrNode === "string" ? $(idOrNode) : idOrNode;
    if (!dlg) return;
    if (typeof dlg.showModal === "function") dlg.showModal();
    else dlg.open = true;
  }

  function closeDialog(idOrNode) {
    const dlg = typeof idOrNode === "string" ? $(idOrNode) : idOrNode;
    if (!dlg) return;
    try {
      dlg.close();
    } catch {
      dlg.open = false;
    }
  }

  function confirmDanger(msg) {
    return window.confirm(msg);
  }

  function can(perms, p) {
    return perms.has(p) || perms.has(p.split(":", 1)[0] + ":*");
  }

  function formatList(arr) {
    if (!arr || !arr.length) return "—";
    return arr.join(", ");
  }

  // -----------------------------
  // Global app state
  // -----------------------------
  const state = {
    me: null,
    perms: new Set(),
    users: [],
    roles: [],
    cfgTree: [],
    cfgIndex: new Map(), // key -> { key, type, id, name, parentKey, raw }
    cfgCollapsed: new Set(),
    cfgSelectedKey: null,
    datapointInstrumentLinksByEquipment: new Map(),
    datapointInstrumentLinkLoading: new Set(),
    mappedInstrumentIds: new Set(),

    meta: {
      classes: [],
      units: [],
      groups: [],
      loaded: false,
      currentDomain: "datapoint",
      loadedByDomain: {},
      catalog: {},
    },

    access: {
      principalType: "role",
      principalId: null,
      grants: [],
      grantByKey: new Map(), // resource_type:id -> grant
      selectedKey: null,
      collapsed: new Set(),
    },

    alarms: {
      datapoints: [],
      rules: [],
      selectedDatapointId: null,
      loaded: false,
    },
    alarmLog: {
      items: [],
      total: 0,
      limit: 200,
      offset: 0,
    },
    commandLog: {
      items: [],
      total: 0,
      limit: 200,
      offset: 0,
      ws: {
        socket: null,
        retryTimer: null,
      },
    },
    logs: {
      activeTab: "alarm",
    },
  };

  // -----------------------------
  // Meta options by domain
  // -----------------------------
  const META_DEFINITIONS = {
    datapoint: {
      label: "Datapoint",
      hint: "Configure shared classes, units, and groups (groups apply across PLC/container/equipment/datapoints).",
      kinds: {
        class: { label: "Class", endpoint: "/api/config/datapoint-classes" },
        unit: { label: "Unit", endpoint: "/api/config/datapoint-units" },
        group: { label: "Group", endpoint: "/api/config/datapoint-groups" },
      },
    },
    plc: {
      label: "PLC",
      hint: "PLC group assignment is configured in PLC Builder using shared datapoint groups.",
      kinds: {},
    },
    container: {
      label: "Container",
      hint: "Manage container type options. Containers also use shared datapoint groups from Datapoint Meta.",
      kinds: {
        type: { label: "Type", endpoint: "/api/config/container-types" },
      },
    },
    equipment: {
      label: "Equipment",
      hint: "Manage equipment type options. Equipment also uses shared datapoint groups from Datapoint Meta.",
      kinds: {
        type: { label: "Type", endpoint: "/api/config/equipment-types" },
      },
    },
  };

  function getMetaDomain(domain) {
    return META_DEFINITIONS[domain] || META_DEFINITIONS.datapoint;
  }

  function getMetaKindDef(domain, kind) {
    return getMetaDomain(domain)?.kinds?.[kind] || null;
  }

  function getMetaItems(domain, kind) {
    if (domain === "datapoint") {
      if (kind === "class") return state.meta.classes || [];
      if (kind === "unit") return state.meta.units || [];
      if (kind === "group") return state.meta.groups || [];
    }
    return state.meta.catalog?.[domain]?.[kind] || [];
  }

  async function ensureMetaDomainLoaded(domain = "datapoint", force = false) {
    const selectedDomain = META_DEFINITIONS[domain] ? domain : "datapoint";
    if (!force && state.meta.loadedByDomain?.[selectedDomain]) return;

    const domainDef = getMetaDomain(selectedDomain);
    const kindEntries = Object.entries(domainDef.kinds || {});

    if (!kindEntries.length) {
      state.meta.catalog[selectedDomain] = {};
      state.meta.loadedByDomain[selectedDomain] = true;
      return;
    }

    const responses = await Promise.all(
      kindEntries.map(async ([kind, def]) => {
        if (!def?.endpoint) return [kind, []];
        const items = await api.get(def.endpoint);
        return [kind, Array.isArray(items) ? items : []];
      })
    );

    const byKind = Object.fromEntries(responses);
    state.meta.catalog[selectedDomain] = byKind;
    state.meta.loadedByDomain[selectedDomain] = true;

    if (selectedDomain === "datapoint") {
      state.meta.classes = byKind.class || [];
      state.meta.units = byKind.unit || [];
      state.meta.groups = byKind.group || [];
      state.meta.loaded = true;
    }
  }

  async function ensureMetaLoaded(force = false) {
    await ensureMetaDomainLoaded("datapoint", force);
  }

    // -----------------------------
  // Routing (hash views) - FIXED
  // -----------------------------

  function parseHash() {
    let h = window.location.hash || "";
    if (h.startsWith("#")) h = h.slice(1);
    if (!h) return { view: null, query: {} };
    const [path, qsPart] = h.split("?");
    const query = {};
    if (qsPart) {
      for (const [k, v] of new URLSearchParams(qsPart).entries()) {
        query[k] = v;
      }
    }
    return { view: (path || null), query };
  }

  function hasView(view) {
    if (!view) return false;
    return !!document.getElementById(`view-${view}`);
  }

  function setActiveNav(view) {
    document.querySelectorAll(".nav-link").forEach((a) => {
      const v = a.getAttribute("data-view");
      const route = a.getAttribute("data-route");
      const routeActive = !v && route && window.location.pathname === route;
      a.classList.toggle("active", v === view || routeActive);
    });
  }

  function showView(view) {
    // Hide ALL views generically (no dependency on a hardcoded list)
    document.querySelectorAll("section.view").forEach((sec) => sec.classList.add("hidden"));

    // Show the requested view if it exists
    const sec = document.getElementById(`view-${view}`);
    if (sec) sec.classList.remove("hidden");

    setActiveNav(view);
  }

  function firstAllowedView() {
    const p = state.perms;

    const candidates = [
      { view: "users", ok: can(p, "users:admin") },
      { view: "roles", ok: can(p, "roles:admin") },
      { view: "plc", ok: can(p, "config:read") || can(p, "config:write") },
      { view: "instruments", ok: can(p, "maintenance:read") || can(p, "maintenance:write") },
      { view: "maintenance-assets", ok: can(p, "maintenance:read") || can(p, "maintenance:write") },
      { view: "meta", ok: can(p, "config:read") || can(p, "config:write") },
      { view: "alarms", ok: can(p, "alarms:admin") },
      { view: "logs", ok: can(p, "alarms:admin") || can(p, "command:read") || can(p, "command:write") },
      { view: "access", ok: can(p, "users:admin") || can(p, "roles:admin") },
    ];

    // If the candidate view doesn’t exist in HTML, skip it.
    return candidates.find((c) => c.ok && hasView(c.view))?.view
      || (hasView("users") ? "users" : (document.querySelector("section.view")?.id?.replace("view-", "") || "users"));
  }

  function navigate(view, query = {}) {
    const qs = new URLSearchParams(query).toString();
    window.location.hash = qs ? `#${view}?${qs}` : `#${view}`;
  }

  async function route() {
    const { view, query } = parseHash();
    const normalizedView = view === "alarm-log" || view === "command-log" ? "logs" : view;
    if (view === "alarm-log" && !query.tab) query.tab = "alarm";
    if (view === "command-log" && !query.tab) query.tab = "command";

    // Accept ANY hash view that actually exists in the DOM.
    const requested = hasView(normalizedView) ? normalizedView : null;
    const target = requested || firstAllowedView();

    // Client-side guards (UX only; server is authoritative)
    if (target === "users" && !can(state.perms, "users:admin")) return navigate(firstAllowedView());
    if (target === "roles" && !can(state.perms, "roles:admin")) return navigate(firstAllowedView());
    if (target === "plc" && !(can(state.perms, "config:read") || can(state.perms, "config:write")))
      return navigate(firstAllowedView());
    if (target === "meta" && !(can(state.perms, "config:read") || can(state.perms, "config:write")))
      return navigate(firstAllowedView());
    if (target === "alarms" && !can(state.perms, "alarms:admin")) return navigate(firstAllowedView());
    if (target === "access" && !(can(state.perms, "users:admin") || can(state.perms, "roles:admin")))
      return navigate(firstAllowedView());
    if (target === "maintenance-assets" && !(can(state.perms, "maintenance:read") || can(state.perms, "maintenance:write")))
      return navigate(firstAllowedView());
    if (
      target === "logs" &&
      !(can(state.perms, "alarms:admin") || can(state.perms, "command:read") || can(state.perms, "command:write"))
    )
      return navigate(firstAllowedView());

    showView(target);

    if (target === "users") await usersView.show();
    if (target === "roles") await rolesView.show();
    if (target === "plc") await plcView.show();
    if (target === "instruments") await instrumentsView.show();
    if (target === "maintenance-assets") await maintenanceAssetsView.show();
    if (target === "meta") await metaView.show();
    if (target === "alarms") await alarmsView.show();
    if (target === "access") await accessView.show(query);
    if (target === "logs") await showLogsView(query);
  }

  function setLogsTab(tab) {
    const canAlarm = can(state.perms, "alarms:admin");
    const canCommand = can(state.perms, "command:read") || can(state.perms, "command:write") || can(state.perms, "command:*");
    const requested = tab === "command" ? "command" : "alarm";

    let next = requested;
    if (next === "alarm" && !canAlarm) next = canCommand ? "command" : "alarm";
    if (next === "command" && !canCommand) next = canAlarm ? "alarm" : "command";
    state.logs.activeTab = next;

    const alarmPanel = $("log-tab-alarm");
    const commandPanel = $("log-tab-command");
    alarmPanel?.classList.toggle("hidden", next !== "alarm");
    commandPanel?.classList.toggle("hidden", next !== "command");

    const alarmBtn = $("btn-log-tab-alarm");
    const commandBtn = $("btn-log-tab-command");
    if (alarmBtn) {
      alarmBtn.disabled = !canAlarm;
      alarmBtn.classList.toggle("primary", next === "alarm");
    }
    if (commandBtn) {
      commandBtn.disabled = !canCommand;
      commandBtn.classList.toggle("primary", next === "command");
    }
  }

  async function showLogsView(query = {}) {
    const requestedTab = String(query?.tab || state.logs.activeTab || "alarm").toLowerCase();
    setLogsTab(requestedTab === "command" ? "command" : "alarm");
    if (state.logs.activeTab === "command") await loadCommandLog();
    else await loadAlarmLog();
  }


  // -----------------------------
  // Auth / bootstrap
  // -----------------------------
  async function initLoginPage() {
    const form = $("login-form");
    const statusEl = $("login-status");
    if (!form) return;

    // If already logged in, go to panel.
    const existing = authStore.get();
    if (existing?.access_token) {
      try {
        await api.get("/auth/me");
        window.location.href = "/admin-panel";
        return;
      } catch {
        // ignore
      }
    }

    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      setStatus(statusEl, "Signing in…");

      const fd = new FormData(form);
      const username = String(fd.get("username") || "").trim();
      const password = String(fd.get("password") || "");
      const remember = !!fd.get("remember");

      try {
        const data = await api.post("/auth/login", { json: { username, password }, skipAuth: true });
        authStore.set(data, remember);
        window.location.href = "/admin-panel";
      } catch (err) {
        setStatus(statusEl, err?.message || "Login failed", "error");
      }
    });
  }

  // -----------------------------
  // Alarm Log loader + bindings
  // -----------------------------
  async function loadAlarmLog() {
    setStatus('alarm-log-status', 'Loading...', '');
    try {
      const severity = $('alarm-log-severity') ? $('alarm-log-severity').value : undefined;
      const acked = $('alarm-log-acked') ? ($('alarm-log-acked').checked ? true : undefined) : undefined;
      const q = { limit: state.alarmLog.limit, offset: state.alarmLog.offset };
      if (severity) q.severity = severity;
      if (acked !== undefined) q.acked = acked;

      const data = await api.get('/admin/alarms', { query: q });
      state.alarmLog.items = Array.isArray(data.items) ? data.items : [];
      state.alarmLog.total = data.total || 0;

      const tbody = $('alarm-log-table').querySelector('tbody');
      tbody.innerHTML = '';
      for (const a of state.alarmLog.items) {
        const tr = document.createElement('tr');
        tr.appendChild(el('td', {}, [new Date(a.ts).toLocaleString()]));
        tr.appendChild(el('td', {}, [a.severity || '']));
        tr.appendChild(el('td', {}, [a.message || '']));
        tr.appendChild(el('td', {}, [a.source || '']));
        tr.appendChild(el('td', {}, [a.acked ? (a.acked_at ? `Yes (${new Date(a.acked_at).toLocaleString()})` : 'Yes') : 'No']));

        const actions = el('td', {});
        if (!a.acked && can(state.perms, 'alarms:admin')) {
          const ackBtn = el('button', { class: 'btn', text: 'Acknowledge' });
          ackBtn.addEventListener('click', async () => {
            try {
              await api.post(`/alarms/${a.alarm_id}/ack`, { json: {} });
              toast('Alarm acknowledged');
              await loadAlarmLog();
            } catch (e) {
              toast(String(e), 'error');
            }
          });
          actions.appendChild(ackBtn);
        }
        tr.appendChild(actions);
        tbody.appendChild(tr);
      }

      setStatus('alarm-log-status', `Showing ${state.alarmLog.items.length} of ${state.alarmLog.total}`, 'ok');
    } catch (e) {
      setStatus('alarm-log-status', `Error loading alarm log: ${e}`, 'error');
    }
  }

  document.addEventListener('click', (ev) => {
    const tabBtn = ev.target?.closest?.("button[data-log-tab]");
    if (tabBtn) {
      const tab = String(tabBtn.dataset.logTab || "alarm");
      setLogsTab(tab);
      if (state.logs.activeTab === "command") loadCommandLog();
      else loadAlarmLog();
      return;
    }
    if (ev.target && ev.target.id === 'btn-alarm-log-refresh') {
      loadAlarmLog();
    }
    if (ev.target && ev.target.id === 'btn-command-log-refresh') {
      loadCommandLog(true);
    }
  });

  document.addEventListener('change', (ev) => {
    if (ev.target && (ev.target.id === 'alarm-log-severity' || ev.target.id === 'alarm-log-acked')) {
      loadAlarmLog();
      return;
    }
    if (ev.target && (ev.target.id === 'command-log-status-filter' || ev.target.id === 'command-log-failed-only')) {
      renderCommandLog();
      updateCommandLogShowingStatus();
    }
  });

  document.addEventListener('input', (ev) => {
    if (ev.target && ev.target.id === 'command-log-search') {
      renderCommandLog();
      updateCommandLogShowingStatus();
    }
  });

  function commandLogSortDesc(a, b) {
    const ta = Date.parse(a.time || "") || 0;
    const tb = Date.parse(b.time || "") || 0;
    return tb - ta;
  }

  function normalizeCommandLogItem(msg) {
    const cmd = msg?.command || {};
    const evt = msg?.event || {};
    return {
      command_id: cmd.command_id,  // Keep for deduplication
      time: evt.ts || cmd.time || new Date().toISOString(),
      plc: cmd.plc || cmd.plc_name || "",
      container: cmd.container || "",
      equipment: cmd.equipment || "",
      data_point_label: cmd.data_point_label || cmd.datapoint || "",
      bit_label: cmd.bit_label,
      bit: cmd.bit,
      value: cmd.value,
      status: evt.status || cmd.status || "",
      attempts: Number(cmd.attempts || 0),
      username: cmd.username || "",
      client_ip: cmd.client_ip || "",
      error_message: evt.message || cmd.error_message || "",
    };
  }

  function upsertCommandLogItem(item) {
    if (!item || !item.command_id) return;
    const rows = state.commandLog.items || [];
    const idx = rows.findIndex((r) => r.command_id === item.command_id);
    if (idx >= 0) rows[idx] = { ...rows[idx], ...item };
    else rows.unshift(item);
    rows.sort(commandLogSortDesc);
    if (rows.length > state.commandLog.limit) rows.length = state.commandLog.limit;
    state.commandLog.items = rows;
    state.commandLog.total = Math.max(Number(state.commandLog.total || 0), rows.length);
  }

  function getCommandLogFilters() {
    const search = String($("command-log-search")?.value || "").trim().toLowerCase();
    const status = String($("command-log-status-filter")?.value || "").trim().toLowerCase();
    const failedOnly = !!$("command-log-failed-only")?.checked;
    return { search, status, failedOnly };
  }

  function applyCommandLogFilters(rows) {
    const list = Array.isArray(rows) ? rows : [];
    const { search, status, failedOnly } = getCommandLogFilters();
    return list.filter((r) => {
      const rowStatus = String(r?.status || "").trim().toLowerCase();
      if (failedOnly && rowStatus !== "failed") return false;
      if (status && rowStatus !== status) return false;
      if (!search) return true;
      const haystack = [
        String(r?.plc || ""),
        String(r?.container || ""),
        String(r?.equipment || ""),
        String(r?.data_point_label || ""),
        String(r?.bit_label || ""),
        String(r?.status || ""),
        String(r?.username || ""),
        String(r?.client_ip || ""),
        String(r?.value ?? ""),
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(search);
    });
  }

  function updateCommandLogShowingStatus(prefix = "Showing") {
    const allRows = Array.isArray(state.commandLog.items) ? state.commandLog.items : [];
    const shownRows = applyCommandLogFilters(allRows);
    setStatus("command-log-status", `${prefix} ${shownRows.length} of ${state.commandLog.total || allRows.length}`, "ok");
  }

  function renderCommandLog() {
    const table = $("command-log-table");
    if (!table) return;
    const tbody = table.querySelector("tbody");
    if (!tbody) return;
    tbody.innerHTML = "";

    const allRows = Array.isArray(state.commandLog.items) ? state.commandLog.items : [];
    const rows = applyCommandLogFilters(allRows);
    if (!rows.length) {
      tbody.appendChild(
        el("tr", {}, [
          el("td", { colspan: "11", class: "muted", text: allRows.length ? "No command events match filters." : "No command events yet." }),
        ])
      );
      return;
    }

    for (const r of rows) {
      const tr = el("tr", {}, [
        el("td", { text: r.time ? new Date(r.time).toLocaleString() : "" }),
        el("td", { text: String(r.plc || "") }),
        el("td", { text: String(r.container || "") }),
        el("td", { text: String(r.equipment || "") }),
        el("td", { text: String(r.data_point_label || "") }),
        el("td", { text: String(r.bit_label || "") }),
        el("td", { text: String(r.value !== undefined && r.value !== null ? r.value : "") }),
        el("td", { text: String(r.status || "") }),
        el("td", { text: String(r.attempts ?? "") }),
        el("td", { text: String(r.username || "") }),
        el("td", { text: String(r.client_ip || "") }),
      ]);
      tbody.appendChild(tr);
    }
  }

  function onCommandLogMessage(msg) {
    if (!msg || typeof msg !== "object") return;
    if (msg.type === "snapshot" && msg.channel === "commands") {
      const items = Array.isArray(msg.items) ? msg.items.map(normalizeCommandLogItem) : [];
      state.commandLog.items = items.sort(commandLogSortDesc).slice(0, state.commandLog.limit);
      state.commandLog.total = state.commandLog.items.length;
      renderCommandLog();
      updateCommandLogShowingStatus("Live");
      return;
    }
    if (msg.type === "command_log") {
      upsertCommandLogItem(normalizeCommandLogItem(msg));
      renderCommandLog();
      updateCommandLogShowingStatus("Live");
    }
  }

  function scheduleCommandLogReconnect() {
    if (state.commandLog.ws.retryTimer) return;
    state.commandLog.ws.retryTimer = window.setTimeout(() => {
      state.commandLog.ws.retryTimer = null;
      startCommandLogStream();
    }, 3000);
  }

  function startCommandLogStream() {
    if (!(can(state.perms, "command:read") || can(state.perms, "command:write") || can(state.perms, "command:*"))) {
      return;
    }
    if (state.commandLog.ws.socket && state.commandLog.ws.socket.readyState <= 1) return;

    const token = authStore.accessToken();
    if (!token) return;

    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const wsUrl = `${proto}://${window.location.host}/ws/commands`;
    const ws = new WebSocket(wsUrl);
    state.commandLog.ws.socket = ws;

    ws.addEventListener("open", () => {
      try {
        ws.send(JSON.stringify({ type: "auth", access_token: token }));
      } catch {
        // ignore
      }
      setStatus("command-log-status", "Connected to command stream…", "");
    });

    ws.addEventListener("message", (ev) => {
      try {
        const msg = JSON.parse(String(ev.data || "{}"));
        onCommandLogMessage(msg);
      } catch {
        // ignore malformed payload
      }
    });

    ws.addEventListener("close", () => {
      state.commandLog.ws.socket = null;
      scheduleCommandLogReconnect();
    });

    ws.addEventListener("error", () => {
      setStatus("command-log-status", "Command stream disconnected. Retrying…", "error");
      try {
        ws.close();
      } catch {
        // ignore
      }
    });
  }

  async function loadCommandLog(force = false) {
    const canRead = can(state.perms, "command:read") || can(state.perms, "command:*");
    if (!force && state.commandLog.items.length) {
      renderCommandLog();
      updateCommandLogShowingStatus();
      startCommandLogStream();
      return;
    }

    if (!canRead) {
      state.commandLog.items = [];
      state.commandLog.total = 0;
      renderCommandLog();
      setStatus("command-log-status", "Live mode enabled (no command:read permission for history).", "ok");
      startCommandLogStream();
      return;
    }

    setStatus("command-log-status", "Loading command history…", "");
    try {
      const statusFilter = String($("command-log-status-filter")?.value || "").trim();
      const failedOnly = !!$("command-log-failed-only")?.checked;
      const queryStatus = statusFilter || (failedOnly ? "failed" : "");
      const data = await api.get('/logs/commands', {
        query: {
          limit: state.commandLog.limit,
          offset: state.commandLog.offset,
          status: queryStatus || undefined,
        },
      });

      const items = Array.isArray(data.items) ? data.items : [];
      state.commandLog.items = items
        .map((r) => ({
          command_id: r.command_id,
          time: r.time,
          plc: r.plc,
          container: r.container,
          equipment: r.equipment,
          data_point_label: r.data_point_label,
          bit_label: r.bit_label,
          bit: r.bit,
          value: r.value,
          status: r.status,
          attempts: Number(r.attempts || 0),
          username: r.username,
          client_ip: r.client_ip,
          error_message: r.error_message,
        }))
        .sort(commandLogSortDesc)
        .slice(0, state.commandLog.limit);
      state.commandLog.total = Number(data.total || state.commandLog.items.length);

      renderCommandLog();
      updateCommandLogShowingStatus();
    } catch (e) {
      setStatus("command-log-status", `Error loading command log: ${e}`, "error");
    }

    startCommandLogStream();
  }

  async function initAdminApp() {
    const meName = $("me-name");
    const logoutBtn = $("logout-btn");

    const existingAuth = authStore.get();
    if (!existingAuth?.access_token || !existingAuth?.refresh_token) {
      authStore.clear();
      window.location.href = "/admin-panel/login";
      return;
    }

    try {
      const me = await api.get("/auth/me");
      state.me = me;
      state.perms = new Set(me.permissions || []);
      if (meName) meName.textContent = me.username || "user";
    } catch {
      authStore.clear();
      window.location.href = "/admin-panel/login";
      return;
    }

    // Hide nav items based on permissions (UX only).
    const navByView = {
      users: can(state.perms, "users:admin"),
      roles: can(state.perms, "roles:admin"),
      plc: can(state.perms, "config:read") || can(state.perms, "config:write"),
      meta: can(state.perms, "config:read") || can(state.perms, "config:write"),
      access: can(state.perms, "users:admin") || can(state.perms, "roles:admin"),
      alarms: can(state.perms, "alarms:admin"),
      logs: can(state.perms, "alarms:admin") || can(state.perms, "command:read") || can(state.perms, "command:write"),
    };
    document.querySelectorAll(".nav-link").forEach((a) => {
      const v = a.getAttribute("data-view");
      if (v && v in navByView) {
        a.classList.toggle("hidden", !navByView[v]);
      }
    });

    if (logoutBtn) {
      logoutBtn.addEventListener("click", async () => {
        try {
          const refresh_token = authStore.refreshToken();
          if (refresh_token) await api.post("/auth/logout", { json: { refresh_token } });
        } catch {
          // ignore
        }
        authStore.clear();
        window.location.href = "/admin-panel/login";
      });
    }

    window.addEventListener("hashchange", () => route());
    startCommandLogStream();
    await route();
  }

  // -----------------------------
  // Users view
  // -----------------------------
  const usersView = (() => {
    let initialized = false;

    function bind() {
      const btnNew = $("btn-user-new");
      const btnRefresh = $("btn-users-refresh");
      const search = $("users-search");
      const filter = $("users-filter-active");
      const table = $("users-table");
      const form = $("form-user");
      const resetForm = $("form-reset-password");

      btnNew?.addEventListener("click", () => openUserModal(null));
      btnRefresh?.addEventListener("click", () => load(true));

      search?.addEventListener("input", () => render());
      filter?.addEventListener("change", () => render());

      table?.addEventListener("click", async (ev) => {
        const btn = ev.target.closest("button[data-action]");
        if (!btn) return;
        const userId = clampInt(btn.dataset.userId, 0);
        const action = btn.dataset.action;
        const user = state.users.find((u) => u.id === userId);
        if (!user) return;

        if (action === "edit") openUserModal(user);
        if (action === "password") openResetPasswordModal(user);
        if (action === "toggle") await toggleActive(user);
        if (action === "access") navigate("access", { principal: "user", id: String(user.id) });
      });

      form?.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        await submitUserForm();
      });

      resetForm?.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        await submitResetPassword();
      });
    }

    async function show() {
      if (!initialized) {
        bind();
        initialized = true;
      }
      await load(false);
    }

    async function load(force) {
      if (!force && state.users.length) {
        render();
        return;
      }
      setStatus("users-status", "Loading…");
      try {
        const users = await api.get("/admin/users");
        state.users = Array.isArray(users) ? users : [];
        setStatus("users-status", `Loaded ${state.users.length} users.`, "ok");
      } catch (err) {
        setStatus("users-status", err?.message || "Failed to load users", "error");
      }
      await ensureRolesForUserEditor();
      render();
    }

    async function ensureRolesForUserEditor() {
      // Role assignment in the user modal uses role names.
      // If the viewer cannot list roles, we keep the selector empty and only allow password/active updates.
      const help = $("user-roles-help");
      const select = $("user-roles");
      if (!select) return;

      if (!can(state.perms, "roles:admin")) {
        help && (help.textContent = "You don't have permission to list roles.");
        select.innerHTML = "";
        select.disabled = true;
        return;
      }

      try {
        const roles = await api.get("/admin/roles");
        state.roles = Array.isArray(roles) ? roles : [];
        select.innerHTML = "";
        for (const r of state.roles) {
          select.appendChild(el("option", { value: r.name, text: r.name }));
        }
        select.disabled = false;
        help && (help.textContent = "");
      } catch (err) {
        help && (help.textContent = err?.message || "Failed to load roles");
        select.innerHTML = "";
        select.disabled = true;
      }
    }

    function filteredUsers() {
      const term = String($("users-search")?.value || "").trim().toLowerCase();
      const filter = String($("users-filter-active")?.value || "all");
      return (state.users || [])
        .filter((u) => {
          if (!term) return true;
          return String(u.username || "").toLowerCase().includes(term);
        })
        .filter((u) => {
          if (filter === "active") return !!u.is_active;
          if (filter === "inactive") return !u.is_active;
          return true;
        });
    }

    function render() {
      const tbody = qs("#users-table tbody");
      if (!tbody) return;
      tbody.innerHTML = "";

      const rows = filteredUsers();
      if (!rows.length) {
        tbody.appendChild(
          el("tr", {}, [
            el("td", { colspan: "5", class: "muted", text: "No users found." }),
          ])
        );
        return;
      }

      for (const u of rows) {
        const rolesText = formatList(u.roles || []);
        const actions = el("div", { class: "actions-group" }, [
          el("button", { class: "btn small", "data-action": "edit", "data-user-id": u.id, text: "Edit" }),
          el("button", {
            class: "btn small",
            "data-action": "password",
            "data-user-id": u.id,
            text: "Password",
          }),
          el("button", {
            class: "btn small",
            "data-action": "toggle",
            "data-user-id": u.id,
            text: u.is_active ? "Deactivate" : "Activate",
          }),
          el("button", {
            class: "btn small",
            "data-action": "access",
            "data-user-id": u.id,
            text: "Access",
          }),
        ]);

        tbody.appendChild(
          el("tr", {}, [
            el("td", { text: String(u.id) }),
            el("td", { text: String(u.username || "") }),
            el("td", { text: u.is_active ? "Yes" : "No" }),
            el("td", { text: rolesText }),
            el("td", { class: "actions-col" }, [actions]),
          ])
        );
      }
    }

    function openUserModal(user) {
      const dlg = $("modal-user");
      const form = $("form-user");
      if (!dlg || !form) return;

      setStatus("modal-user-status", "");
      form.reset();

      const title = $("modal-user-title");
      const usernameInput = form.querySelector('input[name="username"]');
      const passwordInput = form.querySelector('input[name="password"]');
      const activeInput = form.querySelector('input[name="is_active"]');
      const idInput = form.querySelector('input[name="id"]');
      const rolesSelect = $("user-roles");

      if (user) {
        title && (title.textContent = `Edit User #${user.id}`);
        idInput.value = String(user.id);
        usernameInput.value = user.username || "";
        usernameInput.disabled = true;
        activeInput.checked = !!user.is_active;
        passwordInput.required = false;
        passwordInput.value = "";
        // roles
        if (rolesSelect && !rolesSelect.disabled) {
          const roleNames = new Set(user.roles || []);
          for (const opt of Array.from(rolesSelect.options)) {
            opt.selected = roleNames.has(opt.value);
          }
        }
      } else {
        title && (title.textContent = "New User");
        idInput.value = "";
        usernameInput.value = "";
        usernameInput.disabled = false;
        activeInput.checked = true;
        passwordInput.required = true;
        passwordInput.value = "";
        if (rolesSelect && !rolesSelect.disabled) {
          for (const opt of Array.from(rolesSelect.options)) opt.selected = false;
        }
      }

      openDialog(dlg);
    }

    async function submitUserForm() {
      const form = $("form-user");
      if (!form) return;

      const statusEl = $("modal-user-status");
      setStatus(statusEl, "Saving…");

      const fd = new FormData(form);
      const id = String(fd.get("id") || "").trim();
      const username = String(fd.get("username") || "").trim();
      const password = String(fd.get("password") || "");
      const is_active = !!fd.get("is_active");

      const rolesSelect = $("user-roles");
      let roles = null;
      if (rolesSelect && !rolesSelect.disabled) {
        roles = Array.from(rolesSelect.selectedOptions).map((o) => o.value);
      }

      try {
        if (!id) {
          const payload = { username, password, roles: roles || [] };
          await api.post("/admin/users", { json: payload });
          toast("User created");
        } else {
          const userId = clampInt(id, 0);
          const payload = { is_active };
          if (password) payload.password = password;
          if (roles !== null) payload.roles = roles;
          await api.put(`/admin/users/${userId}`, { json: payload });
          toast("User updated");
        }

        closeDialog("modal-user");
        await load(true);
      } catch (err) {
        setStatus(statusEl, err?.message || "Failed to save user", "error");
      }
    }

    function openResetPasswordModal(user) {
      const dlg = $("modal-reset-password");
      const form = $("form-reset-password");
      if (!dlg || !form) return;

      form.reset();
      setStatus("modal-reset-status", "");

      form.querySelector('input[name="id"]').value = String(user.id);
      const label = $("reset-user-label");
      label && (label.textContent = `User: ${user.username} (#${user.id})`);

      openDialog(dlg);
    }

    async function submitResetPassword() {
      const form = $("form-reset-password");
      if (!form) return;

      const statusEl = $("modal-reset-status");
      setStatus(statusEl, "Updating…");

      const fd = new FormData(form);
      const userId = clampInt(fd.get("id"), 0);
      const password = String(fd.get("password") || "");

      try {
        await api.post(`/admin/users/${userId}/reset-password`, { json: { password } });
        toast("Password updated");
        closeDialog("modal-reset-password");
      } catch (err) {
        setStatus(statusEl, err?.message || "Failed to reset password", "error");
      }
    }

    async function toggleActive(user) {
      const next = !user.is_active;
      const msg = next ? `Activate user "${user.username}"?` : `Deactivate user "${user.username}"?`;
      if (!confirmDanger(msg)) return;

      try {
        await api.put(`/admin/users/${user.id}`, { json: { is_active: next } });
        toast(next ? "User activated" : "User deactivated");
        await load(true);
      } catch (err) {
        toast(err?.message || "Failed to update user", "error");
      }
    }

    return { show };
  })();

  // -----------------------------
  // Roles view
  // -----------------------------
  const rolesView = (() => {
    let initialized = false;
    let selectedRoleId = null;

    function bind() {
      const btnNew = $("btn-role-new");
      const btnRefresh = $("btn-roles-refresh");
      const search = $("roles-search");
      const table = $("roles-table");
      const form = $("form-role");
      const btnPermAdd = $("btn-perm-add");
      const btnRoleDelete = $("btn-role-delete");
      const btnRoleAccess = $("btn-role-access");

      btnNew?.addEventListener("click", () => openRoleModal(null));
      btnRefresh?.addEventListener("click", () => load(true));
      search?.addEventListener("input", () => render());

      table?.addEventListener("click", (ev) => {
        const btn = ev.target.closest("button[data-action]");
        if (!btn) {
          // Row select
          const tr = ev.target.closest("tr[data-role-id]");
          if (!tr) return;
          const roleId = clampInt(tr.dataset.roleId, 0);
          selectRole(roleId);
          return;
        }
        const roleId = clampInt(btn.dataset.roleId, 0);
        const action = btn.dataset.action;
        const role = state.roles.find((r) => r.id === roleId);
        if (!role) return;

        if (action === "edit") openRoleModal(role);
        if (action === "access") navigate("access", { principal: "role", id: String(role.id) });
        if (action === "select") selectRole(role.id);
      });

      form?.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        await submitRoleForm();
      });

      btnPermAdd?.addEventListener("click", async () => {
        if (!selectedRoleId) return;
        const input = $("perm-input");
        const perm = String(input?.value || "").trim();
        if (!perm) return;
        await addPermission(selectedRoleId, perm);
        input.value = "";
      });

      btnRoleDelete?.addEventListener("click", async () => {
        if (!selectedRoleId) return;
        const role = state.roles.find((r) => r.id === selectedRoleId);
        if (!role) return;
        if (!confirmDanger(`Delete role "${role.name}"? This cannot be undone.`)) return;

        try {
          await api.delete(`/admin/roles/${role.id}`);
          toast("Role deleted");
          selectedRoleId = null;
          renderRoleEditor(null);
          await load(true);
        } catch (err) {
          toast(err?.message || "Failed to delete role", "error");
        }
      });

      btnRoleAccess?.addEventListener("click", () => {
        if (!selectedRoleId) return;
        navigate("access", { principal: "role", id: String(selectedRoleId) });
      });

      const permList = $("perm-list");
      permList?.addEventListener("click", async (ev) => {
        const btn = ev.target.closest("button[data-perm]");
        if (!btn) return;
        if (!selectedRoleId) return;
        const perm = btn.dataset.perm;
        await removePermission(selectedRoleId, perm);
      });
    }

    async function show() {
      if (!initialized) {
        bind();
        initialized = true;
      }
      await load(false);
    }

    async function load(force) {
      if (!force && state.roles.length) {
        render();
        return;
      }
      setStatus("roles-status", "Loading…");
      try {
        const roles = await api.get("/admin/roles");
        state.roles = Array.isArray(roles) ? roles : [];
        setStatus("roles-status", `Loaded ${state.roles.length} roles.`, "ok");
      } catch (err) {
        setStatus("roles-status", err?.message || "Failed to load roles", "error");
        state.roles = [];
      }
      render();
      // re-select after refresh if possible
      if (selectedRoleId) selectRole(selectedRoleId, { silent: true });
    }

    function filteredRoles() {
      const term = String($("roles-search")?.value || "").trim().toLowerCase();
      return (state.roles || []).filter((r) => {
        if (!term) return true;
        return String(r.name || "").toLowerCase().includes(term);
      });
    }

    function render() {
      const tbody = qs("#roles-table tbody");
      if (!tbody) return;
      tbody.innerHTML = "";

      const roles = filteredRoles();
      if (!roles.length) {
        tbody.appendChild(el("tr", {}, [el("td", { colspan: "5", class: "muted", text: "No roles found." })]));
        return;
      }

      for (const r of roles) {
        const permCount = (r.permissions || []).length;
        const actions = el("div", { class: "actions-group" }, [
          el("button", { class: "btn small", "data-action": "select", "data-role-id": r.id, text: "Select" }),
          el("button", { class: "btn small", "data-action": "edit", "data-role-id": r.id, text: "Edit" }),
          el("button", { class: "btn small", "data-action": "access", "data-role-id": r.id, text: "Access" }),
        ]);

        const tr = el(
          "tr",
          { dataset: { roleId: String(r.id) } },
          [
            el("td", { text: String(r.id) }),
            el("td", { text: String(r.name || "") }),
            el("td", { text: String(r.description || "") }),
            el("td", { text: String(permCount) }),
            el("td", { class: "actions-col" }, [actions]),
          ]
        );
        if (selectedRoleId === r.id) tr.classList.add("selected");
        tbody.appendChild(tr);
      }
    }

    function selectRole(roleId, opts = {}) {
      selectedRoleId = roleId;
      const role = state.roles.find((r) => r.id === roleId) || null;
      renderRoleEditor(role);
      if (!opts.silent) toast(`Selected role: ${role?.name || roleId}`);
    }

    function renderRoleEditor(role) {
      const editor = $("role-editor");
      const name = $("role-editor-name");
      const permList = $("perm-list");
      const permInput = $("perm-input");

      if (!editor || !name || !permList || !permInput) return;

      if (!role) {
        editor.classList.add("hidden");
        name.textContent = "";
        permList.innerHTML = "";
        permInput.value = "";
        return;
      }

      editor.classList.remove("hidden");
      name.textContent = `${role.name} (#${role.id})`;
      permList.innerHTML = "";

      for (const perm of role.permissions || []) {
        const li = el("li", {}, [
          el("span", { text: perm }),
          el("button", {
            class: "btn small danger",
            type: "button",
            dataset: { perm },
            text: "×",
          }),
        ]);
        permList.appendChild(li);
      }
    }

    function openRoleModal(role) {
      const dlg = $("modal-role");
      const form = $("form-role");
      if (!dlg || !form) return;

      setStatus("modal-role-status", "");
      form.reset();

      const title = $("modal-role-title");
      const idInput = form.querySelector('input[name="id"]');
      const nameInput = form.querySelector('input[name="name"]');
      const descInput = form.querySelector('input[name="description"]');

      if (role) {
        title && (title.textContent = `Edit Role #${role.id}`);
        idInput.value = String(role.id);
        nameInput.value = role.name || "";
        descInput.value = role.description || "";
      } else {
        title && (title.textContent = "New Role");
        idInput.value = "";
        nameInput.value = "";
        descInput.value = "";
      }

      openDialog(dlg);
    }

    async function submitRoleForm() {
      const form = $("form-role");
      if (!form) return;

      const statusEl = $("modal-role-status");
      setStatus(statusEl, "Saving…");

      const fd = new FormData(form);
      const id = String(fd.get("id") || "").trim();
      const name = String(fd.get("name") || "").trim();
      const description = String(fd.get("description") || "").trim();

      try {
        if (!id) {
          await api.post("/admin/roles", { json: { name, description: description || null } });
          toast("Role created");
        } else {
          const roleId = clampInt(id, 0);
          await api.put(`/admin/roles/${roleId}`, { json: { name, description: description || null } });
          toast("Role updated");
        }

        closeDialog("modal-role");
        await load(true);
      } catch (err) {
        setStatus(statusEl, err?.message || "Failed to save role", "error");
      }
    }

    async function addPermission(roleId, perm) {
      try {
        await api.post(`/admin/roles/${roleId}/permissions`, { json: { permission: perm } });
        toast("Permission added");
        await load(true);
        selectRole(roleId, { silent: true });
      } catch (err) {
        toast(err?.message || "Failed to add permission", "error");
      }
    }

    async function removePermission(roleId, perm) {
      if (!confirmDanger(`Remove permission "${perm}"?`)) return;
      try {
        await api.delete(`/admin/roles/${roleId}/permissions/${encodeURIComponent(perm)}`);
        toast("Permission removed");
        await load(true);
        selectRole(roleId, { silent: true });
      } catch (err) {
        toast(err?.message || "Failed to remove permission", "error");
      }
    }

    return { show };
  })();

  // -----------------------------
  // PLC Builder view
  // -----------------------------
  const plcView = (() => {
    let initialized = false;
    let dpLinkContext = null;
    let dpLinkResults = [];

    function bind() {
      $("btn-plc-refresh")?.addEventListener("click", () => loadTree(true));
      $("btn-plc-add")?.addEventListener("click", async () => {
        await openPlcModal();
      });
      $("cfg-search")?.addEventListener("input", () => renderTree());

      $("cfg-tree")?.addEventListener("click", (ev) => {
        const twisty = ev.target.closest("button[data-toggle]");
        if (twisty) {
          ev.stopPropagation();
          const key = twisty.dataset.toggle;
          if (state.cfgCollapsed.has(key)) state.cfgCollapsed.delete(key);
          else state.cfgCollapsed.add(key);
          renderTree();
          return;
        }

        const nodeEl = ev.target.closest("[data-node-key]");
        if (!nodeEl) return;
        const key = nodeEl.dataset.nodeKey;
        const info = state.cfgIndex.get(key);
        if (!info) return;
        if (info.type === "datapoint") return; // plc builder selects only structure nodes

        state.cfgSelectedKey = key;
        renderTree();
        renderDetails();
      });

      $("btn-node-save")?.addEventListener("click", () => saveSelectedNode());
      $("btn-node-delete")?.addEventListener("click", () => deleteSelectedNode());

      $("form-plc")?.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        await createPlc();
      });

      $("form-add-child")?.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        await submitChildForm();
      });

      $("btn-dp-link-search")?.addEventListener("click", async (ev) => {
        ev.preventDefault();
        await searchInstrumentsForDatapointLink();
      });

      $("dp-link-search")?.addEventListener("input", () => {
        renderDatapointLinkSelect(dpLinkResults);
      });

      $("form-link-instrument")?.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        await saveDatapointInstrumentLink();
      });
    }

    async function openPlcModal() {
      const form = $("form-plc");
      if (!form) return;
      try {
        await ensureMetaLoaded(false);
      } catch {
        // keep PLC create usable without groups
      }

      const groupSel = form.querySelector('select[name="groupId"]');
      if (groupSel) {
        groupSel.innerHTML = "";
        groupSel.appendChild(el("option", { value: "", text: "(none)" }));
        for (const g of state.meta.groups || []) {
          groupSel.appendChild(el("option", { value: String(g.id), text: g.name }));
        }
      }
      openDialog("modal-plc");
    }

    function getSelectedEquipmentInfo() {
      if (!state.cfgSelectedKey) return null;
      const info = state.cfgIndex.get(state.cfgSelectedKey);
      if (!info || info.type !== "equipment") return null;
      return info;
    }

    function populateDatapointLinkSelect(items) {
      const sel = $("dp-link-instrument");
      if (!sel) return;
      const list = Array.isArray(items) ? items : [];
      const currentInstrumentId = clampInt(dpLinkContext?.current?.instrumentId, 0);

      sel.innerHTML = "";
      sel.appendChild(el("option", { value: "", text: list.length ? "Select instrument" : "No instruments found" }));
      for (const inst of list) {
        const instrumentId = clampInt(inst.id, 0);
        if (!instrumentId) continue;
        const code = String(inst.label || "").trim();
        const name = String(inst.meta?.name || "").trim();
        const serial = String(inst.serial_number || "").trim();
        const text = [code, name, serial ? `SN:${serial}` : ""].filter(Boolean).join(" • ");
        sel.appendChild(el("option", { value: String(instrumentId), text }));
      }
      sel.disabled = list.length === 0;
      if (currentInstrumentId) sel.value = String(currentInstrumentId);
    }

    function renderDatapointLinkSelect(sourceItems) {
      const term = String($("dp-link-search")?.value || "").trim().toLowerCase();
      const items = Array.isArray(sourceItems) ? sourceItems : [];
      if (!term) {
        populateDatapointLinkSelect(items);
        return;
      }
      const filtered = items.filter((inst) => {
        const code = String(inst.label || "").toLowerCase();
        const name = String(inst.meta?.name || "").toLowerCase();
        const serial = String(inst.serial_number || "").toLowerCase();
        const type = String(inst.instrument_type || "").toLowerCase();
        const model = String(inst.model || "").toLowerCase();
        const location = String(inst.location || "").toLowerCase();
        return (
          code.includes(term) ||
          name.includes(term) ||
          serial.includes(term) ||
          type.includes(term) ||
          model.includes(term) ||
          location.includes(term)
        );
      });
      populateDatapointLinkSelect(filtered);
    }

    async function searchInstrumentsForDatapointLink() {
      if (!dpLinkContext?.equipmentId) return;
      const eqId = clampInt(dpLinkContext.equipmentId, 0);
      const searchText = String($("dp-link-search")?.value || "").trim();

      setStatus("modal-link-instrument-status", "Searching instruments...", "");
      try {
        await refreshDatapointInstrumentLinks(eqId, false);

        const query = { equipment_id: eqId };
        if (searchText) query.q = searchText;
        const rows = await api.get("/maintenance/instruments", { query });
        dpLinkResults = Array.isArray(rows) ? rows : [];

        if (!dpLinkResults.length) {
          const fallbackQuery = {};
          if (searchText) fallbackQuery.q = searchText;
          const fallbackRows = await api.get("/maintenance/instruments", { query: fallbackQuery });
          dpLinkResults = Array.isArray(fallbackRows) ? fallbackRows : [];
        }

        const mappedIds = state.mappedInstrumentIds || new Set();
        dpLinkResults = dpLinkResults.filter((inst) => !mappedIds.has(clampInt(inst.id, 0)));

        // API already applies q filtering; render returned rows directly to avoid client-side over-filtering.
        populateDatapointLinkSelect(dpLinkResults);
        setStatus("modal-link-instrument-status", `Found ${dpLinkResults.length} instrument(s).`, "ok");
      } catch (err) {
        dpLinkResults = [];
        populateDatapointLinkSelect([]);
        setStatus("modal-link-instrument-status", err?.message || "Failed to search instruments", "error");
      }
    }

    async function saveDatapointInstrumentLink() {
      const eqId = clampInt(dpLinkContext?.equipmentId, 0);
      const dpId = clampInt(dpLinkContext?.datapointId, 0);
      const selectedInstrumentId = clampInt($("dp-link-instrument")?.value || 0, 0);
      if (!eqId || !dpId) return;
      if (!selectedInstrumentId) {
        setStatus("modal-link-instrument-status", "Select an instrument.", "error");
        return;
      }

      setStatus("modal-link-instrument-status", "Linking...", "");
      try {
        const currentMap = state.datapointInstrumentLinksByEquipment.get(eqId)?.get(dpId) || null;

        const selectedMappingsResp = await api.get(`/maintenance/instruments/${selectedInstrumentId}/datapoints`);
        const selectedMappings = Array.isArray(selectedMappingsResp) ? selectedMappingsResp : [];

        for (const mapping of selectedMappings) {
          const mapId = clampInt(mapping.id, 0);
          const mappedDp = clampInt(mapping.cfg_data_point_id, 0);
          if (!mapId) continue;
          if (mappedDp !== dpId) {
            await api.delete(`/maintenance/instruments/${selectedInstrumentId}/datapoints/${mapId}`);
          }
        }

        if (currentMap && clampInt(currentMap.instrumentId, 0) !== selectedInstrumentId && clampInt(currentMap.mapId, 0)) {
          await api.delete(`/maintenance/instruments/${clampInt(currentMap.instrumentId, 0)}/datapoints/${clampInt(currentMap.mapId, 0)}`);
        }

        const exactExists = selectedMappings.some((m) => clampInt(m.cfg_data_point_id, 0) === dpId);
        if (!exactExists || (currentMap && clampInt(currentMap.instrumentId, 0) !== selectedInstrumentId)) {
          await api.post(`/maintenance/instruments/${selectedInstrumentId}/datapoints`, {
            json: {
              cfg_data_point_id: dpId,
              role: "process",
            },
          });
        }

        setStatus("modal-link-instrument-status", "Instrument linked successfully.", "ok");
        toast("Instrument linked");
        closeDialog("modal-link-instrument");
        await refreshDatapointInstrumentLinks(eqId, true);
      } catch (err) {
        setStatus("modal-link-instrument-status", err?.message || "Failed to link instrument", "error");
      }
    }

    async function unlinkDatapointInstrumentLink(eqId, dpId) {
      const links = state.datapointInstrumentLinksByEquipment.get(eqId) || new Map();
      const currentMap = links.get(dpId) || null;
      if (!currentMap) return;

      const instrumentCode = String(currentMap.instrumentCode || `#${currentMap.instrumentId}`);
      if (!confirmDanger(`Unlink instrument '${instrumentCode}' from this data point?`)) return;

      try {
        await api.delete(`/maintenance/instruments/${clampInt(currentMap.instrumentId, 0)}/datapoints/${clampInt(currentMap.mapId, 0)}`);
        toast("Instrument unlinked");
        await refreshDatapointInstrumentLinks(eqId, true);
      } catch (err) {
        toast(err?.message || "Failed to unlink instrument", "error");
      }
    }

    async function openDatapointLinkModal(eqInfo, datapoint) {
      const eqId = clampInt(eqInfo?.id, 0);
      const dpId = clampInt(datapoint?.id, 0);
      if (!eqId || !dpId) return;

      const currentMap = state.datapointInstrumentLinksByEquipment.get(eqId)?.get(dpId) || null;
      dpLinkContext = {
        equipmentId: eqId,
        datapointId: dpId,
        datapointLabel: String(datapoint?.label || `DataPoint #${dpId}`),
        current: currentMap,
      };

      const label = $("dp-link-datapoint-label");
      if (label) label.value = dpLinkContext.datapointLabel;
      const current = $("dp-link-current");
      if (current) {
        current.textContent = currentMap ? `${currentMap.instrumentCode} — ${currentMap.instrumentName || ""}` : "Not linked";
      }

      const search = $("dp-link-search");
      if (search) search.value = "";
      setStatus("modal-link-instrument-status", "", "");

      openDialog("modal-link-instrument");
      await searchInstrumentsForDatapointLink();
    }

    async function refreshDatapointInstrumentLinks(eqId, force = false) {
      if (!force && state.datapointInstrumentLinksByEquipment.has(eqId)) return state.datapointInstrumentLinksByEquipment.get(eqId);
      if (state.datapointInstrumentLinkLoading.has(eqId)) return state.datapointInstrumentLinksByEquipment.get(eqId);

      state.datapointInstrumentLinkLoading.add(eqId);
      try {
        const eqInfo = state.cfgIndex.get(`equipment:${eqId}`);
        const eqDatapointIds = new Set(
          (Array.isArray(eqInfo?.raw?.datapoints) ? eqInfo.raw.datapoints : [])
            .map((dp) => clampInt(dp.id, 0))
            .filter((id) => id > 0)
        );

        const instruments = await api.get("/maintenance/instruments");
        const list = Array.isArray(instruments) ? instruments : [];
        const mapByDp = new Map();
        const mappedInstrumentIds = new Set();

        await Promise.all(
          list.map(async (inst) => {
            const instrumentId = clampInt(inst.id, 0);
            let mappings = [];
            try {
              const mapRows = await api.get(`/maintenance/instruments/${instrumentId}/datapoints`);
              mappings = Array.isArray(mapRows) ? mapRows : [];
            } catch {
              mappings = [];
            }

            for (const mapping of mappings) {
              const dpId = clampInt(mapping.cfg_data_point_id, 0);
              if (!dpId) continue;
              mappedInstrumentIds.add(instrumentId);
              if (!eqDatapointIds.has(dpId) || mapByDp.has(dpId)) continue;
              mapByDp.set(dpId, {
                instrumentId,
                instrumentCode: String(inst.label || ""),
                instrumentName: String(inst.meta?.name || ""),
                mapId: clampInt(mapping.id, 0),
                role: String(mapping.role || ""),
              });
            }
          })
        );

        state.mappedInstrumentIds = mappedInstrumentIds;
        state.datapointInstrumentLinksByEquipment.set(eqId, mapByDp);
      } catch (err) {
        console.error("Failed to refresh datapoint links:", err);
        state.mappedInstrumentIds = new Set();
        state.datapointInstrumentLinksByEquipment.set(eqId, new Map());
      } finally {
        state.datapointInstrumentLinkLoading.delete(eqId);
      }

      const selected = getSelectedEquipmentInfo();
      if (selected && clampInt(selected.id, 0) === eqId) renderDetails();

      return state.datapointInstrumentLinksByEquipment.get(eqId) || new Map();
    }

    async function show() {
      if (!initialized) {
        bind();
        initialized = true;
      }
      await loadTree(false);
    }

    async function loadTree(force) {
      if (!force && state.cfgTree.length) {
        renderTree();
        renderDetails();
        return;
      }
      setStatus("plc-status", "Loading…");
      try {
        const tree = await api.get("/api/config/tree");
        state.cfgTree = Array.isArray(tree) ? tree : [];
        indexTree();
        if (state.cfgSelectedKey && !state.cfgIndex.has(state.cfgSelectedKey)) state.cfgSelectedKey = null;
        setStatus("plc-status", `Loaded ${state.cfgTree.length} PLC(s).`, "ok");
      } catch (err) {
        state.cfgTree = [];
        state.cfgIndex = new Map();
        setStatus("plc-status", err?.message || "Failed to load tree", "error");
      }
      renderTree();
      renderDetails();
    }

    function indexTree() {
      state.cfgIndex = new Map();

      const add = (type, raw, parentKey = null) => {
        const id = clampInt(raw.id, 0);
        const key = `${type}:${id}`;
        const name =
          type === "plc"
            ? raw.name
            : type === "container"
            ? raw.name
            : type === "equipment"
            ? raw.name
            : type === "datapoint"
            ? raw.label
            : String(raw.name || raw.label || "");
        state.cfgIndex.set(key, { key, type, id, name, parentKey, raw });
        return key;
      };

      for (const plc of state.cfgTree) {
        const plcKey = add("plc", plc, null);

        for (const dp of plc.datapoints || []) {
          add("datapoint", dp, plcKey);
        }

        for (const c of plc.containers || []) {
          const cKey = add("container", c, plcKey);

          for (const dp of c.datapoints || []) {
            add("datapoint", dp, cKey);
          }

          for (const e of c.equipment || []) {
            const eKey = add("equipment", e, cKey);

            for (const dp of e.datapoints || []) {
              add("datapoint", dp, eKey);
            }
          }
        }
      }
    }

    function treeCounts(plcRaw) {
      let containers = 0;
      let equipment = 0;
      let datapoints = 0;

      for (const c of plcRaw.containers || []) {
        containers += 1;
        datapoints += (c.datapoints || []).length;
        for (const e of c.equipment || []) {
          equipment += 1;
          datapoints += (e.datapoints || []).length;
        }
      }
      datapoints += (plcRaw.datapoints || []).length;
      return { containers, equipment, datapoints };
    }

    function renderTree() {
      const root = $("cfg-tree");
      if (!root) return;

      const term = String($("cfg-search")?.value || "").trim().toLowerCase();
      root.innerHTML = "";

      const build = (node, type, parentKey) => {
        const id = clampInt(node.id, 0);
        const key = `${type}:${id}`;
        const info = state.cfgIndex.get(key);
        if (!info) return null;

        const children = [];
        if (type === "plc") {
          for (const c of node.containers || []) {
            const child = build(c, "container", key);
            if (child) children.push(child);
          }
        } else if (type === "container") {
          for (const e of node.equipment || []) {
            const child = build(e, "equipment", key);
            if (child) children.push(child);
          }
        }

        // Filter logic: show node if it matches or any child matches
        const label = String(info.name || "").toLowerCase();
        const selfMatch = !term || label.includes(term);
        const anyChildMatch = children.some((c) => c._matched);
        const matched = selfMatch || anyChildMatch;
        if (!matched) return null;

        const hasChildren = children.length > 0;

        const left = el("div", { class: "left" }, [
          hasChildren
            ? el("button", { class: "twisty", type: "button", dataset: { toggle: key }, text: state.cfgCollapsed.has(key) ? "▸" : "▾" })
            : el("span", { class: "badge", text: " " }),
          el("span", { class: "title", text: info.name || `${type} ${id}` }),
          el("span", { class: "badge", text: type }),
        ]);

        const metaChildren = [];
        if (type === "plc") {
          const c = treeCounts(node);
          metaChildren.push(el("span", { class: "badge", text: `C:${c.containers}` }));
          metaChildren.push(el("span", { class: "badge", text: `E:${c.equipment}` }));
          metaChildren.push(el("span", { class: "badge", text: `DP:${c.datapoints}` }));
        } else if (type === "container") {
          metaChildren.push(el("span", { class: "badge", text: `type:${node.containerType || ""}` }));
          metaChildren.push(el("span", { class: "badge", text: `E:${(node.equipment || []).length}` }));
          metaChildren.push(el("span", { class: "badge", text: `DP:${(node.datapoints || []).length}` }));
        } else if (type === "equipment") {
          metaChildren.push(el("span", { class: "badge", text: `type:${node.equipmentType || ""}` }));
          metaChildren.push(el("span", { class: "badge", text: `DP:${(node.datapoints || []).length}` }));
        }

        const row = el("div", { class: "node", dataset: { nodeKey: key } }, [
          left,
          el("div", { class: "meta" }, metaChildren),
        ]);

        if (state.cfgSelectedKey === key) row.classList.add("selected");

        const container = el("div", {}, [row]);

        if (hasChildren) {
          const childrenWrap = el("div", { class: "children" }, children.map((c) => c.el));
          if (state.cfgCollapsed.has(key)) childrenWrap.classList.add("hidden");
          container.appendChild(childrenWrap);
        }

        return { el: container, _matched: matched };
      };

      for (const plc of state.cfgTree) {
        const nodeEl = build(plc, "plc", null);
        if (nodeEl) root.appendChild(nodeEl.el);
      }
    }

    function renderDetails() {
      const editor = $("node-editor");
      const statusEl = $("node-status");
      if (!editor) return;

      setStatus(statusEl, "");

      const saveBtn = $("btn-node-save");
      const delBtn = $("btn-node-delete");

      if (!state.cfgSelectedKey) {
        editor.classList.add("muted");
        editor.innerHTML = "Select a node from the tree.";
        saveBtn && (saveBtn.disabled = true);
        delBtn && (delBtn.disabled = true);
        return;
      }

      const info = state.cfgIndex.get(state.cfgSelectedKey);
      if (!info) return;

      saveBtn && (saveBtn.disabled = !can(state.perms, "config:write"));
      delBtn && (delBtn.disabled = !can(state.perms, "config:write"));

      const node = info.raw;

      const header = el("div", { class: "row" }, [
        el("div", {}, [
          el("div", { class: "muted", text: `${info.type.toUpperCase()} #${info.id}` }),
          el("div", { style: "font-weight:700", text: info.name }),
        ]),
        el("div", { class: "actions" }, buildNodeActions(info)),
      ]);

      const form = el("div", { class: "form" }, buildNodeFormFields(info));

      const dpSection =
        info.type === "plc" || info.type === "container" || info.type === "equipment"
          ? renderDatapointsSection(info)
          : null;

      editor.classList.remove("muted");
      editor.innerHTML = "";
      editor.appendChild(header);
      editor.appendChild(form);
      if (dpSection) editor.appendChild(dpSection);
    }

    function buildNodeActions(info) {
      const actions = [];

      if (can(state.perms, "config:write")) {
        if (info.type === "plc") {
          actions.push(
            el("button", {
              class: "btn small",
              type: "button",
              text: "Add Container",
              onclick: () => openChildModal({ childType: "container", parentType: "plc", parentId: info.id }),
            })
          );
        }
        if (info.type === "container") {
          actions.push(
            el("button", {
              class: "btn small",
              type: "button",
              text: "Add Equipment",
              onclick: () => openChildModal({ childType: "equipment", parentType: "container", parentId: info.id }),
            })
          );
        }
        if (info.type === "plc" || info.type === "container" || info.type === "equipment") {
          actions.push(
            el("button", {
              class: "btn small",
              type: "button",
              text: "Add DataPoint",
              onclick: () => openChildModal({ childType: "datapoint", parentType: info.type, parentId: info.id }),
            })
          );
        }
      }

      return actions;
    }

    function buildNodeFormFields(info) {
      const node = info.raw;
      const fields = [];

      const inputRow = (label, inputEl) =>
        el("label", {}, [
          el("span", { class: "muted", text: label }),
          inputEl,
        ]);

      const input = (name, value, attrs = {}) =>
        el("input", { name, value: value ?? "", ...attrs });

      const containerTypeSelect = (selectedValue = "") => {
        const options = [el("option", { value: "", text: "Select type" })];
        const knownValues = new Set();

        const managed = getMetaItems("container", "type") || [];
        for (const item of managed) {
          const name = String(item?.name || "").trim();
          if (!name) continue;
          knownValues.add(name);
          options.push(el("option", { value: name, text: name }));
        }

        const selected = String(selectedValue || "").trim();
        if (selected && !knownValues.has(selected)) {
          options.push(el("option", { value: selected, text: `${selected} (custom)` }));
        }

        const sel = el("select", { name: "type" }, options);
        sel.value = selected;
        return sel;
      };

      const equipmentTypeSelect = (selectedValue = "") => {
        const options = [el("option", { value: "", text: "Select type" })];
        const knownValues = new Set();

        const managed = getMetaItems("equipment", "type") || [];
        for (const item of managed) {
          const name = String(item?.name || "").trim();
          if (!name) continue;
          knownValues.add(name);
          options.push(el("option", { value: name, text: name }));
        }

        const selected = String(selectedValue || "").trim();
        if (selected && !knownValues.has(selected)) {
          options.push(el("option", { value: selected, text: `${selected} (custom)` }));
        }

        const sel = el("select", { name: "type" }, options);
        sel.value = selected;
        return sel;
      };

      if (info.type === "plc") {
        fields.push(inputRow("Name", input("name", node.name)));
        fields.push(inputRow("IP", input("ip", node.ip, { placeholder: "192.168.0.10" })));
        fields.push(
          inputRow(
            "Port",
            input("port", node.port, { type: "number", min: "1", max: "65535" })
          )
        );
        if (!state.meta.loaded) {
          ensureMetaLoaded(false).catch(() => {});
        }
        const plcGroupSel = el(
          "select",
          { name: "groupId" },
          [el("option", { value: "", text: "(none)" })].concat((state.meta.groups || []).map((g) => el("option", { value: String(g.id), text: g.name })))
        );
        plcGroupSel.value = node.groupId ? String(node.groupId) : "";
        fields.push(el("label", {}, [el("span", { class: "muted", text: "Group" }), plcGroupSel]));
      } else if (info.type === "container") {
        fields.push(inputRow("Name", input("name", node.name)));
        ensureMetaDomainLoaded("container", false).catch(() => {});
        fields.push(inputRow("Type", containerTypeSelect(node.containerType || "")));
        // Ensure groups are loaded (async; select will populate if already loaded)
        if (!state.meta.loaded) {
          ensureMetaLoaded(false).catch(() => {});
        }
        const containerGroupSel = el(
          "select",
          { name: "groupId" },
          [el("option", { value: "", text: "(none)" })].concat((state.meta.groups || []).map((g) => el("option", { value: String(g.id), text: g.name })))
        );
        containerGroupSel.value = node.groupId ? String(node.groupId) : "";
        fields.push(el("label", {}, [el("span", { class: "muted", text: "Group" }), containerGroupSel]));
      } else if (info.type === "equipment") {
        fields.push(inputRow("Name", input("name", node.name)));
        ensureMetaDomainLoaded("equipment", false).catch(() => {});
        fields.push(inputRow("Type", equipmentTypeSelect(node.equipmentType || "")));
        // Ensure groups are loaded (async; select will populate if already loaded)
        if (!state.meta.loaded) {
          ensureMetaLoaded(false).catch(() => {});
        }
        const equipmentGroupSel = el(
          "select",
          { name: "groupId" },
          [el("option", { value: "", text: "(none)" })].concat((state.meta.groups || []).map((g) => el("option", { value: String(g.id), text: g.name })))
        );
        equipmentGroupSel.value = node.groupId ? String(node.groupId) : "";
        fields.push(el("label", {}, [el("span", { class: "muted", text: "Group" }), equipmentGroupSel]));
      }

      // Wrap in a grid
      return [
        el("div", { class: "split", style: "grid-template-columns: 1fr 1fr" }, fields),
        el("div", { class: "hint muted" }, [
          can(state.perms, "config:write")
            ? "Tip: Save updates the selected node. Deleting a node may require force delete if it has children/datapoints."
            : "You have read-only configuration access.",
        ]),
      ];
    }

    function renderDatapointsSection(info) {
      const node = info.raw;
      const dps = node.datapoints || [];
      const isEquipment = info.type === "equipment";
      const eqId = clampInt(info.id, 0);
      if (isEquipment && eqId && !state.datapointInstrumentLinksByEquipment.has(eqId) && !state.datapointInstrumentLinkLoading.has(eqId)) {
        refreshDatapointInstrumentLinks(eqId, true).catch(() => {});
      }
      const linksByDp = isEquipment ? state.datapointInstrumentLinksByEquipment.get(eqId) || new Map() : new Map();

      const section = el("div", { style: "margin-top: 14px;" }, []);
      section.appendChild(
        el("div", { class: "row" }, [
          el("h3", { text: "Data Points" }),
          can(state.perms, "config:write")
            ? el("button", {
                class: "btn small primary",
                type: "button",
                text: "Add",
                onclick: () => openChildModal({ childType: "datapoint", parentType: info.type, parentId: info.id }),
              })
            : el("span", { class: "muted", text: "" }),
        ])
      );

      const table = el("table", { class: "table", style: "min-width: 0" }, []);
      table.appendChild(
        el("thead", {}, [
          el("tr", {}, [
            el("th", { text: "ID" }),
            el("th", { text: "Label" }),
            el("th", { text: "Cat" }),
            el("th", { text: "Type" }),
            el("th", { text: "Address" }),
            el("th", { text: "Linked Instrument" }),
            el("th", { text: "" }),
          ]),
        ])
      );

      const tbody = el("tbody");
      if (!dps.length) {
        tbody.appendChild(el("tr", {}, [el("td", { colspan: "7", class: "muted", text: "No datapoints on this node." })]));
      } else {
        for (const dp of dps) {
          const dpId = clampInt(dp.id, 0);
          const link = linksByDp.get(dpId) || null;
          const linkedText = link ? `${link.instrumentCode}${link.instrumentName ? ` — ${link.instrumentName}` : ""}` : "—";

          const actions = el("div", { class: "actions-group" }, [
            el("button", {
              class: "btn small",
              type: "button",
              text: "Edit",
              onclick: () => openChildModal({ childType: "datapoint", parentType: info.type, parentId: info.id, mode: "edit", existing: dp }),
            }),
            can(state.perms, "config:write")
              ? el("button", {
                  class: "btn small",
                  type: "button",
                  text: "Duplicate",
                  onclick: () => openChildModal({ childType: "datapoint", parentType: info.type, parentId: info.id, mode: "duplicate", existing: dp }),
                })
              : null,
            can(state.perms, "config:write")
              ? el("button", {
                  class: `btn small ${link ? "danger" : "primary"}`,
                  type: "button",
                  text: link ? "Unlink" : "Link",
                  disabled: !isEquipment ? "true" : null,
                  onclick: () =>
                    link
                      ? unlinkDatapointInstrumentLink(eqId, dpId)
                      : openDatapointLinkModal(info, dp),
                })
              : null,
            can(state.perms, "config:write")
              ? el("button", {
                  class: "btn small danger",
                  type: "button",
                  text: "Delete",
                  onclick: () => deleteDatapoint(dp),
                })
              : null,
          ]);

          tbody.appendChild(
            el("tr", {}, [
              el("td", { text: String(dp.id) }),
              el("td", { text: String(dp.label || "") }),
              el("td", { text: String(dp.category || "") }),
              el("td", { text: String(dp.type || "") }),
              el("td", { text: String(dp.address || "") }),
              el("td", {}, [
                el("div", { text: linkedText }),
                link ? el("span", { class: "badge ok", text: String(link.role || "process").toUpperCase() }) : null,
              ]),
              el("td", { class: "actions-col" }, [actions]),
            ])
          );
        }
      }
      table.appendChild(tbody);

      const wrap = el("div", { class: "table-wrap" }, [table]);
      section.appendChild(wrap);
      return section;
    }

    async function createPlc() {
      const form = $("form-plc");
      if (!form) return;

      setStatus("modal-plc-status", "Creating…");
      const fd = new FormData(form);
      const name = String(fd.get("name") || "").trim();
      const ip = String(fd.get("ip") || "").trim();
      const port = clampInt(fd.get("port"), 502);
      const groupIdRaw = String(fd.get("groupId") || "").trim();
      const groupId = groupIdRaw ? clampInt(groupIdRaw, 0) : null;

      try {
        await api.post("/api/config/plcs", { json: { name, ip, port, groupId } });
        toast("PLC created");
        closeDialog("modal-plc");
        form.reset();
        await loadTree(true);
      } catch (err) {
        setStatus("modal-plc-status", err?.message || "Failed to create PLC", "error");
      }
    }

    async function openChildModal({ childType, parentType, parentId, mode = "create", existing = null }) {
      const dlg = $("modal-add-child");
      const form = $("form-add-child");
      const fields = $("child-fields");
      const title = $("child-title");
      if (!dlg || !form || !fields || !title) return;

      setStatus("modal-child-status", "");
      form.reset();
      fields.innerHTML = "";

      const isEditMode = mode === "edit";
      form.querySelector('input[name="id"]').value = isEditMode && existing ? String(existing.id || "") : "";
      form.querySelector('input[name="parent_type"]').value = parentType;
      form.querySelector('input[name="parent_id"]').value = String(parentId);
      form.querySelector('input[name="child_type"]').value = childType;

      if (childType === "container") {
        title.textContent = "Add Container";
        fields.appendChild(el("label", {}, [el("span", { class: "muted", text: "Name" }), el("input", { name: "name", required: "true" })]));
        try {
          await ensureMetaDomainLoaded("container", false);
        } catch {
          // keep modal usable even if container type meta load fails
        }

        const typeOptions = [el("option", { value: "", text: "Select type" })];
        for (const row of getMetaItems("container", "type") || []) {
          const v = String(row?.name || "").trim();
          if (!v) continue;
          typeOptions.push(el("option", { value: v, text: v }));
        }
        const typeSel = el("select", { name: "type", required: "true" }, typeOptions);
        fields.appendChild(el("label", {}, [el("span", { class: "muted", text: "Type" }), typeSel]));

        // Allow assigning a datapoint group when creating a container
        try {
          await ensureMetaLoaded(false);
        } catch (err) {
          // keep modal usable even if meta load fails
        }
        const containerGroupSel = el(
          "select",
          { name: "groupId" },
          [el("option", { value: "", text: "(none)" })].concat((state.meta.groups || []).map((g) => el("option", { value: String(g.id), text: g.name })))
        );
        fields.appendChild(el("label", {}, [el("span", { class: "muted", text: "Group" }), containerGroupSel]));
      } else if (childType === "equipment") {
        title.textContent = "Add Equipment";
        fields.appendChild(el("label", {}, [el("span", { class: "muted", text: "Name" }), el("input", { name: "name", required: "true" })]));
        try {
          await ensureMetaDomainLoaded("equipment", false);
        } catch {
          // keep modal usable even if equipment type meta load fails
        }
        const typeOptions = [el("option", { value: "", text: "Select type" })];
        for (const row of getMetaItems("equipment", "type") || []) {
          const v = String(row?.name || "").trim();
          if (!v) continue;
          typeOptions.push(el("option", { value: v, text: v }));
        }
        const typeSel = el("select", { name: "type", required: "true" }, typeOptions);
        fields.appendChild(el("label", {}, [el("span", { class: "muted", text: "Type" }), typeSel]));

        // Allow assigning a datapoint group when creating equipment
        try {
          await ensureMetaLoaded(false);
        } catch (err) {
          // ignore
        }
        const equipmentGroupSel = el(
          "select",
          { name: "groupId" },
          [el("option", { value: "", text: "(none)" })].concat((state.meta.groups || []).map((g) => el("option", { value: String(g.id), text: g.name })))
        );
        fields.appendChild(el("label", {}, [el("span", { class: "muted", text: "Group" }), equipmentGroupSel]));
      } else if (childType === "datapoint") {
        title.textContent = mode === "edit" ? `Edit DataPoint #${existing?.id}` : mode === "duplicate" ? `Duplicate DataPoint #${existing?.id}` : "Add DataPoint";

        // Load dropdown options (classes/units/groups) used by datapoint editor.
        try {
          await ensureMetaLoaded(false);
        } catch (err) {
          // Keep modal functional even if meta options fail to load.
          toast(err?.message || "Failed to load datapoint meta lists", "error");
          state.meta.loaded = false;
          state.meta.classes = [];
          state.meta.units = [];
          state.meta.groups = [];
          state.meta.loadedByDomain.datapoint = false;
          state.meta.catalog.datapoint = { class: [], unit: [], group: [] };
        }

        fields.appendChild(el("label", {}, [el("span", { class: "muted", text: "Label" }), el("input", { name: "label", required: "true", value: existing?.label || "" })]));
        fields.appendChild(el("label", {}, [el("span", { class: "muted", text: "Description" }), el("input", { name: "description", value: existing?.description || "" })]));

        const category = el("select", { name: "category" }, [
          el("option", { value: "read", text: "read" }),
          el("option", { value: "write", text: "write" }),
        ]);
        category.value = existing?.category || "read";

        const typeSel = el("select", { name: "type" }, [
          el("option", { value: "INTEGER", text: "INTEGER" }),
          el("option", { value: "DIGITAL", text: "DIGITAL" }),
          el("option", { value: "REAL", text: "REAL" }),
        ]);
        typeSel.value = existing?.type || "INTEGER";

        fields.appendChild(el("label", {}, [el("span", { class: "muted", text: "Category" }), category]));
        fields.appendChild(el("label", {}, [el("span", { class: "muted", text: "Type" }), typeSel]));
        fields.appendChild(el("label", {}, [el("span", { class: "muted", text: "Address" }), el("input", { name: "address", required: "true", value: existing?.address || "" })]));

        // DB-driven datapoint meta
        const groupSel = el(
          "select",
          { name: "groupId" },
          [el("option", { value: "", text: "(none)" })].concat(
            (state.meta.groups || []).map((g) => el("option", { value: String(g.id), text: g.name }))
          )
        );
        groupSel.value = existing?.groupId ? String(existing.groupId) : "";

        // also allow setting group on container/equipment editors below

        const classSel = el(
          "select",
          { name: "classId" },
          [el("option", { value: "", text: "(none)" })].concat(
            (state.meta.classes || []).map((c) => el("option", { value: String(c.id), text: c.name }))
          )
        );
        classSel.value = existing?.classId ? String(existing.classId) : "";

        const unitSel = el(
          "select",
          { name: "unitId" },
          [el("option", { value: "", text: "(none)" })].concat(
            (state.meta.units || []).map((u) => el("option", { value: String(u.id), text: u.name }))
          )
        );
        unitSel.value = existing?.unitId ? String(existing.unitId) : "";

        const multiplierInput = el("input", {
          name: "multiplier",
          type: "text",
          inputmode: "decimal",
          placeholder: "e.g., 0.5, 1, 2.5",
          value: String(existing?.multiplier ?? 1),
        });

        const classWrap = el("label", {}, [el("span", { class: "muted", text: "Class" }), classSel]);
        const unitWrap = el("label", {}, [el("span", { class: "muted", text: "Unit" }), unitSel]);

        fields.appendChild(el("label", {}, [el("span", { class: "muted", text: "Group" }), groupSel]));
        fields.appendChild(classWrap);
        fields.appendChild(unitWrap);
        fields.appendChild(el("label", {}, [el("span", { class: "muted", text: "Multiplier" }), multiplierInput]));

        // Bit labels editor (only for DIGITAL)
        const bitsWrap = el("div", { id: "bitlabels-wrap" }, []);
        const bitsTitle = el("div", { class: "row" }, [
          el("strong", { text: "Bit Labels" }),
          el("button", { class: "btn small", type: "button", text: "Add Bit", onclick: () => addBitRow(bitsWrap, null, "") }),
        ]);
        bitsWrap.appendChild(bitsTitle);

        const bitsList = el("div", { id: "bitlabels-list", style: "display:grid; gap:8px; margin-top: 8px;" });
        bitsWrap.appendChild(bitsList);

        const existingBits = existing?.bitLabels || {};
        const entries = Object.entries(existingBits).sort((a, b) => clampInt(a[0], 0) - clampInt(b[0], 0));
        if (entries.length) {
          for (const [bit, lbl] of entries) addBitRow(bitsWrap, bit, lbl);
        } else {
          // start empty
          addBitRow(bitsWrap, "0", "");
        }

        const hint = el("div", { class: "hint muted" }, [
          "Bit labels are only used for DIGITAL datapoints. They are stored as a map {bit: label}.",
        ]);
        bitsWrap.appendChild(hint);

        const toggleByType = () => {
          const isDigital = typeSel.value === "DIGITAL";
          bitsWrap.classList.toggle("hidden", !isDigital);

          // class/unit are only allowed for REAL/INTEGER.
          classWrap.classList.toggle("hidden", isDigital);
          unitWrap.classList.toggle("hidden", isDigital);
          classSel.toggleAttribute("disabled", isDigital);
          unitSel.toggleAttribute("disabled", isDigital);

          if (isDigital) {
            classSel.value = "";
            unitSel.value = "";
          }
        };
        typeSel.addEventListener("change", toggleByType);
        toggleByType();

        fields.appendChild(bitsWrap);

        if (mode === "edit") {
          // owner cannot be changed here
        }
      }

      openDialog(dlg);
    }

    function addBitRow(bitsWrap, bit, label) {
      const list = bitsWrap.querySelector("#bitlabels-list");
      if (!list) return;

      const row = el("div", { class: "row", style: "justify-content: flex-start" }, [
        el("input", { name: "bit", style: "width: 90px", placeholder: "bit", value: bit ?? "" }),
        el("input", { name: "bit_label", style: "flex: 1", placeholder: "label", value: label ?? "" }),
        el("button", { class: "btn small danger", type: "button", text: "Remove", onclick: () => row.remove() }),
      ]);

      list.appendChild(row);
    }

    function collectBitLabels(form) {
      const wrap = form.querySelector("#bitlabels-wrap");
      if (!wrap || wrap.classList.contains("hidden")) return null;

      const rows = wrap.querySelectorAll("#bitlabels-list .row");
      const out = {};
      for (const r of rows) {
        const bit = clampInt(r.querySelector('input[name="bit"]')?.value, NaN);
        const label = String(r.querySelector('input[name="bit_label"]')?.value || "").trim();
        if (!Number.isFinite(bit)) continue;
        if (!label) continue;
        out[String(bit)] = label;
      }
      return out;
    }

    async function submitChildForm() {
      const form = $("form-add-child");
      if (!form) return;

      setStatus("modal-child-status", "Saving…");
      const fd = new FormData(form);
      const id = String(fd.get("id") || "").trim();
      const parentType = String(fd.get("parent_type") || "");
      const parentId = clampInt(fd.get("parent_id"), 0);
      const childType = String(fd.get("child_type") || "");

      try {
        if (childType === "container") {
          const name = String(fd.get("name") || "").trim();
          const type = String(fd.get("type") || "").trim() || null;
          const groupIdRaw = String(fd.get("groupId") || "").trim();
          const groupId = groupIdRaw ? clampInt(groupIdRaw, 0) : null;
          await api.post(`/api/config/plcs/${parentId}/containers`, { json: { name, type, groupId } });
          toast("Container added");
        } else if (childType === "equipment") {
          const name = String(fd.get("name") || "").trim();
          const type = String(fd.get("type") || "").trim() || null;
          const groupIdRaw = String(fd.get("groupId") || "").trim();
          const groupId = groupIdRaw ? clampInt(groupIdRaw, 0) : null;
          await api.post(`/api/config/containers/${parentId}/equipment`, { json: { name, type, groupId } });
          toast("Equipment added");
        } else if (childType === "datapoint") {
          const label = String(fd.get("label") || "").trim();
          const description = String(fd.get("description") || "").trim() || null;
          const category = String(fd.get("category") || "read");
          const type = String(fd.get("type") || "INTEGER");
          const address = String(fd.get("address") || "").trim();

          const groupIdRaw = String(fd.get("groupId") || "").trim();
          const classIdRaw = String(fd.get("classId") || "").trim();
          const unitIdRaw = String(fd.get("unitId") || "").trim();

          const groupId = groupIdRaw ? clampInt(groupIdRaw, 0) : null;
          const classId = classIdRaw ? clampInt(classIdRaw, 0) : null;
          const unitId = unitIdRaw ? clampInt(unitIdRaw, 0) : null;

          // Multiplier supports decimal values for scaling (e.g., 0.5, 1.5, 2.0, etc.)
          const multiplier = clampFloat(fd.get("multiplier"), 1.0);

          const bitLabels = collectBitLabels(form);

          const payload = {
            label,
            description,
            category,
            type,
            address,
            groupId,
            classId: type === "DIGITAL" ? null : classId,
            unitId: type === "DIGITAL" ? null : unitId,
            multiplier,
            bitLabels,
          };

          if (!id) {
            const path =
              parentType === "plc"
                ? `/api/config/plcs/${parentId}/data-points`
                : parentType === "container"
                ? `/api/config/containers/${parentId}/data-points`
                : `/api/config/equipment/${parentId}/data-points`;

            await api.post(path, { json: payload });
            toast("Datapoint added");
          } else {
            const dpId = clampInt(id, 0);
            await api.patch(`/api/config/data-points/${dpId}`, {
              json: payload,
            });
            toast("Datapoint updated");
          }
        }

        closeDialog("modal-add-child");
        await loadTree(true);
        renderDetails();
      } catch (err) {
        setStatus("modal-child-status", err?.message || "Failed to save", "error");
      }
    }

    async function deleteDatapoint(dp) {
      if (!confirmDanger(`Delete datapoint "${dp.label}" (#${dp.id})?`)) return;
      try {
        await api.delete(`/api/config/data-points/${dp.id}`);
        toast("Datapoint deleted");
        await loadTree(true);
        renderDetails();
      } catch (err) {
        toast(err?.message || "Failed to delete datapoint", "error");
      }
    }

    async function saveSelectedNode() {
      if (!state.cfgSelectedKey) return;
      if (!can(state.perms, "config:write")) return toast("No config write permission", "error");

      const info = state.cfgIndex.get(state.cfgSelectedKey);
      if (!info) return;

      // Find inputs inside node-editor
      const editor = $("node-editor");
      if (!editor) return;

      const getInput = (name) => editor.querySelector(`input[name="${name}"], select[name="${name}"]`);

      try {
        if (info.type === "plc") {
          const name = String(getInput("name")?.value || "").trim();
          const ip = String(getInput("ip")?.value || "").trim();
          const port = clampInt(getInput("port")?.value, 502);
          const groupIdRaw = String(getInput("groupId")?.value || "").trim();
          const groupId = groupIdRaw ? clampInt(groupIdRaw, 0) : null;
          await api.patch(`/api/config/plcs/${info.id}`, { json: { name, ip, port, groupId } });
          toast("PLC updated");
        } else if (info.type === "container") {
          const name = String(getInput("name")?.value || "").trim();
          const type = String(getInput("type")?.value || "").trim() || null;
          const groupIdRaw = String(getInput("groupId")?.value || "").trim();
          const groupId = groupIdRaw ? clampInt(groupIdRaw, 0) : null;
          await api.patch(`/api/config/containers/${info.id}`, { json: { name, type, groupId } });
          toast("Container updated");
        } else if (info.type === "equipment") {
          const name = String(getInput("name")?.value || "").trim();
          const type = String(getInput("type")?.value || "").trim() || null;
          const groupIdRaw = String(getInput("groupId")?.value || "").trim();
          const groupId = groupIdRaw ? clampInt(groupIdRaw, 0) : null;
          await api.patch(`/api/config/equipment/${info.id}`, { json: { name, type, groupId } });
          toast("Equipment updated");
        }
        await loadTree(true);
        renderDetails();
      } catch (err) {
        setStatus("node-status", err?.message || "Failed to save node", "error");
      }
    }

    async function deleteSelectedNode() {
      if (!state.cfgSelectedKey) return;
      if (!can(state.perms, "config:write")) return toast("No config write permission", "error");

      const info = state.cfgIndex.get(state.cfgSelectedKey);
      if (!info) return;

      if (!confirmDanger(`Delete ${info.type} "${info.name}" (#${info.id})?`)) return;

      const endpoint =
        info.type === "plc"
          ? `/api/config/plcs/${info.id}`
          : info.type === "container"
          ? `/api/config/containers/${info.id}`
          : `/api/config/equipment/${info.id}`;

      try {
        await api.delete(endpoint, { query: { force: false } });
        toast("Deleted");
        state.cfgSelectedKey = null;
        await loadTree(true);
      } catch (err) {
        const msg = err?.message || "";
        if (msg.includes("dependent") || msg.includes("force=true")) {
          if (confirmDanger("This node has dependent resources. Force delete (also deletes its children/datapoints)?")) {
            try {
              await api.delete(endpoint, { query: { force: true } });
              toast("Deleted (force)");
              state.cfgSelectedKey = null;
              await loadTree(true);
            } catch (err2) {
              toast(err2?.message || "Failed to force delete", "error");
            }
          }
        } else {
          toast(err?.message || "Failed to delete", "error");
        }
      }
      renderDetails();
    }

    return { show, refreshDatapointInstrumentLinks };
  })();

  // Shared utility: ensure config tree is loaded
  async function ensureConfigTreeLoaded() {
    if (state.cfgTree.length) return;
    try {
      const tree = await api.get("/api/config/tree");
      state.cfgTree = Array.isArray(tree) ? tree : [];
      plcView && plcView.show; // noop - keep linter quiet
      // build index for name lookups
      // reuse plc view indexer logic
      // (duplicated small part to avoid tight coupling)
      state.cfgIndex = new Map();
      const add = (type, raw, parentKey = null) => {
        const id = clampInt(raw.id, 0);
        const key = `${type}:${id}`;
        const name =
          type === "plc"
            ? raw.name
            : type === "container"
            ? raw.name
            : type === "equipment"
            ? raw.name
            : type === "datapoint"
            ? raw.label
            : String(raw.name || raw.label || "");
        state.cfgIndex.set(key, { key, type, id, name, parentKey, raw });
        return key;
      };
      for (const plc of state.cfgTree) {
        const plcKey = add("plc", plc, null);
        for (const dp of plc.datapoints || []) add("datapoint", dp, plcKey);
        for (const c of plc.containers || []) {
          const cKey = add("container", c, plcKey);
          for (const dp of c.datapoints || []) add("datapoint", dp, cKey);
          for (const e of c.equipment || []) {
            const eKey = add("equipment", e, cKey);
            for (const dp of e.datapoints || []) add("datapoint", dp, eKey);
          }
        }
      }
    } catch (err) {
      console.error("Failed to load configuration tree:", err);
    }
  }

  // -----------------------------
  // Instruments view (UI-only)
  // -----------------------------
  const instrumentsView = (() => {
    let initialized = false;
    let editingInstrumentId = null;
    let currentInstrumentDetail = null;
    let currentInstrumentMappings = [];
    let currentInstrumentCalibrations = [];
    let currentInstrumentSpares = [];
    let currentInstrumentWorkOrders = [];
    let sparesPartsCatalog = [];
    let instrumentsCache = [];
    let instrumentsCacheFiltered = [];
    const instrumentHealthCache = new Map();
    const instrumentHealthLoading = new Set();
    let healthRowObserver = null;
    let pageIndex = 0;
    const PAGE_SIZE = 25;
    // Bulk map state
    let bulkMapInstrumentsWithoutMappings = [];
    let bulkMapSelectedInstrumentId = null;
    let bulkMapDatapoints = [];
    let bulkMapAllMappings = [];
    let bulkMapDpSearch = "";
    const equipmentPathCache = new Map();

    function bind() {
      const btnRefresh = $("btn-instruments-refresh");
      const btnAdd = $("btn-instruments-add");
      const btnBulkMap = $("btn-instruments-bulk-map");
      const bulkMapModal = $("modal-bulk-map");
      const bulkMapDpSearchInput = $("bulk-map-dp-search");
      const form = $("form-instrument");
      const equipmentSel = $("instrument-equipment");
      const table = $("instruments-table");
      const search = $("instrument-search");
      const filterEquipment = $("instrument-filter-equipment");
      const filterType = $("instrument-filter-type");
      const filterStatus = $("instrument-filter-status");
      const filterCalibration = $("instrument-filter-calibration");
      const btnPrev = $("btn-instruments-prev");
      const btnNext = $("btn-instruments-next");
      const detailModal = $("modal-instrument-detail");
      const detailClose = $("btn-instrument-detail-close");
      const mapEquipment = $("instrument-detail-map-equipment");
      const mapSearch = $("instrument-detail-map-search");
      const mapAdd = $("btn-instrument-detail-add-mapping");
      const mapTable = $("instrument-detail-mapping-table");
      const healthRefresh = $("btn-instrument-detail-health-refresh");
      const btnCalibrationAdd = $("btn-calibration-add");
      const calModal = $("modal-add-calibration");
      const calForm = $("form-add-calibration");
      const btnSparesAdd = $("btn-spares-add");
      const spareModal = $("modal-add-spare");
      const spareForm = $("form-add-spare");
      const spareSearch = $("spare-search");
      const spareTable = $("spares-table-body");
      const btnWorkOrderAdd = $("btn-workorder-add");
      const woModal = $("modal-create-workorder");
      const woForm = $("form-create-workorder");
      const woTable = $("workorders-table-body");

      btnRefresh?.addEventListener("click", () => reloadAndRender());
      btnAdd?.addEventListener("click", async () => {
        await openInstrumentModal("create");
      });
      btnBulkMap?.addEventListener("click", async () => {
        await openBulkMapModal();
      });
      bulkMapDpSearchInput?.addEventListener("input", () => {
        bulkMapDpSearch = String($("bulk-map-dp-search")?.value || "").toLowerCase();
        renderBulkMapDatapointsList();
      });
      search?.addEventListener("input", () => {
        pageIndex = 0;
        applyFiltersAndRender();
      });
      filterEquipment?.addEventListener("change", () => {
        pageIndex = 0;
        applyFiltersAndRender();
      });
      filterType?.addEventListener("change", () => {
        pageIndex = 0;
        applyFiltersAndRender();
      });
      filterStatus?.addEventListener("change", () => {
        pageIndex = 0;
        applyFiltersAndRender();
      });
      filterCalibration?.addEventListener("change", () => {
        pageIndex = 0;
        applyFiltersAndRender();
      });
      btnPrev?.addEventListener("click", () => {
        if (pageIndex <= 0) return;
        pageIndex -= 1;
        renderTable();
      });
      btnNext?.addEventListener("click", () => {
        const total = instrumentsCacheFiltered.length;
        if ((pageIndex + 1) * PAGE_SIZE >= total) return;
        pageIndex += 1;
        renderTable();
      });
      equipmentSel?.addEventListener("change", async () => {
        await updateInstrumentEquipmentPathDisplay(clampInt(equipmentSel.value, 0));
      });
      table?.addEventListener("click", onTableClick);
      detailClose?.addEventListener("click", () => closeDialog("modal-instrument-detail"));
      detailModal?.addEventListener("click", async (ev) => {
        const tabButton = ev.target.closest("button[data-tab]");
        if (!tabButton) return;
        const tab = String(tabButton.dataset.tab || "overview");
        setInstrumentDetailTab(tab);
        if (tab === "mapping") {
          try {
            await loadInstrumentMappings();
          } catch (err) {
            setStatus("instrument-detail-mapping-status", err?.message || "Failed to load mappings", "error");
          }
          return;
        }
        if (tab === "health") {
          try {
            await loadAndRenderInstrumentHealth(false);
          } catch (err) {
            setStatus("instrument-detail-health-status", err?.message || "Failed to load health", "error");
          }
          return;
        }
        if (tab === "calibration") {
          try {
            await loadInstrumentCalibrations();
          } catch (err) {
            setStatus("calibration-status", err?.message || "Failed to load calibrations", "error");
          }
          return;
        }
        if (tab === "spares") {
          try {
            await loadInstrumentSpares();
          } catch (err) {
            setStatus("spares-status", err?.message || "Failed to load spares", "error");
          }
          return;
        }
        if (tab === "workorders") {
          try {
            await loadInstrumentWorkOrders();
          } catch (err) {
            setStatus("workorders-status", err?.message || "Failed to load work orders", "error");
          }
        }
      });
      mapEquipment?.addEventListener("change", () => renderMappingSearchResults());
      mapSearch?.addEventListener("input", () => renderMappingSearchResults());
      mapAdd?.addEventListener("click", async () => {
        await addInstrumentMapping();
      });
      healthRefresh?.addEventListener("click", async () => {
        await loadAndRenderInstrumentHealth(true);
      });
      btnCalibrationAdd?.addEventListener("click", () => {
        openAddCalibrationModal();
      });
      calForm?.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        await saveCalibration();
        calForm.reset();
        calModal?.close();
        await loadInstrumentCalibrations();
      });
      btnSparesAdd?.addEventListener("click", async () => {
        await openAddSpareModal();
      });
      spareSearch?.addEventListener("input", () => renderSpareSearchResults());
      spareForm?.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        await addInstrumentSpare();
        spareForm.reset();
        spareModal?.close();
        await loadInstrumentSpares();
      });
      spareTable?.addEventListener("click", async (ev) => {
        const btn = ev.target.closest("button[data-action='unmap-spare']");
        if (!btn) return;
        const spareId = clampInt(btn.dataset.spareId, 0);
        if (!spareId) return;
        await removeInstrumentSpare(spareId);
      });
      btnWorkOrderAdd?.addEventListener("click", async () => {
        await openCreateWorkOrderModal();
      });
      woForm?.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        await createWorkOrder();
        woForm.reset();
        woModal?.close();
        await loadInstrumentWorkOrders();
      });
      woTable?.addEventListener("click", async (ev) => {
        const btn = ev.target.closest("button[data-action='view-workorder']");
        if (!btn) return;
        const woId = clampInt(btn.dataset.woId, 0);
        if (woId) {
          alert(`View work order ${woId} (navigate to maintenance/work_orders/${woId})`);
        }
      });
      mapTable?.addEventListener("click", async (ev) => {
        const btn = ev.target.closest("button[data-action='unlink']");
        if (!btn) return;
        const mapId = clampInt(btn.dataset.mapId, 0);
        if (!mapId) return;
        await unlinkInstrumentMapping(mapId);
      });
      bulkMapModal?.addEventListener("click", async (ev) => {
        const instrBtn = ev.target.closest("button[data-bulk-instrument]");
        if (instrBtn) {
          const instrId = clampInt(instrBtn.dataset.bulkInstrument, 0);
          if (instrId) selectBulkMapInstrument(instrId);
          return;
        }
        const dpBtn = ev.target.closest("button[data-bulk-datapoint]");
        if (dpBtn) {
          const dpId = clampInt(dpBtn.dataset.bulkDatapoint, 0);
          if (dpId) openBulkMapDpRoleSelector(dpId);
          return;
        }
      });

      if (typeof IntersectionObserver === "function") {
        healthRowObserver = new IntersectionObserver(
          (entries) => {
            for (const entry of entries) {
              if (!entry.isIntersecting) continue;
              const node = entry.target;
              const id = clampInt(node?.dataset?.healthCell || 0, 0);
              if (id) {
                loadInstrumentHealthForRow(id, false).catch(() => {});
              }
              healthRowObserver?.unobserve(node);
            }
          },
          { root: null, threshold: 0.1 }
        );
      }

      form?.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        const label = String(form.instrument_code?.value || "").trim();
        if (!label) {
          setStatus("modal-instrument-status", "Instrument code is required.", "error");
          return;
        }
        const equipmentIdStr = String(form.equipment_id?.value || "").trim();
        if (!equipmentIdStr || equipmentIdStr === "0") {
          setStatus("modal-instrument-status", "Equipment is required.", "error");
          return;
        }
        try {
          setStatus("modal-instrument-status", "Saving...", "");
          const payload = buildInstrumentPayload(form);
          console.log("Submitting instrument payload:", payload);
          if (editingInstrumentId) {
            await api.put(`/maintenance/instruments/${editingInstrumentId}`, { json: payload });
          } else {
            await api.post("/maintenance/instruments", { json: payload });
          }
          setStatus("modal-instrument-status", "Saved successfully!", "ok");
          closeDialog("modal-instrument");
          form.reset();
          editingInstrumentId = null;
          await reloadAndRender();
        } catch (err) {
          console.error("Instrument save error:", err);
          setStatus("modal-instrument-status", err?.message || "Failed to save instrument", "error");
        }
      });
    }

    async function populateInstrumentEquipment() {
      const sel = $("instrument-equipment");
      if (!sel) return;
      try {
        // Try loading from maintenance Equipment table first
        let equipment = await api.get("/maintenance/equipment");
        if (!Array.isArray(equipment) || equipment.length === 0) {
          console.log("Equipment table empty, using config tree fallback");
          // Fall back to config tree if Equipment table is empty
          await ensureConfigTreeLoaded();
          equipment = [];
          if (state.cfgTree && Array.isArray(state.cfgTree)) {
            for (const plc of state.cfgTree) {
              const plcName = String(plc.name || "PLC");
              const containers = Array.isArray(plc.containers) ? plc.containers : [];
              for (const c of containers) {
                const cName = String(c.name || "Container");
                const equipmentList = Array.isArray(c.equipment) ? c.equipment : [];
                for (const e of equipmentList) {
                  equipment.push({
                    id: clampInt(e.id, 0),
                    name: `${e.name || "Equipment"} (${plcName} / ${cName})`,
                    equipment_code: "",
                    location: "",
                  });
                }
              }
            }
          }
        }

        sel.innerHTML = "";
        sel.appendChild(el("option", { value: "", text: "-- Select Equipment (required) --" }));
        for (const eq of equipment) {
          const id = clampInt(eq.id, 0);
          const name = String(eq.name || "");
          sel.appendChild(el("option", { value: String(id), text: name }));
        }
        await updateInstrumentEquipmentPathDisplay(clampInt(sel.value, 0));
      } catch (err) {
        console.error("Failed to load equipment:", err);
        setStatus("modal-instrument-status", `Failed to load equipment: ${err?.message || ""}`, "error");
      }
    }

    async function loadEquipmentPath(equipmentId) {
      const id = clampInt(equipmentId, 0);
      if (!id) return [];
      if (equipmentPathCache.has(id)) return equipmentPathCache.get(id) || [];
      try {
        const resp = await api.get(`/maintenance/equipment/${id}/path`);
        const path = Array.isArray(resp?.path) ? resp.path : [];
        equipmentPathCache.set(id, path);
        return path;
      } catch {
        return [];
      }
    }

    async function updateInstrumentEquipmentPathDisplay(equipmentId) {
      const host = $("instrument-equipment-path");
      if (!host) return;
      const id = clampInt(equipmentId, 0);
      if (!id) {
        host.textContent = "Path: -";
        return;
      }
      const path = await loadEquipmentPath(id);
      if (!path.length) {
        host.textContent = "Path: -";
        return;
      }
      const breadcrumb = path.map((n) => String(n?.name || "-")).join(" > ");
      host.textContent = `Path: ${breadcrumb}`;
    }

    async function populateInstrumentEquipmentFilter() {
      const sel = $("instrument-filter-equipment");
      if (!sel) return;

      let options = [];
      try {
        const equipment = await api.get("/maintenance/equipment");
        if (Array.isArray(equipment) && equipment.length) {
          options = equipment.map((eq) => ({
            id: clampInt(eq.id, 0),
            label: String(eq.name || eq.equipment_code || `#${eq.id}`),
          }));
        }
      } catch {
        // fallback below
      }

      if (!options.length) {
        await ensureConfigTreeLoaded();
        if (Array.isArray(state.cfgTree)) {
          for (const plc of state.cfgTree) {
            const plcName = String(plc?.name || "PLC");
            const containers = Array.isArray(plc?.containers) ? plc.containers : [];
            for (const c of containers) {
              const cName = String(c?.name || "Container");
              const equipmentList = Array.isArray(c?.equipment) ? c.equipment : [];
              for (const e of equipmentList) {
                options.push({
                  id: clampInt(e?.id, 0),
                  label: `${String(e?.name || "Equipment")} (${plcName} / ${cName})`,
                });
              }
            }
          }
        }
      }

      const current = String(sel.value || "");
      sel.innerHTML = "";
      sel.appendChild(el("option", { value: "", text: "All Equipment" }));
      const seen = new Set();
      for (const opt of options) {
        const id = clampInt(opt.id, 0);
        if (!id || seen.has(id)) continue;
        seen.add(id);
        sel.appendChild(el("option", { value: String(id), text: String(opt.label || `#${id}`) }));
      }
      sel.value = current;
    }

    function populateTypeFilter() {
      const sel = $("instrument-filter-type");
      if (!sel) return;
      const current = String(sel.value || "");

      const values = new Set();
      for (const inst of instrumentsCache) {
        const type = String(inst?.instrument_type || "").trim();
        if (type) values.add(type);
      }

      sel.innerHTML = "";
      sel.appendChild(el("option", { value: "", text: "All Types" }));
      for (const type of Array.from(values).sort((a, b) => a.localeCompare(b))) {
        sel.appendChild(el("option", { value: type, text: type }));
      }
      sel.value = Array.from(sel.options).some((o) => o.value === current) ? current : "";
    }

    function parseDateSafe(value) {
      if (!value) return null;
      const dt = new Date(String(value));
      return Number.isNaN(dt.getTime()) ? null : dt;
    }

    function matchesCalibrationFilter(inst, calibrationFilter) {
      if (!calibrationFilter) return true;
      const dueRaw = inst?.meta?.calibration_due;
      if (!dueRaw) return true;

      const due = parseDateSafe(dueRaw);
      if (!due) return true;

      const today = new Date();
      today.setHours(0, 0, 0, 0);

      const msPerDay = 24 * 60 * 60 * 1000;
      const diffDays = Math.floor((due.getTime() - today.getTime()) / msPerDay);

      if (calibrationFilter === "overdue") return diffDays < 0;
      if (calibrationFilter === "7d") return diffDays >= 0 && diffDays <= 7;
      if (calibrationFilter === "30d") return diffDays >= 0 && diffDays <= 30;
      return true;
    }

    function applyFiltersAndRender() {
      const searchTerm = String($("instrument-search")?.value || "").trim().toLowerCase();
      const equipmentFilter = String($("instrument-filter-equipment")?.value || "").trim();
      const typeFilter = String($("instrument-filter-type")?.value || "").trim();
      const statusFilter = String($("instrument-filter-status")?.value || "").trim();
      const calibrationFilter = String($("instrument-filter-calibration")?.value || "").trim();

      instrumentsCacheFiltered = instrumentsCache.filter((inst) => {
        const label = String(inst?.label || "");
        const metaName = String(inst?.meta?.name || "");
        const serial = String(inst?.serial_number || "");
        const type = String(inst?.instrument_type || "");
        const model = String(inst?.model || "");
        const location = String(inst?.location || "");

        if (searchTerm) {
          const haystack = [label, metaName, serial, type, model, location].join(" ").toLowerCase();
          if (!haystack.includes(searchTerm)) return false;
        }

        if (equipmentFilter) {
          const eqId = clampInt(inst?.equipment_id, 0);
          if (String(eqId) !== equipmentFilter) return false;
        }

        if (typeFilter && String(type).trim() !== typeFilter) return false;

        if (statusFilter && String(inst?.status || "").trim() !== statusFilter) return false;

        if (!matchesCalibrationFilter(inst, calibrationFilter)) return false;

        return true;
      });

      const maxPage = Math.max(0, Math.ceil(instrumentsCacheFiltered.length / PAGE_SIZE) - 1);
      if (pageIndex > maxPage) pageIndex = maxPage;
      renderTable();
    }

    function defaultHealthQuery() {
      return {
        window_minutes: 10,
        flatline_minutes: 10,
        max_gap_seconds: 30,
      };
    }

    function getHealthQueryFromControls() {
      const base = defaultHealthQuery();
      const windowMinutes = clampInt($("instrument-detail-health-window-minutes")?.value, base.window_minutes);
      const flatlineMinutes = clampInt($("instrument-detail-health-flatline-minutes")?.value, base.flatline_minutes);
      const maxGapSeconds = clampInt($("instrument-detail-health-max-gap-seconds")?.value, base.max_gap_seconds);
      const noiseRaw = String($("instrument-detail-health-noise-std-threshold")?.value || "").trim();
      const noise = noiseRaw ? clampFloat(noiseRaw, 0) : null;

      const query = {
        window_minutes: windowMinutes,
        flatline_minutes: flatlineMinutes,
        max_gap_seconds: maxGapSeconds,
      };
      if (noise && noise > 0) query.noise_std_threshold = noise;
      return query;
    }

    function healthCacheKey(instrumentId, query) {
      const q = query || {};
      return [
        clampInt(instrumentId, 0),
        clampInt(q.window_minutes, 10),
        clampInt(q.flatline_minutes, 10),
        clampInt(q.max_gap_seconds, 30),
        q.noise_std_threshold == null ? "" : String(q.noise_std_threshold),
      ].join("|");
    }

    function hasPvMapping(instrument) {
      const mappings = Array.isArray(instrument?.mapped_datapoints) ? instrument.mapped_datapoints : [];
      return mappings.some((m) => {
        const role = String(m?.role || "").trim().toLowerCase();
        const cfgId = clampInt(m?.cfg_data_point_id, 0);
        return role === "pv" && cfgId > 0;
      });
    }

    function findInstrumentById(instrumentId) {
      const id = clampInt(instrumentId, 0);
      if (!id) return null;
      return (
        instrumentsCache.find((x) => clampInt(x?.id, 0) === id) ||
        instrumentsCacheFiltered.find((x) => clampInt(x?.id, 0) === id) ||
        null
      );
    }

    async function fetchInstrumentHealth(instrumentId, query, force = false) {
      const id = clampInt(instrumentId, 0);
      if (!id) return null;
      const key = healthCacheKey(id, query);

      if (!force && instrumentHealthCache.has(key)) {
        return instrumentHealthCache.get(key);
      }
      if (instrumentHealthLoading.has(key)) return null;

      instrumentHealthLoading.add(key);
      try {
        const data = await api.get(`/maintenance/instruments/${id}/health`, { query });
        instrumentHealthCache.set(key, data || null);
        return data || null;
      } finally {
        instrumentHealthLoading.delete(key);
      }
    }

    function renderHealthTabData(payload) {
      const scoreHost = $("instrument-detail-health-score");
      const flagsHost = $("instrument-detail-health-flags");
      const stats = payload?.simple_stats || {};
      const flags = Array.isArray(payload?.flags) ? payload.flags : [];

      if (scoreHost) {
        scoreHost.innerHTML = "";
        scoreHost.appendChild(renderHealthBadge(payload?.score_0_100, flags));
      }

      if (flagsHost) {
        flagsHost.innerHTML = "";
        if (!flags.length) {
          flagsHost.textContent = "-";
        } else {
          const ul = el("ul", { style: "margin: 0; padding-left: 18px" }, []);
          for (const flag of flags) {
            ul.appendChild(el("li", { text: String(flag) }, []));
          }
          flagsHost.appendChild(ul);
        }
      }

      setDetailValue("instrument-detail-health-last-sample-ts", payload?.last_sample_ts || "-");
      setDetailValue("instrument-detail-health-sample-count", payload?.sample_count ?? "-");
      setDetailValue("instrument-detail-health-stat-min", stats?.min ?? "-");
      setDetailValue("instrument-detail-health-stat-max", stats?.max ?? "-");
      setDetailValue("instrument-detail-health-stat-avg", stats?.avg ?? "-");
      setDetailValue("instrument-detail-health-stat-std", stats?.std ?? "-");
    }

    async function loadAndRenderInstrumentHealth(force = false) {
      const instrumentId = clampInt(currentInstrumentDetail?.id, 0);
      if (!instrumentId) return;
      if (!hasPvMapping(currentInstrumentDetail)) {
        renderHealthTabData({ flags: ["pv_not_mapped"], score_0_100: null, sample_count: 0, simple_stats: {} });
        setStatus("instrument-detail-health-status", "PV datapoint mapping is required to calculate health.", "error");
        return;
      }
      const query = getHealthQueryFromControls();

      setStatus("instrument-detail-health-status", "Loading health...", "");
      try {
        const payload = await fetchInstrumentHealth(instrumentId, query, force);
        renderHealthTabData(payload || {});
        setStatus("instrument-detail-health-status", "Health loaded.", "ok");
      } catch (err) {
        setStatus("instrument-detail-health-status", err?.message || "Failed to load health", "error");
      }
    }

    function renderHealthCellContent(cell, instrumentId) {
      if (!cell) return;
      const id = clampInt(instrumentId, 0);
      const instrument = findInstrumentById(id);
      const mapped = hasPvMapping(instrument);
      const query = defaultHealthQuery();
      const key = healthCacheKey(id, query);
      const cached = instrumentHealthCache.get(key) || null;

      cell.innerHTML = "";
      if (!mapped) {
        cell.appendChild(el("span", { class: "muted", text: "Not mapped" }, []));
      } else if (cached) {
        const flags = Array.isArray(cached?.flags) ? cached.flags : [];
        cell.appendChild(renderHealthBadge(cached?.score_0_100, flags));
      } else if (instrumentHealthLoading.has(key)) {
        cell.appendChild(el("span", { class: "muted", text: "..." }, []));
      } else {
        cell.appendChild(el("span", { class: "muted", text: "-" }, []));
      }

      cell.appendChild(document.createTextNode(" "));
      cell.appendChild(
        el(
          "button",
          {
            class: "btn small",
            type: "button",
            title: mapped ? "Load Health" : "PV mapping required",
            disabled: mapped ? undefined : true,
            dataset: { action: "load-health", id: String(id) },
            text: "⟳",
          },
          []
        )
      );
    }

    async function loadInstrumentHealthForRow(instrumentId, force = false) {
      const id = clampInt(instrumentId, 0);
      if (!id) return;
      const cell = document.querySelector(`#instruments-table [data-health-cell='${id}']`);
      const instrument = findInstrumentById(id);
      if (!hasPvMapping(instrument)) {
        renderHealthCellContent(cell, id);
        return;
      }
      const query = defaultHealthQuery();
      renderHealthCellContent(cell, id);
      try {
        await fetchInstrumentHealth(id, query, force);
      } catch {
        // ignore row-level load errors to keep table rendering resilient
      }
      renderHealthCellContent(cell, id);
    }

    function renderTable() {
      const tableBody = $("instruments-table")?.querySelector("tbody");
      const pagination = $("instruments-pagination");
      const btnPrev = $("btn-instruments-prev");
      const btnNext = $("btn-instruments-next");
      if (!tableBody) return;

      if (healthRowObserver) {
        healthRowObserver.disconnect();
      }

      const start = pageIndex * PAGE_SIZE;
      const rows = instrumentsCacheFiltered.slice(start, start + PAGE_SIZE);

      tableBody.innerHTML = "";
      for (const inst of rows) {
        const id = clampInt(inst?.id, 0);
        const code = String(inst?.label || "");
        const name = String(inst?.meta?.name || "");
        const equipmentName = String(inst?.equipment?.name || "-");
        const type = String(inst?.instrument_type || "");
        const pvDatapoint = String(inst?.mapped_datapoints?.[0]?.label || "-");
        const pvMapped = hasPvMapping(inst);
        const calibrationDueRaw = inst?.meta?.calibration_due;
        const calibrationDue = calibrationDueRaw ? new Date(calibrationDueRaw).toLocaleDateString() : "-";
        
        // Calculate calibration due status
        let calibrationBadgeClass = "";
        if (calibrationDueRaw) {
          const dueDate = new Date(calibrationDueRaw);
          const now = new Date();
          const oneMonthFromNow = new Date(now.getTime() + 30 * 24 * 60 * 60 * 1000);
          
          if (dueDate < now) {
            calibrationBadgeClass = "error"; // Overdue - red
          } else if (dueDate <= oneMonthFromNow) {
            calibrationBadgeClass = "warn"; // Due within 1 month - yellow
          } else {
            calibrationBadgeClass = "ok"; // OK - green
          }
        }
        
        const sparesStatus = String(inst?.meta?.spares_status || "-");
        const status = String(inst?.status || "");

        const healthCell = el("td", { dataset: { healthCell: String(id) } }, []);
        renderHealthCellContent(healthCell, id);

        const row = el("tr", {}, [
          el("td", { text: code }),
          el("td", { text: name }),
          el("td", { text: equipmentName }),
          el("td", { text: type }),
          el("td", { text: pvDatapoint }),
          el("td", {}, [
            el("span", {
              class: `badge ${pvMapped ? "ok" : "warn"}`,
              text: pvMapped ? "PV mapped" : "PV missing",
            }),
          ]),
          healthCell,
          el("td", {}, calibrationBadgeClass ? [
            el("span", { class: `badge ${calibrationBadgeClass}`, text: calibrationDue })
          ] : [calibrationDue]),
          el("td", { text: sparesStatus }),
          el("td", {}, [
            el("span", {
              class: `badge ${status === "active" ? "ok" : "error"}`,
              text: status,
            }),
          ]),
          el("td", {}, [
            el("button", {
              class: "btn small",
              type: "button",
              dataset: { action: "view", id: String(id) },
              text: "View",
            }),
            " ",
            el("button", {
              class: "btn small",
              type: "button",
              dataset: { action: "edit", id: String(id) },
              text: "Edit",
            }),
            " ",
            el("button", {
              class: "btn small",
              type: "button",
              dataset: { action: "duplicate", id: String(id) },
              text: "Duplicate",
            }),
            " ",
            el("button", {
              class: "btn small danger",
              type: "button",
              dataset: { action: "delete", id: String(id) },
              text: "Delete",
            }),
          ]),
        ]);
        tableBody.appendChild(row);
        if (healthRowObserver) {
          healthRowObserver.observe(healthCell);
        } else {
          const rect = healthCell.getBoundingClientRect();
          if (rect.top < window.innerHeight && rect.bottom > 0) {
            loadInstrumentHealthForRow(id, false).catch(() => {});
          }
        }
      }

      const filteredTotal = instrumentsCacheFiltered.length;
      if (pagination) pagination.textContent = `Showing ${rows.length} of ${filteredTotal}`;
      if (btnPrev) btnPrev.disabled = pageIndex <= 0;
      if (btnNext) btnNext.disabled = (pageIndex + 1) * PAGE_SIZE >= filteredTotal;

      setStatus("instruments-status", `Showing ${filteredTotal} instrument(s)`, "ok");
    }

    function setDetailValue(id, value) {
      const node = $(id);
      if (!node) return;
      node.value = String(value ?? "");
    }

    function setInstrumentDetailTab(tab) {
      const tabs = ["overview", "mapping", "health", "calibration", "spares", "workorders"];
      for (const key of tabs) {
        const panel = $(`instrument-detail-tab-${key}`);
        panel?.classList.toggle("hidden", key !== tab);
      }
      const modal = $("modal-instrument-detail");
      const buttons = modal ? modal.querySelectorAll("button[data-tab]") : [];
      for (const btn of buttons) {
        const isActive = String(btn.dataset.tab || "") === tab;
        btn.classList.toggle("primary", isActive);
      }
    }

    function getDatapointAddress(raw) {
      if (!raw || typeof raw !== "object") return "-";
      const value =
        raw.address ??
        raw.register ??
        raw.register_address ??
        raw.modbus_address ??
        raw.offset ??
        raw.channel ??
        null;
      return value === null || typeof value === "undefined" || value === "" ? "-" : String(value);
    }

    function getCfgDatapointInfo(dpId) {
      const node = state.cfgIndex.get(`datapoint:${clampInt(dpId, 0)}`) || null;
      if (!node) {
        return {
          label: "-",
          address: "-",
          type: "-",
          ownerName: "-",
          ownerId: 0,
        };
      }

      let ownerName = "-";
      let ownerId = 0;
      let parentKey = node.parentKey;
      while (parentKey) {
        const parent = state.cfgIndex.get(parentKey);
        if (!parent) break;
        if (parent.type === "equipment") {
          ownerName = String(parent.name || "-");
          ownerId = clampInt(parent.id, 0);
          break;
        }
        parentKey = parent.parentKey;
      }

      return {
        label: String(node.raw?.label || node.name || "-"),
        address: getDatapointAddress(node.raw),
        type: String(node.raw?.type || node.raw?.register_type || "-"),
        ownerName,
        ownerId,
      };
    }

    async function populateMappingEquipmentOptions(defaultEquipmentId = 0) {
      await ensureConfigTreeLoaded();
      const sel = $("instrument-detail-map-equipment");
      if (!sel) return;

      const options = [];
      if (Array.isArray(state.cfgTree)) {
        for (const plc of state.cfgTree) {
          const plcName = String(plc?.name || "PLC");
          const containers = Array.isArray(plc?.containers) ? plc.containers : [];
          for (const c of containers) {
            const cName = String(c?.name || "Container");
            const equipmentList = Array.isArray(c?.equipment) ? c.equipment : [];
            for (const e of equipmentList) {
              options.push({
                id: clampInt(e?.id, 0),
                label: `${String(e?.name || "Equipment")} (${plcName} / ${cName})`,
              });
            }
          }
        }
      }

      const current = String(sel.value || "");
      sel.innerHTML = "";
      sel.appendChild(el("option", { value: "", text: "Select equipment" }));
      const seen = new Set();
      for (const item of options) {
        const id = clampInt(item.id, 0);
        if (!id || seen.has(id)) continue;
        seen.add(id);
        sel.appendChild(el("option", { value: String(id), text: item.label }));
      }

      const preferred = defaultEquipmentId ? String(defaultEquipmentId) : current;
      sel.value = Array.from(sel.options).some((o) => o.value === preferred) ? preferred : "";
    }

    function getEquipmentDatapointsForMapping(equipmentId) {
      const eqId = clampInt(equipmentId, 0);
      if (!eqId) return [];
      const eqNode = state.cfgIndex.get(`equipment:${eqId}`);
      const rawDatapoints = Array.isArray(eqNode?.raw?.datapoints) ? eqNode.raw.datapoints : [];
      return rawDatapoints
        .map((dp) => {
          const dpId = clampInt(dp?.id, 0);
          if (!dpId) return null;
          return {
            id: dpId,
            label: String(dp?.label || `DP-${dpId}`),
            address: getDatapointAddress(dp),
            type: String(dp?.type || dp?.register_type || ""),
          };
        })
        .filter(Boolean);
    }

    function renderMappingSearchResults() {
      const equipmentId = clampInt($("instrument-detail-map-equipment")?.value || 0, 0);
      const term = String($("instrument-detail-map-search")?.value || "").trim().toLowerCase();
      const sel = $("instrument-detail-map-dp");
      if (!sel) return;

      const datapoints = getEquipmentDatapointsForMapping(equipmentId);
      const filtered = term
        ? datapoints.filter((dp) => {
            const haystack = `${dp.label} ${dp.address} ${dp.type}`.toLowerCase();
            return haystack.includes(term);
          })
        : datapoints;

      const current = String(sel.value || "");
      sel.innerHTML = "";
      sel.appendChild(el("option", { value: "", text: filtered.length ? "Select datapoint" : "No datapoints found" }));
      for (const dp of filtered) {
        const text = `${dp.label} • ${dp.address} • ${dp.type || "-"}`;
        sel.appendChild(el("option", { value: String(dp.id), text }));
      }
      sel.value = Array.from(sel.options).some((o) => o.value === current) ? current : "";
    }

    function renderMappingTable() {
      const tbody = $("instrument-detail-mapping-table")?.querySelector("tbody");
      if (!tbody) return;
      tbody.innerHTML = "";

      for (const mapping of currentInstrumentMappings) {
        const mapId = clampInt(mapping?.id, 0);
        const dpId = clampInt(mapping?.cfg_data_point_id, 0);
        const role = String(mapping?.role || "-");
        const dpInfo = getCfgDatapointInfo(dpId);

        tbody.appendChild(
          el("tr", {}, [
            el("td", { text: role }),
            el("td", { text: String(dpId || "-") }),
            el("td", { text: dpInfo.label }),
            el("td", { text: dpInfo.address }),
            el("td", { text: dpInfo.ownerName }),
            el("td", {}, [
              el("button", {
                class: "btn small danger",
                type: "button",
                dataset: { action: "unlink", mapId: String(mapId) },
                text: "Unlink",
              }),
            ]),
          ])
        );
      }

      if (!currentInstrumentMappings.length) {
        tbody.appendChild(el("tr", {}, [el("td", { colspan: "6", class: "muted", text: "No mappings" })]));
      }
    }

    async function loadInstrumentMappings() {
      const instrumentId = clampInt(currentInstrumentDetail?.id, 0);
      if (!instrumentId) return;

      setStatus("instrument-detail-mapping-status", "Loading mappings...", "");
      const rows = await api.get(`/maintenance/instruments/${instrumentId}/datapoints`);
      currentInstrumentMappings = Array.isArray(rows) ? rows : [];
      renderMappingTable();
      renderMappingSearchResults();
      setStatus("instrument-detail-mapping-status", `Loaded ${currentInstrumentMappings.length} mapping(s).`, "ok");
    }

    async function refreshPlcLinksForDatapoint(dpId) {
      const dpInfo = getCfgDatapointInfo(dpId);
      const eqId = clampInt(dpInfo.ownerId, 0);
      if (!eqId) return;
      if (plcView && typeof plcView.refreshDatapointInstrumentLinks === "function") {
        try {
          await plcView.refreshDatapointInstrumentLinks(eqId, true);
        } catch {
          // ignore bridge refresh failures in instruments tab
        }
      }
    }

    async function addInstrumentMapping() {
      const instrumentId = clampInt(currentInstrumentDetail?.id, 0);
      if (!instrumentId) return;

      const dpId = clampInt($("instrument-detail-map-dp")?.value || 0, 0);
      const role = String($("instrument-detail-map-role")?.value || "process").trim() || "process";
      if (!dpId) {
        setStatus("instrument-detail-mapping-status", "Select a datapoint first.", "error");
        return;
      }

      if (role === "pv") {
        const existingPv = currentInstrumentMappings.some((m) => String(m?.role || "").toLowerCase() === "pv");
        if (existingPv) {
          setStatus("instrument-detail-mapping-status", "Only one PV mapping is allowed per instrument.", "error");
          return;
        }
      }

      setStatus("instrument-detail-mapping-status", "Adding mapping...", "");
      await api.post(`/maintenance/instruments/${instrumentId}/datapoints`, {
        json: {
          cfg_data_point_id: dpId,
          role,
        },
      });
      await loadInstrumentMappings();
      await refreshPlcLinksForDatapoint(dpId);
      setStatus("instrument-detail-mapping-status", "Mapping added.", "ok");
    }

    async function unlinkInstrumentMapping(mapId) {
      const instrumentId = clampInt(currentInstrumentDetail?.id, 0);
      if (!instrumentId || !mapId) return;

      const target = currentInstrumentMappings.find((m) => clampInt(m?.id, 0) === mapId) || null;
      const dpId = clampInt(target?.cfg_data_point_id, 0);

      if (!confirmDanger("Unlink this datapoint mapping?")) return;
      setStatus("instrument-detail-mapping-status", "Unlinking...", "");
      await api.delete(`/maintenance/instruments/${instrumentId}/datapoints/${mapId}`);
      await loadInstrumentMappings();
      if (dpId) await refreshPlcLinksForDatapoint(dpId);
      setStatus("instrument-detail-mapping-status", "Mapping removed.", "ok");
    }

    function formatDateTimeISO(isoStr) {
      if (!isoStr) return "-";
      const d = new Date(isoStr);
      if (!Number.isFinite(d.getTime())) return "-";
      return d.toLocaleString();
    }

    function calculateCalibrationStatus(calibrations) {
      if (!Array.isArray(calibrations) || !calibrations.length) {
        return { status: "OK", badge: "ok", nextDueAt: null };
      }

      const sorted = calibrations.slice().sort((a, b) => {
        const tsA = new Date(a?.ts || 0).getTime();
        const tsB = new Date(b?.ts || 0).getTime();
        return tsB - tsA;
      });

      const latest = sorted[0];
      const nextDueAt = latest?.next_due_at;
      if (!nextDueAt) {
        return { status: "OK", badge: "ok", nextDueAt: null };
      }

      const now = new Date();
      const dueDate = new Date(nextDueAt);
      const daysUntilDue = (dueDate.getTime() - now.getTime()) / (1000 * 60 * 60 * 24);

      if (daysUntilDue < 0) {
        return { status: "Overdue", badge: "error", nextDueAt: formatDateTimeISO(nextDueAt) };
      }
      if (daysUntilDue <= 7) {
        return { status: "Due Soon", badge: "warn", nextDueAt: formatDateTimeISO(nextDueAt) };
      }

      return { status: "OK", badge: "ok", nextDueAt: formatDateTimeISO(nextDueAt) };
    }

    function renderCalibrationsTable(calibrations) {
      const tbody = $("calibration-table-body");
      if (!tbody) return;
      tbody.innerHTML = "";

      const sorted = Array.isArray(calibrations) ? calibrations.slice().sort((a, b) => {
        const tsA = new Date(a?.ts || 0).getTime();
        const tsB = new Date(b?.ts || 0).getTime();
        return tsB - tsA;
      }) : [];

      for (const cal of sorted) {
        tbody.appendChild(
          el("tr", {}, [
            el("td", { text: formatDateTimeISO(cal?.ts) }),
            el("td", { text: String(cal?.method || "-") }),
            el("td", { text: String(cal?.result || "-") }),
            el("td", { text: String(cal?.as_found || "-") }),
            el("td", { text: String(cal?.as_left || "-") }),
            el("td", { text: String(cal?.performed_by || "-") }),
            el("td", { text: String(cal?.certificate_no || "-") }),
            el("td", { text: String(cal?.notes || "-") }),
          ])
        );
      }

      if (!sorted.length) {
        tbody.appendChild(el("tr", {}, [el("td", { colspan: "8", class: "muted", text: "No calibrations" })]));
      }
    }

    async function loadInstrumentCalibrations() {
      const instrumentId = clampInt(currentInstrumentDetail?.id, 0);
      if (!instrumentId) return;

      setStatus("calibration-status", "Loading calibrations...", "");
      const calibrations = await api.get(`/maintenance/instruments/${instrumentId}/calibrations?limit=200`);
      currentInstrumentCalibrations = Array.isArray(calibrations) ? calibrations : [];
      renderCalibrationsTable(currentInstrumentCalibrations);

      const bannerDiv = $("calibration-status-banner");
      if (bannerDiv) {
        bannerDiv.innerHTML = "";
        const { status, badge, nextDueAt } = calculateCalibrationStatus(currentInstrumentCalibrations);
        const badge_span = renderHealthBadge(badge === "ok" ? 80 : badge === "warn" ? 60 : 30, []);
        const label = el("span", { text: status }, []);
        if (nextDueAt) {
          const due = el("span", { class: "muted smaller", text: ` (${nextDueAt})` }, []);
          bannerDiv.appendChild(el("div", {}, [badge_span, label, due]));
        } else {
          bannerDiv.appendChild(el("div", {}, [badge_span, label]));
        }
      }

      setStatus("calibration-status", `Loaded ${currentInstrumentCalibrations.length} calibration(s).`, "ok");
    }

    function openAddCalibrationModal() {
      const now = new Date();
      const isoStr = now.toISOString().slice(0, 16);
      const tsInput = $("calibration-ts");
      if (tsInput) tsInput.value = isoStr;

      const nextDueInput = $("calibration-next-due-at");
      if (nextDueInput) nextDueInput.value = "";

      const resultInput = $("calibration-result");
      if (resultInput) resultInput.value = "";

      $("calibration-method").value = "";
      $("calibration-as-found").value = "";
      $("calibration-as-left").value = "";
      $("calibration-performed-by").value = "";
      $("calibration-certificate-no").value = "";
      $("calibration-notes").value = "";

      $("modal-add-calibration")?.showModal();
    }

    async function saveCalibration() {
      const instrumentId = clampInt(currentInstrumentDetail?.id, 0);
      if (!instrumentId) return;

      const normalizeNumericInput = (raw) => {
        const text = String(raw || "").trim();
        if (!text) return null;
        const stripped = text.replace(/%/g, "").replace(/^\+/, "").trim();
        if (!stripped) return null;
        const num = Number(stripped);
        if (!Number.isFinite(num)) {
          throw new Error("As Found/As Left must be numeric values");
        }
        return num;
      };

      const normalizeDateTimeInput = (raw) => {
        const text = String(raw || "").trim();
        if (!text) return "";
        if (text.includes("T")) return text;

        const m = text.match(/^(\d{1,2})[\/-](\d{1,2})[\/-](\d{4})(?:\s+(\d{1,2}):(\d{2}))?$/);
        if (m) {
          const day = m[1].padStart(2, "0");
          const month = m[2].padStart(2, "0");
          const year = m[3];
          const hh = (m[4] || "00").padStart(2, "0");
          const mm = (m[5] || "00").padStart(2, "0");
          return `${year}-${month}-${day}T${hh}:${mm}`;
        }

        const parsed = new Date(text);
        if (!Number.isNaN(parsed.getTime())) {
          const yyyy = parsed.getFullYear();
          const mm = String(parsed.getMonth() + 1).padStart(2, "0");
          const dd = String(parsed.getDate()).padStart(2, "0");
          const hh = String(parsed.getHours()).padStart(2, "0");
          const min = String(parsed.getMinutes()).padStart(2, "0");
          return `${yyyy}-${mm}-${dd}T${hh}:${min}`;
        }

        return text;
      };

      const ts = String($("calibration-ts")?.value || "").trim();
      const nextDueAt = String($("calibration-next-due-at")?.value || "").trim();
      const method = String($("calibration-method")?.value || "").trim();
      const result = String($("calibration-result")?.value || "").trim();
      const asFound = String($("calibration-as-found")?.value || "").trim();
      const asLeft = String($("calibration-as-left")?.value || "").trim();
      const performedBy = String($("calibration-performed-by")?.value || "").trim();
      const certificateNo = String($("calibration-certificate-no")?.value || "").trim();
      const notes = String($("calibration-notes")?.value || "").trim();

      if (!ts) {
        alert("Calibration date/time is required");
        return;
      }

      const normalizedTs = normalizeDateTimeInput(ts);
      const normalizedNextDueAt = normalizeDateTimeInput(nextDueAt);

      let normalizedAsFound;
      let normalizedAsLeft;
      try {
        normalizedAsFound = normalizeNumericInput(asFound);
        normalizedAsLeft = normalizeNumericInput(asLeft);
      } catch (err) {
        alert(err?.message || "Invalid calibration value");
        return;
      }

      const payload = {
        ts: normalizedTs,
        method,
        result,
        as_found: normalizedAsFound,
        as_left: normalizedAsLeft,
        performed_by: performedBy,
        certificate_no: certificateNo,
        notes,
      };
      if (normalizedNextDueAt) payload.next_due_at = normalizedNextDueAt;

      setStatus("calibration-status", "Saving...", "");
      await api.post(`/maintenance/instruments/${instrumentId}/calibrations`, { json: payload });
      setStatus("calibration-status", "Calibration saved.", "ok");
    }

    async function loadSparePartsCatalog() {
      if (sparesPartsCatalog.length > 0) return;
      const parts = await api.get("/maintenance/spare_parts");
      sparesPartsCatalog = Array.isArray(parts) ? parts : [];
    }

    function renderSparesTable() {
      const tbody = $("spares-table-body");
      if (!tbody) return;
      tbody.innerHTML = "";

      const sorted = Array.isArray(currentInstrumentSpares) ? currentInstrumentSpares.slice().sort((a, b) => {
        const codeA = String(a?.part_code || "").toLowerCase();
        const codeB = String(b?.part_code || "").toLowerCase();
        return codeA.localeCompare(codeB);
      }) : [];

      for (const spare of sorted) {
        const spareId = clampInt(spare?.spare_part_id, 0);
        const code = String(spare?.part_code || "-");
        const name = String(spare?.name || "-");
        const qtyPerRepl = clampInt(spare?.qty_per_replacement, 1);

        tbody.appendChild(
          el("tr", {}, [
            el("td", { text: code }),
            el("td", { text: name }),
            el("td", { text: String(qtyPerRepl) }),
            el("td", {}, [
              el("button", {
                class: "btn small danger",
                type: "button",
                dataset: { action: "unmap-spare", spareId: String(spareId) },
                text: "Unmap",
              }),
            ]),
          ])
        );
      }

      if (!sorted.length) {
        tbody.appendChild(el("tr", {}, [el("td", { colspan: "4", class: "muted", text: "No spares mapped" })]));
      }
    }

    function renderSpareSearchResults() {
      const search = String($("spare-search")?.value || "").trim().toLowerCase();
      const resultsDiv = $("spare-search-results");
      if (!resultsDiv) return;
      resultsDiv.innerHTML = "";

      let filtered = sparesPartsCatalog;
      if (search) {
        filtered = filtered.filter((part) => {
          const code = String(part?.part_code || "").toLowerCase();
          const name = String(part?.name || "").toLowerCase();
          return code.includes(search) || name.includes(search);
        });
      }

      if (!filtered.length) {
        resultsDiv.appendChild(el("div", { class: "muted", style: "padding: 12px;", text: "No spare parts found" }, []));
        return;
      }

      for (const part of filtered) {
        const spareId = clampInt(part?.id, 0);
        const code = String(part?.part_code || "-");
        const name = String(part?.name || "-");
        const item = el("div", {
          class: "border-bottom",
          style: "padding: 8px 12px; cursor: pointer; hover: background-color: var(--panel);",
          dataset: { spareId: String(spareId), partCode: code, partName: name },
        }, [
          el("div", { class: "strong", text: code }, []),
          el("div", { class: "small muted", text: name }, []),
        ]);
        item.addEventListener("click", () => selectSpareFromResults(spareId, code, name));
        resultsDiv.appendChild(item);
      }
    }

    function selectSpareFromResults(spareId, code, name) {
      $("spare-selected-id").value = String(spareId);
      $("spare-selected").value = `${code} - ${name}`;
      $("spare-search-results").innerHTML = "";
      $("spare-search").value = "";
    }

    async function openAddSpareModal() {
      await loadSparePartsCatalog();
      $("spare-search").value = "";
      $("spare-selected-id").value = "";
      $("spare-selected").value = "";
      $("spare-qty-per-replacement").value = "1";
      $("spare-search-results").innerHTML = "";
      $("modal-add-spare")?.showModal();
    }

    async function addInstrumentSpare() {
      const instrumentId = clampInt(currentInstrumentDetail?.id, 0);
      if (!instrumentId) return;

      const spareId = clampInt($("spare-selected-id")?.value || 0, 0);
      const qtyPerRepl = clampInt($("spare-qty-per-replacement")?.value || 1, 1);

      if (!spareId) {
        alert("Please select a spare part");
        return;
      }

      setStatus("spares-status", "Adding spare...", "");
      await api.post(`/maintenance/instruments/${instrumentId}/spares`, {
        spare_part_id: spareId,
        qty_per_replacement: qtyPerRepl,
      });
      setStatus("spares-status", "Spare added.", "ok");
    }

    async function loadInstrumentSpares() {
      const instrumentId = clampInt(currentInstrumentDetail?.id, 0);
      if (!instrumentId) return;

      setStatus("spares-status", "Loading spares...", "");
      const spares = await api.get(`/maintenance/instruments/${instrumentId}/spares`);
      currentInstrumentSpares = Array.isArray(spares) ? spares : [];
      renderSparesTable();
      setStatus("spares-status", `Loaded ${currentInstrumentSpares.length} spare(s).`, "ok");
    }

    async function removeInstrumentSpare(spareId) {
      const instrumentId = clampInt(currentInstrumentDetail?.id, 0);
      if (!instrumentId || !spareId) return;

      if (!confirmDanger("Unmap this spare part?")) return;
      setStatus("spares-status", "Removing spare...", "");
      await api.delete(`/maintenance/instruments/${instrumentId}/spares/${spareId}`);
      await loadInstrumentSpares();
      setStatus("spares-status", "Spare removed.", "ok");
    }

    function formatDate(isoStr) {
      if (!isoStr) return "-";
      const d = new Date(isoStr);
      if (!Number.isFinite(d.getTime())) return "-";
      return d.toLocaleDateString();
    }

    function renderWorkOrdersTable() {
      const tbody = $("workorders-table-body");
      if (!tbody) return;
      tbody.innerHTML = "";

      const sorted = Array.isArray(currentInstrumentWorkOrders) ? currentInstrumentWorkOrders.slice().sort((a, b) => {
        const tsA = new Date(a?.created_at || 0).getTime();
        const tsB = new Date(b?.created_at || 0).getTime();
        return tsB - tsA;
      }) : [];

      for (const wo of sorted) {
        const woId = clampInt(wo?.id, 0);
        const code = String(wo?.work_order_code || "-");
        const title = String(wo?.title || "-");
        const status = String(wo?.status || "-");
        const priority = String(wo?.priority || "-");
        const created = formatDate(wo?.created_at);
        const due = formatDate(wo?.due_at);

        tbody.appendChild(
          el("tr", {}, [
            el("td", { text: code }),
            el("td", { text: title }),
            el("td", { text: status }),
            el("td", { text: priority }),
            el("td", { text: created }),
            el("td", { text: due }),
            el("td", {}, [
              el("button", {
                class: "btn small",
                type: "button",
                dataset: { action: "view-workorder", woId: String(woId) },
                text: "View",
              }),
            ]),
          ])
        );
      }

      if (!sorted.length) {
        tbody.appendChild(el("tr", {}, [el("td", { colspan: "7", class: "muted", text: "No work orders" })]));
      }
    }

    async function loadInstrumentWorkOrders() {
      const instrumentId = clampInt(currentInstrumentDetail?.id, 0);
      if (!instrumentId) return;

      setStatus("workorders-status", "Loading work orders...", "");
      const workOrders = await api.get(`/maintenance/work_orders?instrument_id=${instrumentId}&limit=100`);
      currentInstrumentWorkOrders = Array.isArray(workOrders) ? workOrders : [];
      renderWorkOrdersTable();
      setStatus("workorders-status", `Loaded ${currentInstrumentWorkOrders.length} work order(s).`, "ok");
    }

    async function openCreateWorkOrderModal() {
      const dueDateInput = $("workorder-due-at");
      if (dueDateInput) dueDateInput.value = "";
      $("workorder-title").value = "";
      $("workorder-priority").value = "";
      $("workorder-description").value = "";
      $("workorder-assigned-user-id").value = "";
      $("modal-create-workorder")?.showModal();
    }

    async function createWorkOrder() {
      const instrumentId = clampInt(currentInstrumentDetail?.id, 0);
      const equipmentId = clampInt(currentInstrumentDetail?.equipment_id, 0);
      if (!instrumentId) return;

      const title = String($("workorder-title")?.value || "").trim();
      const priority = String($("workorder-priority")?.value || "").trim();
      const description = String($("workorder-description")?.value || "").trim();
      const dueAt = String($("workorder-due-at")?.value || "").trim();
      const assignedUserId = clampInt($("workorder-assigned-user-id")?.value || 0, 0) || null;

      if (!title) {
        alert("Title is required");
        return;
      }

      if (!priority) {
        alert("Priority is required");
        return;
      }

      const payload = {
        equipment_id: equipmentId,
        instrument_id: instrumentId,
        title,
        description,
        priority,
        assigned_user_id: assignedUserId,
        assigned_role_id: null,
      };

      if (dueAt) {
        payload.due_at = `${dueAt}T00:00:00Z`;
      }

      setStatus("workorders-status", "Creating work order...", "");
      const result = await api.post("/maintenance/work_orders", payload);
      setStatus("workorders-status", `Work order created: ${result?.work_order_code || ""}`, "ok");
    }

    async function openBulkMapModal() {
      try {
        setStatus("bulk-map-status", "Loading instruments...", "");
        
        // Get all instrument mappings
        bulkMapAllMappings = [];
        for (const inst of instrumentsCache) {
          const mappings = await api.get(`/maintenance/instruments/${inst.id}/datapoints`);
          if (Array.isArray(mappings)) {
            bulkMapAllMappings.push(...mappings.map((m) => ({ ...m, instrument_id: inst.id })));
          }
        }

        // Filter to instruments with 0 mappings
        bulkMapInstrumentsWithoutMappings = instrumentsCache.filter((inst) => {
          const count = bulkMapAllMappings.filter((m) => m.instrument_id === inst.id).length;
          return count === 0;
        });

        bulkMapSelectedInstrumentId = null;
        bulkMapDatapoints = [];
        bulkMapDpSearch = "";
        $("bulk-map-dp-search").value = "";
        
        renderBulkMapInstrumentsList();
        renderBulkMapDatapointsList();
        $("modal-bulk-map")?.showModal();
        
        setStatus("bulk-map-status", `${bulkMapInstrumentsWithoutMappings.length} instruments ready to map`, "ok");
      } catch (err) {
        setStatus("bulk-map-status", err?.message || "Failed to load bulk map data", "error");
      }
    }

    function renderBulkMapInstrumentsList() {
      const listDiv = $("bulk-map-instruments-list");
      if (!listDiv) return;
      listDiv.innerHTML = "";

      if (!bulkMapInstrumentsWithoutMappings.length) {
        listDiv.appendChild(el("div", { class: "muted", style: "padding: 12px;", text: "All instruments have mappings" }, []));
        return;
      }

      for (const inst of bulkMapInstrumentsWithoutMappings) {
        const instrId = clampInt(inst?.id, 0);
        const code = String(inst?.label || "-");
        const name = String(inst?.meta?.name || "-");
        const isSelected = instrId === bulkMapSelectedInstrumentId;
        
        const item = el("button", {
          type: "button",
          class: isSelected ? "border-bottom" : "border-bottom",
          style: `padding: 12px; text-align: left; cursor: pointer; background-color: ${isSelected ? "var(--panel)" : "transparent"}; width: 100%;`,
          dataset: { bulkInstrument: String(instrId) },
          text: `${code} - ${name}`,
        }, []);
        
        listDiv.appendChild(item);
      }
    }

    function selectBulkMapInstrument(instrId) {
      bulkMapSelectedInstrumentId = instrId;
      const inst = bulkMapInstrumentsWithoutMappings.find((i) => i.id === instrId);
      
      if (!inst) return;

      // Load datapoints for the instrument's equipment
      const equipmentId = clampInt(inst?.equipment_id, 0);
      if (!equipmentId) {
        setStatus("bulk-map-status", "No equipment assigned", "error");
        bulkMapDatapoints = [];
        renderBulkMapDatapointsList();
        return;
      }

      const cfgEq = state.cfgIndex?.get(`equipment:${equipmentId}`);
      if (!cfgEq) {
        setStatus("bulk-map-status", "Equipment not found in config", "error");
        bulkMapDatapoints = [];
        renderBulkMapDatapointsList();
        return;
      }

      // Extract datapoints from config
      const raw = cfgEq.raw || {};
      bulkMapDatapoints = Array.isArray(raw.data_points)
        ? raw.data_points.map((dp, idx) => ({
            ...dp,
            cfg_id: clampInt(dp?.id, idx),
            label: String(dp?.label || "-"),
            address: String(dp?.address || "-"),
            type: String(dp?.type || "-"),
          }))
        : [];

      const selectedLabel = `${String(inst?.label || "-")} (Equipment: ${String(cfgEq.name || "-")})`;
      const selectedDiv = $("bulk-map-selected-instrument");
      if (selectedDiv) {
        selectedDiv.innerHTML = "";
        selectedDiv.appendChild(el("div", { class: "strong", text: selectedLabel }, []));
      }

      renderBulkMapInstrumentsList();
      renderBulkMapDatapointsList();
      setStatus("bulk-map-status", `${bulkMapDatapoints.length} datapoints available`, "ok");
    }

    function renderBulkMapDatapointsList() {
      const listDiv = $("bulk-map-datapoints-list");
      if (!listDiv) return;
      listDiv.innerHTML = "";

      if (bulkMapSelectedInstrumentId === null) {
        listDiv.appendChild(el("div", { class: "muted", style: "padding: 12px;", text: "Select an instrument" }, []));
        return;
      }

      const search = bulkMapDpSearch.toLowerCase();
      let filtered = bulkMapDatapoints;
      if (search) {
        filtered = filtered.filter((dp) => {
          const label = String(dp?.label || "").toLowerCase();
          const addr = String(dp?.address || "").toLowerCase();
          return label.includes(search) || addr.includes(search);
        });
      }

      if (!filtered.length) {
        listDiv.appendChild(el("div", { class: "muted", style: "padding: 12px;", text: search ? "No datapoints match" : "No datapoints" }, []));
        return;
      }

      for (const dp of filtered) {
        const dpCfgId = clampInt(dp?.cfg_id, 0);
        const label = String(dp?.label || "-");
        const address = String(dp?.address || "-");
        const type = String(dp?.type || "-");

        // Check if already mapped to another instrument
        const isAlreadyMapped = bulkMapAllMappings.some(
          (m) => clampInt(m?.cfg_data_point_id, 0) === dpCfgId && m.instrument_id !== bulkMapSelectedInstrumentId
        );

        const item = el("div", { style: "padding: 8px; margin-bottom: 4px; border: 1px solid var(--border); border-radius: 4px; background-color: var(--panel);" }, [
          el("div", { class: "strong", text: label }, []),
          el("div", { class: "small muted", text: `${address} (${type})` }, []),
          isAlreadyMapped &&
            el("div", { class: "small", style: "color: var(--danger); margin-top: 4px;", text: "⚠ Already mapped to another instrument" }, []),
          el("button", {
            type: "button",
            class: "btn small",
            style: `margin-top: 4px; ${isAlreadyMapped ? "opacity: 0.6;" : ""}`,
            dataset: { bulkDatapoint: String(dpCfgId) },
            disabled: isAlreadyMapped,
            text: "Map",
          }, []),
        ]);

        listDiv.appendChild(item);
      }
    }

    async function openBulkMapDpRoleSelector(dpCfgId) {
      const role = prompt("Enter role (e.g., pv, alarm, status, info, setpoint):", "pv");
      if (role === null) return;

      const roleStr = String(role).trim().toLowerCase();
      if (!roleStr) {
        alert("Role cannot be empty");
        return;
      }

      if (bulkMapSelectedInstrumentId && dpCfgId) {
        await bulkMapAddMapping(bulkMapSelectedInstrumentId, dpCfgId, roleStr);
      }
    }

    async function bulkMapAddMapping(instrId, dpCfgId, role) {
      try {
        setStatus("bulk-map-status", "Adding mapping...", "");
        await api.post(`/maintenance/instruments/${instrId}/datapoints`, {
          cfg_data_point_id: dpCfgId,
          role: role,
        });

        // Refresh the bulk map view
        await openBulkMapModal();
        setStatus("bulk-map-status", `Mapping added successfully`, "ok");
      } catch (err) {
        setStatus("bulk-map-status", err?.message || "Failed to add mapping", "error");
      }
    }

    function renderInstrumentDetailOverview(inst) {
      const code = String(inst?.label || "-");
      const name = String(inst?.meta?.name || "-");
      const type = String(inst?.instrument_type || "-");
      const model = String(inst?.model || "-");
      const serial = String(inst?.serial_number || "-");
      const status = String(inst?.status || "-");
      const equipment = String(inst?.equipment?.name || "-");
      const location = String(inst?.location || "-");
      const criticality = String(inst?.meta?.criticality || "-");
      const firstMapping = Array.isArray(inst?.mapped_datapoints) ? inst.mapped_datapoints[0] : null;
      const mappingLabel = String(firstMapping?.label || "-");
      const mappingRole = String(firstMapping?.role || "");
      const mappingSummary = mappingRole ? `${mappingLabel} (${mappingRole})` : mappingLabel;

      const title = $("instrument-detail-title");
      if (title) title.textContent = `${code} + ${name}`;

      setDetailValue("instrument-detail-code", code);
      setDetailValue("instrument-detail-name", name);
      setDetailValue("instrument-detail-type", type);
      setDetailValue("instrument-detail-model", model);
      setDetailValue("instrument-detail-serial", serial);
      setDetailValue("instrument-detail-status", status);
      setDetailValue("instrument-detail-equipment", equipment);
      setDetailValue("instrument-detail-location", location);
      setDetailValue("instrument-detail-criticality", criticality);
      setDetailValue("instrument-detail-mapping", mappingSummary);
    }

    async function openInstrumentDetailModal(id) {
      const detail = await api.get(`/maintenance/instruments/${id}`);
      currentInstrumentDetail = detail;
      currentInstrumentMappings = [];
      currentInstrumentCalibrations = [];
      currentInstrumentSpares = [];
      currentInstrumentWorkOrders = [];
      renderInstrumentDetailOverview(detail);
      await populateMappingEquipmentOptions(clampInt(detail?.equipment_id, 0));
      renderMappingSearchResults();
      setStatus("instrument-detail-mapping-status", "", "");
      setStatus("instrument-detail-health-status", "", "");
      setStatus("calibration-status", "", "");
      setStatus("spares-status", "", "");
      setStatus("workorders-status", "", "");
      renderHealthTabData({});
      setInstrumentDetailTab("overview");
      openDialog("modal-instrument-detail");
    }

    async function reloadAndRender() {
      const tableBody = $("instruments-table")?.querySelector("tbody");
      const pagination = $("instruments-pagination");
      if (!tableBody) return;

      try {
        setStatus("instruments-status", "Loading...", "");
        const instruments = await api.get("/maintenance/instruments");
        instrumentsCache = Array.isArray(instruments) ? instruments : [];
        await populateInstrumentEquipmentFilter();
        populateTypeFilter();
        applyFiltersAndRender();
      } catch (err) {
        console.error("Error loading instruments:", err);
        instrumentsCache = [];
        instrumentsCacheFiltered = [];
        tableBody.innerHTML = "";
        if (pagination) pagination.textContent = "Showing 0 of 0";
        setStatus("instruments-status", err?.message || "Failed to load instruments", "error");
      }
    }

    function buildInstrumentPayload(form) {
      const equipmentIdStr = String(form.equipment_id?.value || "").trim();
      const equipmentId = clampInt(equipmentIdStr, 0);
      return {
        label: String(form.instrument_code?.value || "").trim(),
        status: String(form.status?.value || "active").trim(),
        equipment_id: equipmentId,
        instrument_type: String(form.type?.value || "").trim() || null,
        model: String(form.model?.value || "").trim() || null,
        serial_number: String(form.serial?.value || "").trim() || null,
        location: String(form.location?.value || "").trim() || null,
        installed_at: form.install_date?.value || null,
        notes: String(form.make?.value || "") + (form.firmware?.value ? ` | FW: ${form.firmware.value}` : ""),
        meta: {
          name: String(form.name?.value || "").trim() || null,
          make: String(form.make?.value || "").trim() || null,
          firmware: String(form.firmware?.value || "").trim() || null,
          criticality: String(form.criticality?.value || "medium").trim(),
          commission_date: form.commission_date?.value || null,
          warranty_expiry: form.warranty_expiry?.value || null,
        },
      };
    }

    function toDateInputValue(value) {
      if (!value) return "";
      const text = String(value);
      return text.length >= 10 ? text.slice(0, 10) : text;
    }

    async function openInstrumentModal(mode, inst = null) {
      const form = $("form-instrument");
      const modalTitle = qs("h3", $("modal-instrument"));
      const saveButton = $("btn-instrument-save");
      if (!form) return;

      editingInstrumentId = mode === "edit" ? clampInt(inst?.id, 0) : null;
      form.reset();
      setStatus("modal-instrument-status", "", "");

      if (modalTitle) {
        modalTitle.textContent = mode === "edit" ? "Edit Instrument" : mode === "duplicate" ? "Duplicate Instrument" : "Add Instrument";
      }
      if (saveButton) {
        saveButton.textContent = mode === "edit" ? "Update" : "Save";
      }

      openDialog("modal-instrument");
      await populateInstrumentEquipment();

      if (!inst) return;

      form.instrument_code.value = mode === "duplicate" ? `${String(inst.label || "")}-COPY` : String(inst.label || "");
      form.name.value = String(inst.meta?.name || "");
      form.type.value = String(inst.instrument_type || "");
      form.make.value = String(inst.meta?.make || "");
      form.model.value = String(inst.model || "");
      form.serial.value = String(inst.serial_number || "");
      form.firmware.value = String(inst.meta?.firmware || "");
      form.equipment_id.value = inst.equipment_id != null ? String(inst.equipment_id) : "";
      form.location.value = String(inst.location || "");
      form.criticality.value = String(inst.meta?.criticality || "medium");
      form.status.value = String(inst.status || "active");
      form.install_date.value = toDateInputValue(inst.installed_at);
      form.commission_date.value = toDateInputValue(inst.meta?.commission_date);
      form.warranty_expiry.value = toDateInputValue(inst.meta?.warranty_expiry);
      await updateInstrumentEquipmentPathDisplay(clampInt(form.equipment_id.value, 0));
    }

    async function onTableClick(ev) {
      const btn = ev.target.closest("button[data-action]");
      if (!btn) return;

      const action = String(btn.dataset.action || "").trim();
      const id = clampInt(btn.dataset.id, 0);
      if (!id) return;
      try {
        if (action === "load-health") {
          await loadInstrumentHealthForRow(id, true);
          return;
        }

        const base = instrumentsCache.find((x) => Number(x.id) === id) || null;
        const inst = base || (await api.get(`/maintenance/instruments/${id}`));

        if (action === "view") {
          await openInstrumentDetailModal(id);
          return;
        }

        if (action === "edit") {
          await openInstrumentModal("edit", inst);
          return;
        }

        if (action === "duplicate") {
          await openInstrumentModal("duplicate", inst);
          return;
        }

        if (action === "delete") {
          const label = String(inst?.label || `#${id}`);
          if (!confirmDanger(`Delete instrument '${label}'?`)) return;
          await api.delete(`/maintenance/instruments/${id}`);
          toast("Instrument deleted");
          await reloadAndRender();
        }
      } catch (err) {
        toast(err?.message || "Instrument action failed", "error");
      }
    }

    async function show() {
      if (!initialized) {
        bind();
        initialized = true;
      }
      await reloadAndRender();
    }

    async function openInstrumentDetail(instrumentId) {
      const id = clampInt(instrumentId, 0);
      if (!id) return;
      if (!initialized) {
        bind();
        initialized = true;
      }
      await openInstrumentDetailModal(id);
    }

    return { show, openInstrumentDetail };
  })();

  const maintenanceAssetsView = (() => {
    let initialized = false;
    let containerRows = [];
    let assetRows = [];
    let instrumentRows = [];
    let editingAsset = null;
    let selectedNodeKey = null;
    const collapsed = new Set();

    function bind() {
      $("btn-maint-assets-refresh")?.addEventListener("click", () => reloadTree());
      $("btn-maint-assets-add-root")?.addEventListener("click", async () => {
        await openAssetModal("create", null, null);
      });
      $("maintenance-assets-search")?.addEventListener("input", () => renderTree());
      $("btn-maint-node-save")?.addEventListener("click", async () => {
        await saveSelectedNode();
      });
      $("btn-maint-node-delete")?.addEventListener("click", async () => {
        await deleteSelectedNode();
      });
      $("maintenance-assets-tree")?.addEventListener("click", async (ev) => {
        const twisty = ev.target.closest("button[data-toggle]");
        if (twisty) {
          ev.stopPropagation();
          const key = String(twisty.dataset.toggle || "");
          if (!key) return;
          if (collapsed.has(key)) collapsed.delete(key);
          else collapsed.add(key);
          renderTree();
          return;
        }

        const nodeEl = ev.target.closest("[data-node-key]");
        if (!nodeEl) return;
        const nextKey = String(nodeEl.dataset.nodeKey || "");
        if (!nextKey) return;
        selectedNodeKey = nextKey;
        renderTree();
        renderDetails();
      });

      $("maintenance-node-editor")?.addEventListener("click", async (ev) => {
        const actionNode = ev.target.closest("button[data-action]");
        if (!actionNode) return;
        const action = String(actionNode.dataset.action || "");
        const id = clampInt(actionNode.dataset.id, 0);

        if (action === "open-instrument" && id) {
          await instrumentsView.openInstrumentDetail(id);
          return;
        }

        if (action === "edit-asset-modal" && id) {
          const row = await api.get(`/maintenance/equipment/${id}`);
          await openAssetModal("edit", row, null);
          return;
        }

        if (action === "add-child-asset" && id) {
          await openAssetModal("create", null, id);
          return;
        }

        if (action === "add-asset-under-container" && id) {
          await openAssetModal("create", null, null, id);
        }
      });

      $("form-maintenance-asset")?.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        await submitAssetForm();
      });
    }

    function parseNodeKey(key) {
      const [type, rawId] = String(key || "").split(":");
      const id = clampInt(rawId, 0);
      if (!type || !id) return null;
      return { type, id };
    }

    function buildTreeData() {
      const equipmentById = new Map();
      const equipmentRootsByContainer = new Map();
      const instrumentsByEquipment = new Map();

      for (const inst of instrumentRows) {
        const equipmentId = clampInt(inst?.equipment_id, 0);
        if (!equipmentId) continue;
        if (!instrumentsByEquipment.has(equipmentId)) instrumentsByEquipment.set(equipmentId, []);
        instrumentsByEquipment.get(equipmentId).push(inst);
      }

      for (const row of assetRows) {
        const id = clampInt(row?.id, 0);
        if (!id) continue;
        equipmentById.set(id, {
          type: "equipment",
          id,
          name: String(row?.name || `Asset #${id}`),
          code: String(row?.equipment_code || ""),
          row,
          children: [],
        });
      }

      for (const eqNode of equipmentById.values()) {
        const parentId = clampInt(eqNode?.row?.parent_id, 0);
        if (parentId && equipmentById.has(parentId)) {
          equipmentById.get(parentId).children.push(eqNode);
        } else {
          const containerId = clampInt(eqNode?.row?.container_id, 0);
          if (!equipmentRootsByContainer.has(containerId)) equipmentRootsByContainer.set(containerId, []);
          equipmentRootsByContainer.get(containerId).push(eqNode);
        }
      }

      for (const eqNode of equipmentById.values()) {
        const linkedInstruments = (instrumentsByEquipment.get(eqNode.id) || []).slice().sort((a, b) => {
          return String(a?.label || "").localeCompare(String(b?.label || ""));
        });
        for (const inst of linkedInstruments) {
          const instId = clampInt(inst?.id, 0);
          if (!instId) continue;
          eqNode.children.push({
            type: "instrument",
            id: instId,
            name: String(inst?.label || `Instrument #${instId}`),
            row: inst,
            children: [],
          });
        }
      }

      const containerById = new Map();
      const roots = [];
      for (const row of containerRows) {
        const id = clampInt(row?.id, 0);
        if (!id) continue;
        containerById.set(id, {
          type: "container",
          id,
          name: String(row?.name || `Container #${id}`),
          code: String(row?.container_code || ""),
          row,
          children: [],
        });
      }

      for (const containerNode of containerById.values()) {
        const parentId = clampInt(containerNode?.row?.parent_id, 0);
        if (parentId && containerById.has(parentId)) {
          containerById.get(parentId).children.push(containerNode);
        } else {
          roots.push(containerNode);
        }
      }

      for (const containerNode of containerById.values()) {
        const equipmentRoots = equipmentRootsByContainer.get(containerNode.id) || [];
        containerNode.children.push(...equipmentRoots);
      }

      const unassigned = equipmentRootsByContainer.get(0) || [];
      if (unassigned.length) {
        roots.push({
          type: "container",
          id: -1,
          name: "Unassigned",
          code: "",
          row: {
            id: -1,
            name: "Unassigned",
            container_code: "",
            description: "Assets without container",
            location: null,
            parent_id: null,
            asset_category: null,
            asset_type: null,
            criticality: "B",
            duty_cycle_hours_per_day: null,
            spares_class: "standard",
            safety_classification: [],
            meta: {},
            is_active: true,
          },
          children: unassigned,
        });
      }

      const sortChildren = (node) => {
        const ordered = (Array.isArray(node.children) ? node.children : []).slice().sort((a, b) => {
          const at = String(a?.type || "");
          const bt = String(b?.type || "");
          if (at !== bt) return at.localeCompare(bt);
          return String(a?.name || "").localeCompare(String(b?.name || ""));
        });
        node.children = ordered;
        for (const child of node.children) sortChildren(child);
      };

      roots.sort((a, b) => String(a?.name || "").localeCompare(String(b?.name || "")));
      for (const root of roots) sortChildren(root);
      return roots;
    }

    function renderTree() {
      const host = $("maintenance-assets-tree");
      if (!host) return;
      host.innerHTML = "";
      const roots = buildTreeData();
      const term = String($("maintenance-assets-search")?.value || "").trim().toLowerCase();

      if (!roots.length) {
        host.appendChild(el("div", { class: "muted", text: "No maintenance containers/assets found." }, []));
        return;
      }

      const buildNodeEl = (node) => {
        const key = `${node.type}:${node.id}`;
        const childrenRaw = Array.isArray(node.children) ? node.children : [];
        const childrenBuilt = childrenRaw.map((child) => buildNodeEl(child)).filter(Boolean);

        const title = String(node?.name || "");
        const code = String(node?.code || "").trim();
        const typeLabel = String(node?.type || "");

        const selfText = `${title} ${code} ${typeLabel} ${String(node?.row?.asset_type || "")}`.toLowerCase();
        const selfMatch = !term || selfText.includes(term);
        const childMatch = childrenBuilt.some((x) => x.matched);
        const matched = selfMatch || childMatch;
        if (!matched) return null;

        const hasChildren = childrenBuilt.length > 0;
        const left = el("div", { class: "left" }, [
          hasChildren
            ? el("button", { class: "twisty", type: "button", dataset: { toggle: key }, text: collapsed.has(key) ? "▸" : "▾" })
            : el("span", { class: "badge", text: " " }),
          el("span", { class: "title", text: code ? `${title} (${code})` : title }),
          el("span", { class: "badge", text: typeLabel }),
        ]);

        const badges = [];
        if (node.type === "container") {
          const childContainers = childrenRaw.filter((c) => c.type === "container").length;
          const childAssets = childrenRaw.filter((c) => c.type === "equipment").length;
          badges.push(el("span", { class: "badge", text: `C:${childContainers}` }));
          badges.push(el("span", { class: "badge", text: `A:${childAssets}` }));
        }
        if (node.type === "equipment") {
          const childAssets = childrenRaw.filter((c) => c.type === "equipment").length;
          const childInst = childrenRaw.filter((c) => c.type === "instrument").length;
          if (node?.row?.asset_type) badges.push(el("span", { class: "badge", text: `type:${node.row.asset_type}` }));
          badges.push(el("span", { class: "badge", text: `A:${childAssets}` }));
          badges.push(el("span", { class: "badge", text: `I:${childInst}` }));
        }
        if (node.type === "instrument") {
          badges.push(el("span", { class: "badge", text: String(node?.row?.instrument_type || "-") }));
          badges.push(el("span", { class: "badge", text: String(node?.row?.status || "-") }));
        }

        const row = el("div", { class: "node", dataset: { nodeKey: key } }, [left, el("div", { class: "meta" }, badges)]);
        if (selectedNodeKey === key) row.classList.add("selected");

        const block = el("div", {}, [row]);
        if (hasChildren) {
          const wrap = el("div", { class: "children" }, childrenBuilt.map((c) => c.el));
          if (collapsed.has(key)) wrap.classList.add("hidden");
          block.appendChild(wrap);
        }
        return { el: block, matched };
      };

      for (const root of roots) {
        const built = buildNodeEl(root);
        if (built) host.appendChild(built.el);
      }
    }

    function getNodeByKey(key) {
      const parsed = parseNodeKey(key);
      if (!parsed) return null;
      if (parsed.type === "container") {
        const row = containerRows.find((x) => clampInt(x?.id, 0) === parsed.id) || null;
        return row ? { type: "container", row, id: parsed.id } : null;
      }
      if (parsed.type === "equipment") {
        const row = assetRows.find((x) => clampInt(x?.id, 0) === parsed.id) || null;
        return row ? { type: "equipment", row, id: parsed.id } : null;
      }
      if (parsed.type === "instrument") {
        const row = instrumentRows.find((x) => clampInt(x?.id, 0) === parsed.id) || null;
        return row ? { type: "instrument", row, id: parsed.id } : null;
      }
      return null;
    }

    function renderDetails() {
      const editor = $("maintenance-node-editor");
      if (!editor) return;
      const statusEl = $("maintenance-node-status");
      setStatus(statusEl, "", "");

      const saveBtn = $("btn-maint-node-save");
      const deleteBtn = $("btn-maint-node-delete");

      if (!selectedNodeKey) {
        editor.classList.add("muted");
        editor.innerHTML = "Select a node from the tree.";
        if (saveBtn) saveBtn.disabled = true;
        if (deleteBtn) deleteBtn.disabled = true;
        return;
      }

      const selected = getNodeByKey(selectedNodeKey);
      if (!selected) {
        selectedNodeKey = null;
        editor.classList.add("muted");
        editor.innerHTML = "Select a node from the tree.";
        if (saveBtn) saveBtn.disabled = true;
        if (deleteBtn) deleteBtn.disabled = true;
        return;
      }

      const writable = can(state.perms, "maintenance:write");
      if (saveBtn) saveBtn.disabled = !writable || selected.type === "instrument";
      if (deleteBtn) deleteBtn.disabled = !writable || selected.type === "instrument" || (selected.type === "container" && selected.id < 0);

      editor.classList.remove("muted");
      editor.innerHTML = "";

      const header = el("div", { class: "row" }, [
        el("div", {}, [
          el("div", { class: "muted", text: `${selected.type.toUpperCase()} #${selected.id}` }),
          el("div", { style: "font-weight:700", text: String(selected.row?.name || selected.row?.label || `#${selected.id}`) }),
        ]),
        el("div", { class: "actions" }, [
          selected.type === "container" && selected.id > 0
            ? el("button", { class: "btn small", type: "button", dataset: { action: "add-asset-under-container", id: String(selected.id) }, text: "Add Asset" })
            : "",
          selected.type === "equipment"
            ? el("button", { class: "btn small", type: "button", dataset: { action: "add-child-asset", id: String(selected.id) }, text: "Add Child" })
            : "",
          selected.type === "equipment"
            ? el("button", { class: "btn small", type: "button", dataset: { action: "edit-asset-modal", id: String(selected.id) }, text: "Advanced Edit" })
            : "",
          selected.type === "instrument"
            ? el("button", { class: "btn small", type: "button", dataset: { action: "open-instrument", id: String(selected.id) }, text: "Open Instrument" })
            : "",
        ].filter(Boolean)),
      ]);
      editor.appendChild(header);

      if (selected.type === "instrument") {
        editor.appendChild(
          el("div", { class: "form" }, [
            el("label", {}, ["Label", el("input", { class: "input", disabled: true, value: String(selected.row?.label || "") })]),
            el("label", {}, ["Type", el("input", { class: "input", disabled: true, value: String(selected.row?.instrument_type || "") })]),
            el("label", {}, ["Status", el("input", { class: "input", disabled: true, value: String(selected.row?.status || "") })]),
            el("label", {}, ["Equipment ID", el("input", { class: "input", disabled: true, value: String(selected.row?.equipment_id || "") })]),
          ])
        );
        return;
      }

      const form = el("div", { class: "form", id: "maintenance-node-form", dataset: { type: selected.type, id: String(selected.id) } }, []);
      form.appendChild(el("label", {}, ["Name", el("input", { class: "input", name: "name", value: String(selected.row?.name || "") })]));
      form.appendChild(el("label", {}, ["Location", el("input", { class: "input", name: "location", value: String(selected.row?.location || "") })]));
      form.appendChild(el("label", {}, ["Asset Category", el("input", { class: "input", name: "asset_category", value: String(selected.row?.asset_category || "") })]));
      form.appendChild(el("label", {}, ["Asset Type", el("input", { class: "input", name: "asset_type", value: String(selected.row?.asset_type || "") })]));

      const criticality = el("select", { class: "input", name: "criticality" }, [
        el("option", { value: "A", text: "A" }),
        el("option", { value: "B", text: "B" }),
        el("option", { value: "C", text: "C" }),
      ]);
      criticality.value = String(selected.row?.criticality || "B");
      form.appendChild(el("label", {}, ["Criticality", criticality]));

      if (selected.type === "container") {
        const parentSelect = el("select", { class: "input", name: "parent_id" }, [el("option", { value: "", text: "(No Parent)" })]);
        for (const row of containerRows) {
          const id = clampInt(row?.id, 0);
          if (!id || id === selected.id) continue;
          parentSelect.appendChild(el("option", { value: String(id), text: `${String(row?.name || `#${id}`)} (${String(row?.container_code || `#${id}`)})` }));
        }
        parentSelect.value = selected.row?.parent_id != null ? String(selected.row.parent_id) : "";
        form.appendChild(el("label", {}, ["Parent Container", parentSelect]));
      }

      if (selected.type === "equipment") {
        const parentSelect = el("select", { class: "input", name: "parent_id" }, [el("option", { value: "", text: "(No Parent)" })]);
        for (const row of assetRows) {
          const id = clampInt(row?.id, 0);
          if (!id || id === selected.id) continue;
          parentSelect.appendChild(el("option", { value: String(id), text: `${String(row?.name || `#${id}`)} (${String(row?.equipment_code || `#${id}`)})` }));
        }
        parentSelect.value = selected.row?.parent_id != null ? String(selected.row.parent_id) : "";

        const containerSelect = el("select", { class: "input", name: "container_id" }, [el("option", { value: "", text: "(No Container)" })]);
        for (const row of containerRows) {
          const id = clampInt(row?.id, 0);
          if (!id) continue;
          containerSelect.appendChild(el("option", { value: String(id), text: `${String(row?.name || `#${id}`)} (${String(row?.container_code || `#${id}`)})` }));
        }
        containerSelect.value = selected.row?.container_id != null ? String(selected.row.container_id) : "";
        form.appendChild(el("label", {}, ["Parent Asset", parentSelect]));
        form.appendChild(el("label", {}, ["Container", containerSelect]));
      }

      const description = el("textarea", { class: "input", name: "description", rows: "3" }, []);
      description.value = String(selected.row?.description || "");
      form.appendChild(el("label", {}, ["Description", description]));

      editor.appendChild(form);
    }

    async function saveSelectedNode() {
      const selected = getNodeByKey(selectedNodeKey);
      if (!selected || selected.type === "instrument") return;
      const form = $("maintenance-node-form");
      if (!form) return;

      const name = String(form.querySelector("[name='name']")?.value || "").trim();
      if (!name) {
        setStatus("maintenance-node-status", "Name is required", "error");
        return;
      }

      const location = String(form.querySelector("[name='location']")?.value || "").trim() || null;
      const assetCategory = String(form.querySelector("[name='asset_category']")?.value || "").trim() || null;
      const assetType = String(form.querySelector("[name='asset_type']")?.value || "").trim() || null;
      const criticality = String(form.querySelector("[name='criticality']")?.value || "B").trim() || "B";
      const description = String(form.querySelector("[name='description']")?.value || "").trim() || null;

      setStatus("maintenance-node-status", "Saving...", "");
      try {
        if (selected.type === "container") {
          const parentId = clampInt(form.querySelector("[name='parent_id']")?.value || 0, 0) || null;
          const payload = {
            name,
            location,
            description,
            parent_id: parentId,
            asset_category: assetCategory,
            asset_type: assetType,
            criticality,
            duty_cycle_hours_per_day: selected.row?.duty_cycle_hours_per_day ?? null,
            spares_class: String(selected.row?.spares_class || "standard"),
            safety_classification: Array.isArray(selected.row?.safety_classification) ? selected.row.safety_classification : [],
            meta: selected.row?.meta && typeof selected.row.meta === "object" ? selected.row.meta : {},
          };
          await api.put(`/maintenance/containers/${selected.id}`, { json: payload });
        }

        if (selected.type === "equipment") {
          const parentId = clampInt(form.querySelector("[name='parent_id']")?.value || 0, 0) || null;
          const containerId = clampInt(form.querySelector("[name='container_id']")?.value || 0, 0) || null;
          const payload = {
            name,
            location,
            description,
            vendor_id: selected.row?.vendor_id ?? null,
            container_id: containerId,
            parent_id: parentId,
            asset_category: assetCategory,
            asset_type: assetType,
            criticality,
            duty_cycle_hours_per_day: selected.row?.duty_cycle_hours_per_day ?? null,
            spares_class: String(selected.row?.spares_class || "standard"),
            safety_classification: Array.isArray(selected.row?.safety_classification) ? selected.row.safety_classification : [],
            meta: selected.row?.meta && typeof selected.row.meta === "object" ? selected.row.meta : {},
          };
          await api.put(`/maintenance/equipment/${selected.id}`, { json: payload });
        }

        toast("Saved");
        await reloadTree();
        setStatus("maintenance-node-status", "Saved", "ok");
      } catch (err) {
        setStatus("maintenance-node-status", err?.message || "Failed to save", "error");
      }
    }

    async function deleteSelectedNode() {
      const selected = getNodeByKey(selectedNodeKey);
      if (!selected || selected.type === "instrument") return;
      if (selected.type === "container" && selected.id < 0) return;

      const label = String(selected.row?.name || `#${selected.id}`);
      if (!confirmDanger(`Delete ${selected.type} '${label}'?`)) return;

      try {
        if (selected.type === "container") {
          await api.delete(`/maintenance/containers/${selected.id}`);
        } else if (selected.type === "equipment") {
          await api.delete(`/maintenance/equipment/${selected.id}`);
        }
        selectedNodeKey = null;
        toast("Deleted");
        await reloadTree();
      } catch (err) {
        setStatus("maintenance-node-status", err?.message || "Failed to delete", "error");
      }
    }

    async function loadContainers() {
      try {
        const rows = await api.get("/maintenance/containers");
        containerRows = Array.isArray(rows) ? rows : [];
      } catch {
        containerRows = [];
      }
    }

    async function loadFlatAssets() {
      const rows = await api.get("/maintenance/equipment");
      assetRows = Array.isArray(rows) ? rows : [];
    }

    async function loadInstruments() {
      try {
        const rows = await api.get("/maintenance/instruments");
        instrumentRows = Array.isArray(rows) ? rows : [];
      } catch {
        instrumentRows = [];
      }
    }

    async function reloadTree() {
      setStatus("maintenance-assets-status", "Loading...", "");
      try {
        await Promise.all([loadContainers(), loadFlatAssets(), loadInstruments()]);
        if (selectedNodeKey && !getNodeByKey(selectedNodeKey)) selectedNodeKey = null;
        renderTree();
        renderDetails();
        setStatus("maintenance-assets-status", `Loaded ${containerRows.length} container(s), ${assetRows.length} asset(s), ${instrumentRows.length} instrument(s)`, "ok");
      } catch (err) {
        setStatus("maintenance-assets-status", err?.message || "Failed to load maintenance assets", "error");
      }
    }

    async function populateParentOptions(selectedParentId = null, currentAssetId = null) {
      const sel = $("maintenance-asset-parent");
      if (!sel) return;
      const current = selectedParentId == null ? "" : String(selectedParentId);

      sel.innerHTML = "";
      sel.appendChild(el("option", { value: "", text: "(No Parent)" }, []));
      for (const row of assetRows) {
        const id = clampInt(row?.id, 0);
        if (!id) continue;
        if (currentAssetId && id === clampInt(currentAssetId, 0)) continue;
        const label = `${String(row?.name || `#${id}`)} (${String(row?.equipment_code || `#${id}`)})`;
        sel.appendChild(el("option", { value: String(id), text: label }, []));
      }
      sel.value = Array.from(sel.options).some((o) => o.value === current) ? current : "";
    }

    async function populateContainerOptions(selectedContainerId = null) {
      const sel = $("maintenance-asset-container");
      if (!sel) return;
      const current = selectedContainerId == null ? "" : String(selectedContainerId);
      sel.innerHTML = "";
      sel.appendChild(el("option", { value: "", text: "(No Container)" }, []));
      for (const row of containerRows) {
        const id = clampInt(row?.id, 0);
        if (!id) continue;
        const label = `${String(row?.name || `#${id}`)} (${String(row?.container_code || `#${id}`)})`;
        sel.appendChild(el("option", { value: String(id), text: label }, []));
      }
      sel.value = Array.from(sel.options).some((o) => o.value === current) ? current : "";
    }

    async function openAssetModal(mode, row = null, forcedParentId = null, forcedContainerId = null) {
      const form = $("form-maintenance-asset");
      const title = $("maintenance-asset-modal-title");
      if (!form) return;

      editingAsset = row ? { ...row } : null;
      form.reset();
      setStatus("maintenance-asset-modal-status", "", "");
      if (title) title.textContent = mode === "edit" ? "Edit Maintenance Asset" : "Add Maintenance Asset";

      const currentAssetId = clampInt(row?.id, 0) || null;
      const selectedParentId = forcedParentId ?? row?.parent_id ?? null;
      await populateParentOptions(selectedParentId, currentAssetId);
      await populateContainerOptions(forcedContainerId ?? row?.container_id ?? null);

      form.asset_id.value = currentAssetId ? String(currentAssetId) : "";
      form.name.value = String(row?.name || "");
      form.location.value = String(row?.location || "");
      form.description.value = String(row?.description || "");
      form.vendor_id.value = row?.vendor_id != null ? String(row.vendor_id) : "";
      form.asset_category.value = String(row?.asset_category || "");
      form.asset_type.value = String(row?.asset_type || "");
      form.criticality.value = String(row?.criticality || "B");
      form.duty_cycle_hours_per_day.value = row?.duty_cycle_hours_per_day != null ? String(row.duty_cycle_hours_per_day) : "";
      form.spares_class.value = String(row?.spares_class || "standard");

      const selectedSafety = new Set(Array.isArray(row?.safety_classification) ? row.safety_classification : []);
      const checks = form.querySelectorAll("input[name='safety_classification']");
      checks.forEach((node) => {
        node.checked = selectedSafety.has(String(node.value));
      });

      openDialog("modal-maintenance-asset");
    }

    async function submitAssetForm() {
      const form = $("form-maintenance-asset");
      if (!form) return;

      const assetId = clampInt(form.asset_id?.value || 0, 0);
      const safety = Array.from(form.querySelectorAll("input[name='safety_classification']:checked")).map((node) => String(node.value));

      const payload = {
        name: String(form.name?.value || "").trim(),
        location: String(form.location?.value || "").trim() || null,
        description: String(form.description?.value || "").trim() || null,
        vendor_id: form.vendor_id?.value ? clampInt(form.vendor_id.value, 0) : null,
        container_id: form.container_id?.value ? clampInt(form.container_id.value, 0) : null,
        parent_id: form.parent_id?.value ? clampInt(form.parent_id.value, 0) : null,
        asset_category: String(form.asset_category?.value || "").trim() || null,
        asset_type: String(form.asset_type?.value || "").trim() || null,
        criticality: String(form.criticality?.value || "B").trim() || "B",
        duty_cycle_hours_per_day: String(form.duty_cycle_hours_per_day?.value || "").trim() === "" ? null : Number(form.duty_cycle_hours_per_day.value),
        spares_class: String(form.spares_class?.value || "standard").trim() || "standard",
        safety_classification: safety,
        meta: editingAsset?.meta && typeof editingAsset.meta === "object" ? editingAsset.meta : {},
      };

      if (!payload.name) {
        setStatus("maintenance-asset-modal-status", "Name is required", "error");
        return;
      }

      setStatus("maintenance-asset-modal-status", "Saving...", "");
      try {
        if (assetId) {
          await api.put(`/maintenance/equipment/${assetId}`, { json: payload });
        } else {
          await api.post("/maintenance/equipment", { json: payload });
        }
        closeDialog("modal-maintenance-asset");
        toast("Maintenance asset saved");
        await reloadTree();
      } catch (err) {
        setStatus("maintenance-asset-modal-status", err?.message || "Failed to save maintenance asset", "error");
      }
    }

    async function show() {
      if (!initialized) {
        bind();
        initialized = true;
      }
      await reloadTree();
    }

    return { show };
  })();

  // -----------------------------
  // Meta view
  // -----------------------------
  const metaView = (() => {
    let initialized = false;

    function bind() {
      $("btn-meta-refresh")?.addEventListener("click", () => refresh(true));

      const domainSel = $("meta-domain");
      domainSel?.addEventListener("change", async () => {
        const next = String(domainSel.value || "datapoint");
        state.meta.currentDomain = META_DEFINITIONS[next] ? next : "datapoint";
        await refresh(false);
      });

      const form = $("form-meta-option");
      form?.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        await submitMetaForm();
      });

      $("meta-sections")?.addEventListener("click", async (ev) => {
        await onMetaSectionsClick(ev);
      });
    }

    async function show() {
      if (!initialized) {
        bind();
        initialized = true;
      }

      if (!META_DEFINITIONS[state.meta.currentDomain]) {
        state.meta.currentDomain = "datapoint";
      }

      const domainSel = $("meta-domain");
      if (domainSel) domainSel.value = state.meta.currentDomain;
      await refresh(false);
    }

    async function refresh(force) {
      const domain = state.meta.currentDomain || "datapoint";
      try {
        await ensureMetaDomainLoaded(domain, force);
        render(domain);
        setStatus("meta-status", "");
      } catch (err) {
        setStatus("meta-status", err?.message || "Failed to load meta lists", "error");
      }
    }

    function render(domain) {
      const root = $("meta-sections");
      if (!root) return;
      root.innerHTML = "";

      const domainDef = getMetaDomain(domain);
      const hint = $("meta-domain-hint");
      if (hint) hint.textContent = domainDef?.hint || "";

      const kindEntries = Object.entries(domainDef?.kinds || {});
      if (!kindEntries.length) {
        root.appendChild(
          el("div", { class: "panel" }, [
            el("h3", { text: `${domainDef?.label || "Meta"} metadata` }),
            el("div", { class: "muted", text: "No metadata kinds are configured for this domain yet." }),
          ])
        );
        return;
      }

      const wrapperClass = kindEntries.length > 1 ? "split" : "";
      const wrapper = el("div", { class: wrapperClass }, []);
      for (const [kind, kindDef] of kindEntries) {
        wrapper.appendChild(renderMetaKindPanel(domain, kind, kindDef));
      }
      root.appendChild(wrapper);
    }

    function renderMetaKindPanel(domain, kind, kindDef) {
      const statusId = `meta-status-${domain}-${kind}`;
      const tableId = `meta-table-${domain}-${kind}`;
      const items = getMetaItems(domain, kind);

      const panel = el("div", { class: "panel", style: "margin-top: 16px" }, [
        el("div", { class: "row" }, [
          el("h3", { text: `${kindDef?.label || kind}s` }),
          el("div", { class: "actions" }, [
            el("button", {
              class: "btn primary",
              type: "button",
              text: "New",
              dataset: { action: "new", domain, kind },
            }),
            el("button", {
              class: "btn",
              type: "button",
              text: "Refresh",
              dataset: { action: "refresh-kind", domain, kind },
            }),
          ]),
        ]),
        el("div", { id: statusId, class: "status" }),
      ]);

      const tableWrap = el("div", { class: "table-wrap" }, []);
      const table = el("table", { class: "table", id: tableId }, [
        el("thead", {}, [
          el("tr", {}, [
            el("th", { style: "width: 70px", text: "ID" }),
            el("th", { text: "Name" }),
            el("th", { text: "Description" }),
            el("th", { style: "width: 220px", text: "Actions" }),
          ]),
        ]),
        el("tbody"),
      ]);
      tableWrap.appendChild(table);
      panel.appendChild(tableWrap);

      renderMetaTable(table, items, domain, kind);
      return panel;
    }

    function renderMetaTable(table, items, domain, kind) {
      const tbody = table?.querySelector("tbody");
      if (!tbody) return;
      tbody.innerHTML = "";

      for (const item of items || []) {
        const tr = el("tr", {}, [
          el("td", { text: String(item.id ?? "") }),
          el("td", { text: item.name ?? "" }),
          el("td", { text: item.description ?? "" }),
          el("td", {}, [
            el("button", {
              class: "btn",
              dataset: { action: "edit", domain, kind, id: String(item.id) },
              type: "button",
              text: "Edit",
            }),
            " ",
            el("button", {
              class: "btn danger",
              dataset: { action: "delete", domain, kind, id: String(item.id) },
              type: "button",
              text: "Delete",
            }),
          ]),
        ]);
        tbody.appendChild(tr);
      }

      if (!items || items.length === 0) {
        tbody.appendChild(el("tr", {}, [el("td", { class: "muted", colspan: "4", text: "No items" }, [])]));
      }
    }

    async function onMetaSectionsClick(ev) {
      const btn = ev.target.closest("button[data-action]");
      if (!btn) return;

      const action = String(btn.dataset.action || "");
      const domain = META_DEFINITIONS[btn.dataset.domain] ? String(btn.dataset.domain) : (state.meta.currentDomain || "datapoint");
      const kind = String(btn.dataset.kind || "").trim();

      if (action === "new") {
        openMetaModal(domain, kind, null);
        return;
      }
      if (action === "refresh-kind") {
        await refreshKind(domain, kind);
        return;
      }

      const id = clampInt(btn.dataset.id, 0);
      if (!id || !kind) return;
      const list = getMetaItems(domain, kind);
      const existing = (list || []).find((x) => Number(x.id) === id) || null;

      if (action === "edit") {
        openMetaModal(domain, kind, existing);
        return;
      }
      if (action === "delete") {
        await deleteMeta(domain, kind, existing);
      }
    }

    async function refreshKind(domain, kind) {
      const status = $(`meta-status-${domain}-${kind}`);
      const kindDef = getMetaKindDef(domain, kind);
      if (!kindDef?.endpoint) {
        setStatus(status, "No endpoint configured for this metadata kind.");
        return;
      }
      setStatus(status, "Refreshing…");
      await refresh(true);
      setStatus(status, "", "ok");
    }

    function openMetaModal(domain, kind, existing) {
      const dlg = $("modal-meta-option");
      const form = $("form-meta-option");
      if (!dlg || !form) return;
      setStatus("modal-meta-status", "");

      form.reset();
      form.elements.domain.value = domain;
      form.elements.kind.value = kind;
      form.elements.id.value = existing?.id ? String(existing.id) : "";
      form.elements.name.value = existing?.name || "";
      form.elements.description.value = existing?.description || "";

      const title = $("modal-meta-title");
      const domainLabel = getMetaDomain(domain)?.label || "Meta";
      const kindLabel = getMetaKindDef(domain, kind)?.label || "Option";
      if (title) title.textContent = existing ? `Edit ${domainLabel} ${kindLabel}` : `New ${domainLabel} ${kindLabel}`;

      openDialog(dlg);
    }

    async function submitMetaForm() {
      const form = $("form-meta-option");
      if (!form) return;
      const status = $("modal-meta-status");
      setStatus(status, "Saving…");

      const fd = new FormData(form);
      const domain = String(fd.get("domain") || "datapoint").trim();
      const kind = String(fd.get("kind") || "").trim();
      const id = clampInt(fd.get("id"), 0);
      const name = String(fd.get("name") || "").trim();
      const description = String(fd.get("description") || "").trim();

      const endpoint = getMetaKindDef(domain, kind)?.endpoint;
      if (!endpoint) {
        setStatus(status, "This metadata kind is not configured yet.", "error");
        return;
      }

      try {
        if (id) {
          await api.patch(`${endpoint}/${id}`, { json: { name, description } });
        } else {
          await api.post(endpoint, { json: { name, description } });
        }
        closeDialog("modal-meta-option");
        toast("Saved");
        await refresh(true);
      } catch (err) {
        setStatus(status, err?.message || "Failed to save", "error");
      }
    }

    async function deleteMeta(domain, kind, existing) {
      if (!existing?.id) return;
      const domainLabel = getMetaDomain(domain)?.label || "Meta";
      const kindLabel = getMetaKindDef(domain, kind)?.label || "Item";
      if (!confirmDanger(`Delete ${domainLabel} ${kindLabel} '${existing.name}'?`)) return;

      const endpoint = getMetaKindDef(domain, kind)?.endpoint;
      if (!endpoint) return;
      try {
        await api.delete(`${endpoint}/${existing.id}`);
        toast("Deleted");
        await refresh(true);
      } catch (err) {
        toast(err?.message || "Failed to delete", "error");
      }
    }

    return { show };
  })();

  // -----------------------------
  // Alarm Rules view (admin)
  // -----------------------------
  const alarmsView = (() => {
    let initialized = false;
    let editingRuleId = null;

    const els = {
      refresh: () => $("btn-alarms-refresh"),
      new: () => $("btn-alarm-rule-new"),
      dpSelect: () => $("alarms-datapoint-select"),
      dpInfo: () => $("alarms-datapoint-info"),
      search: () => $("alarms-search"),
      status: () => $("alarms-status"),
      tableBody: () => qs("#alarms-table tbody"),

      modal: () => $("modal-alarm-rule"),
      form: () => $("form-alarm-rule"),
      modalTitle: () => $("alarm-rule-modal-title"),
      modalStatus: () => $("alarm-rule-status"),
      cancel: () => $("btn-alarm-rule-cancel"),

      mDatapoint: () => $("alarm-rule-datapoint"),
      mName: () => $("alarm-rule-name"),
      mEnabled: () => $("alarm-rule-enabled"),
      mWarnEnabled: () => $("alarm-rule-warning-enabled"),
      mScheduleEnabled: () => $("alarm-rule-schedule-enabled"),
      mSeverity: () => $("alarm-rule-severity"),
      mComparison: () => $("alarm-rule-comparison"),

      oneBox: () => $("alarm-rule-thresholds-one"),
      wThreshold: () => $("alarm-rule-warning-threshold"),
      aThreshold: () => $("alarm-rule-alarm-threshold"),

      rangeBox: () => $("alarm-rule-thresholds-range"),
      wLow: () => $("alarm-rule-warning-low"),
      wHigh: () => $("alarm-rule-warning-high"),
      aLow: () => $("alarm-rule-alarm-low"),
      aHigh: () => $("alarm-rule-alarm-high"),

      scheduleBox: () => $("alarm-rule-schedule"),
      start: () => $("alarm-rule-start"),
      end: () => $("alarm-rule-end"),
      tz: () => $("alarm-rule-tz"),
    };

    function parseNullableFloat(value) {
      const raw = String(value ?? "").trim();
      if (!raw) return null;
      const n = Number.parseFloat(raw);
      if (!Number.isFinite(n)) throw new Error("Invalid number");
      return n;
    }

    function currentDp() {
      const id = Number(state.alarms.selectedDatapointId);
      if (!Number.isFinite(id) || !id) return null;

      return (
        state.alarms.datapoints.find((d) => {
          const did = Number(d.id ?? d.datapoint_id ?? d.data_point_id);
          return Number.isFinite(did) && did === id;
        }) || null
      );
    }


    function formatThreshold(rule, which) {
      const cmp = rule.comparison;
      if (cmp === "above" || cmp === "below") {
        const v = which === "warning" ? rule.warning_threshold : rule.alarm_threshold;
        return (v === null || typeof v === "undefined") ? "—" : String(v);
      }
      // range
      const lo = which === "warning" ? rule.warning_threshold_low : rule.alarm_threshold_low;
      const hi = which === "warning" ? rule.warning_threshold_high : rule.alarm_threshold_high;
      if (lo === null || hi === null || typeof lo === "undefined" || typeof hi === "undefined") return "—";
      return `${lo} … ${hi}`;
    }

    function formatSchedule(rule) {
      if (!rule.schedule_enabled) return "Always";
      const s = rule.schedule_start_time || "";
      const e = rule.schedule_end_time || "";
      const tz = rule.schedule_timezone || "UTC";
      if (!s || !e) return `Scheduled (${tz})`;
      if (String(s) === String(e)) return `24h (${tz})`;
      return `${s} – ${e} (${tz})`;
    }

    function updateThresholdUI() {
      const cmp = els.mComparison()?.value || "above";
      const isRange = cmp === "outside_range" || cmp === "inside_range";
      els.oneBox()?.classList.toggle("hidden", isRange);
      els.rangeBox()?.classList.toggle("hidden", !isRange);
      const wOn = !!els.mWarnEnabled()?.checked;
      // Enable/disable warning inputs
      [els.wThreshold(), els.wLow(), els.wHigh()].forEach((n) => {
        if (!n) return;
        n.disabled = !wOn;
      });
    }

    function updateScheduleUI() {
      const on = !!els.mScheduleEnabled()?.checked;
      els.scheduleBox()?.classList.toggle("hidden", !on);
      [els.start(), els.end(), els.tz()].forEach((n) => {
        if (!n) return;
        n.disabled = !on;
      });
    }

    async function renderDatapoints() {
      const sel = els.dpSelect();
      if (!sel) return;

      const groupSel = $("alarms-group-select");
      const prevGroupValue = groupSel ? String(groupSel.value || "") : "";

      // Build group options (if the group filter exists in HTML)
      if (groupSel) {
        groupSel.innerHTML = "";
        groupSel.appendChild(el("option", { value: "", text: "All groups" }));
        groupSel.appendChild(el("option", { value: "__none__", text: "Ungrouped" }));

        // Try to load meta groups (best labels). If this fails (no config:read),
        // we still render a minimal group list based on datapoints we can see.
        try {
          if (!state.meta.loaded) await ensureMetaLoaded(false);
        } catch {
          // ignore
        }

        const metaGroups = Array.isArray(state.meta.groups) ? state.meta.groups : [];

        const dpGroupIds = Array.from(
          new Set(
            (state.alarms.datapoints || [])
              .map((d) => d.groupId ?? d.group_id ?? d.group ?? null)
              .filter((x) => x !== null && typeof x !== "undefined")
          )
        ).sort((a, b) => Number(a) - Number(b));

        const byId = new Map(metaGroups.map((g) => [String(g.id), g]));
        const merged = metaGroups.slice();

        for (const gid of dpGroupIds) {
          const k = String(gid);
          if (!byId.has(k)) merged.push({ id: gid, name: `Group #${gid}`, description: null });
        }

        merged.sort((a, b) => String(a.name || "").localeCompare(String(b.name || "")));

        for (const g of merged) {
          groupSel.appendChild(el("option", { value: String(g.id), text: g.name }));
        }

        // Restore previous selection if possible
        if (prevGroupValue && Array.from(groupSel.options).some((o) => o.value === prevGroupValue)) {
          groupSel.value = prevGroupValue;
        } else {
          groupSel.value = "";
        }
      }

      const selectedGroupRaw = groupSel ? String(groupSel.value || "") : "";
      const groupFilter =
        selectedGroupRaw === "__none__" ? "__none__" : selectedGroupRaw ? clampInt(selectedGroupRaw, 0) : null;

      // Filter datapoints by selected group
      const dps = (state.alarms.datapoints || [])
        .filter((dp) => {
          const gid = dp.groupId ?? dp.group_id ?? dp.group ?? null;
          if (groupFilter === null) return true;
          if (groupFilter === "__none__") return gid === null || typeof gid === "undefined";
          return Number(gid) === Number(groupFilter);
        })
        .sort((a, b) => {
          // stable, human-friendly sorting
          const la = String(a._display || a.label || a.name || "");
          const lb = String(b._display || b.label || b.name || "");
          const c = la.localeCompare(lb);
          if (c !== 0) return c;
          return Number(a.id) - Number(b.id);
        });

      // Populate datapoint select
      const prevDpId = state.alarms.selectedDatapointId;
      sel.innerHTML = "";
      sel.appendChild(el("option", { value: "", text: dps.length ? "Select datapoint…" : "No datapoints found" }));

      for (const dp of dps) {
        const id = dp.id ?? dp.datapoint_id ?? dp.data_point_id ?? "";
        const label = dp.description || dp._display || dp.label || dp.name || "";
        sel.appendChild(el("option", { value: String(id), text: label ? String(label) : String(id) }));
      }

      let chosen = null;
      if (prevDpId && dps.some((d) => Number(d.id) === Number(prevDpId))) chosen = prevDpId;
      else if (dps.length) chosen = dps[0].id;

      if (chosen) {
        sel.value = String(chosen);
        state.alarms.selectedDatapointId = clampInt(chosen, 0);
      } else {
        sel.value = "";
        state.alarms.selectedDatapointId = null;
      }

      const info = els.dpInfo();
      const dp = currentDp();
      if (info) {
        if (!dp) {
          info.textContent = "Pick a datapoint to view and manage its alarm rules.";
        } else {
          const gid = dp.groupId ?? dp.group_id ?? null;
          const gname = gid
            ? (state.meta.groups || []).find((g) => String(g.id) === String(gid))?.name || `Group #${gid}`
            : "Ungrouped";
          info.textContent =
            `${dp.owner_type || dp.ownerType || ""}/${dp.owner_id || dp.ownerId || ""}` +
            ` · ${dp.category || ""} · ${dp.type || ""} · ${dp.address || ""}` +
            ` · ${gname}`;
        }
      }

      // Optional datapoints table for quicker actions
      const tableWrap = $("alarms-datapoints-table");
      if (tableWrap) {
        tableWrap.innerHTML = "";
        const table = el("table", { class: "table" }, []);
        table.appendChild(
          el("thead", {}, [
            el("tr", {}, [
              el("th", { text: "ID" }),
              el("th", { text: "Label" }),
              el("th", { text: "Group" }),
              el("th", { text: "Type" }),
              el("th", { text: "Address" }),
              el("th", { text: "" }),
            ]),
          ])
        );
        const tbody = el("tbody");
        for (const d of dps) {
          const gid = d.groupId ?? d.group_id ?? null;
          const groupName =
            gid != null
              ? (state.meta.groups || []).find((g) => String(g.id) === String(gid))?.name || `Group #${gid}`
              : "—";
          const actions = el("div", { class: "actions-group" }, [
            el("button", { class: "btn small", type: "button", dataset: { action: "add-alarm", dpId: d.id }, text: "Add Alarm" }),
          ]);
          tbody.appendChild(
            el("tr", {}, [
              el("td", { text: String(d.id) }),
              el("td", { text: String(d.label || "") }),
              el("td", { text: groupName }),
              el("td", { text: String(d.type || "") }),
              el("td", { text: String(d.address || "") }),
              el("td", { class: "actions-col" }, [actions]),
            ])
          );
        }
        if (!dps.length) {
          tbody.appendChild(el("tr", {}, [el("td", { colspan: "6", class: "muted", text: "No datapoints found." })]));
        }
        table.appendChild(tbody);
        tableWrap.appendChild(table);
      }
    }

    function renderRules() {
      const body = els.tableBody();
      if (!body) return;
      body.innerHTML = "";

      const dp = currentDp();
      if (!dp) {
        if (!state.alarms.datapoints || state.alarms.datapoints.length === 0) {
          setStatus(els.status(), "No datapoints available. Ensure you have config:read and datapoints exist.", "error");
        } else {
          setStatus(els.status(), "Select a datapoint to load rules.", "");
        }
        return;
      }

      const q = String(els.search()?.value || "").toLowerCase().trim();
      const canWrite = (can(state.perms, "alarms:write") || can(state.perms, "alarms:admin"));
      const dpId = Number(dp.id ?? dp.datapoint_id ?? dp.data_point_id);
      const rows = (state.alarms.rules || []).filter((r) => {
        const ruleDpId = Number(r.datapoint_id ?? r.datapointId ?? r.data_point_id);

        // Only filter if both are valid numbers
        if (Number.isFinite(dpId) && Number.isFinite(ruleDpId) && dpId !== ruleDpId) return false;

        if (!q) return true;
        return (
          String(r.name || "").toLowerCase().includes(q) ||
          String(r.severity || "").toLowerCase().includes(q) ||
          String(r.comparison || "").toLowerCase().includes(q)
        );
      });

      if (!rows.length) {
        body.appendChild(el("tr", {}, [el("td", { colspan: "8", class: "muted", text: "No alarm rules." })]));
        setStatus(els.status(), "Loaded 0 rule(s).", "ok");
        return;
      }

      for (const r of rows) {
        const warn = r.warning_enabled ? formatThreshold(r, "warning") : "—";
        const al = formatThreshold(r, "alarm");
        const tr = el("tr", {}, [
          el("td", { text: r.name || "" }),
          el("td", { text: r.severity || "" }),
          el("td", { text: r.comparison || "" }),
          el("td", { text: warn }),
          el("td", { text: al }),
          el("td", { text: formatSchedule(r) }),
          el("td", { text: r.enabled ? "Yes" : "No" }),
          el("td", {}, [
            canWrite
              ? el("div", { class: "actions" }, [
                  el("button", { class: "btn", dataset: { action: "edit", id: r.id }, text: "Edit" }),
                  el("button", { class: "btn danger", dataset: { action: "delete", id: r.id }, text: "Delete" }),
                ])
              : el("span", { class: "muted", text: "—" }),
          ]),
        ]);
        body.appendChild(tr);
      }

      setStatus(els.status(), `Loaded ${rows.length} rule(s).`, "ok");
    }

    function flattenDatapointsFromTree(tree) {
      const out = [];
      const seen = new Set();

      const push = (dp, pathParts) => {
        if (!dp) return;
        const id = dp.id ?? dp.datapoint_id ?? dp.data_point_id ?? null;
        if (!id) return;
        if (seen.has(Number(id))) return;
        seen.add(Number(id));

        const path = (pathParts || []).filter(Boolean).join(" / ");
        const label = dp.label || dp.name || "";
        const display = path ? `${path} · ${label} (id:${id})` : `${label} (id:${id})`;

        out.push({
          ...dp,
          id: Number(id),
          _path: path,
          _display: display,
        });
      };

      const walkEquipment = (e, plcName, containerName) => {
        const eName = e?.name || `Equipment ${e?.id ?? ""}`.trim();
        for (const dp of e?.datapoints || []) push(dp, [plcName, containerName, eName]);
      };

      const walkContainer = (c, plcName) => {
        const cName = c?.name || `Container ${c?.id ?? ""}`.trim();
        for (const dp of c?.datapoints || []) push(dp, [plcName, cName]);
        for (const e of c?.equipment || []) walkEquipment(e, plcName, cName);
      };

      const walkPlc = (p) => {
        const plcName = p?.name || `PLC ${p?.id ?? ""}`.trim();
        for (const dp of p?.datapoints || []) push(dp, [plcName]);
        for (const c of p?.containers || []) walkContainer(c, plcName);
      };

      for (const plc of tree || []) walkPlc(plc);

      // sort for a stable UI
      out.sort((a, b) => {
        const la = String(a._display || a.label || "");
        const lb = String(b._display || b.label || "");
        const c = la.localeCompare(lb);
        if (c !== 0) return c;
        return Number(a.id) - Number(b.id);
      });

      return out;
    }

    async function loadDatapoints(force = false) {
      if (state.alarms.loaded && !force) return;
      setStatus(els.status(), "Loading datapoints…");

      let items = null;
      let lastErr = null;

      // Preferred (if present): purpose-built admin endpoint
      try {
        const r = await api.get("/admin/alarm-rules/datapoints");
        if (Array.isArray(r) && r.length) items = r;
      } catch (err) {
        lastErr = err;
      }

      // Fallback: use the existing config tree endpoint (this repo does NOT have /api/config/data-points list)
      if (!items || !Array.isArray(items) || items.length === 0) {
        try {
          const tree = await api.get("/api/config/tree");
          items = flattenDatapointsFromTree(tree);
        } catch (err) {
          lastErr = err;
          items = [];
        }
      }

      state.alarms.datapoints = Array.isArray(items) ? items : [];
      state.alarms.loaded = true;

      // Load meta groups for the group filter labels (best-effort)
      try {
        if (!state.meta.loaded) await ensureMetaLoaded(false);
      } catch {
        // ignore
      }

      // Preserve selection if possible
      const cur = state.alarms.selectedDatapointId;
      if (cur && state.alarms.datapoints.some((d) => Number(d.id) === Number(cur))) {
        // keep
      } else if (state.alarms.datapoints.length) {
        state.alarms.selectedDatapointId = Number(state.alarms.datapoints[0].id);
      } else {
        state.alarms.selectedDatapointId = null;
        if (lastErr) {
          setStatus(els.status(), lastErr?.message || "Failed to load datapoints", "error");
        } else {
          setStatus(els.status(), "No datapoints found.", "error");
        }
      }
    }

    async function loadRules() {
      const dp = currentDp();
      if (!dp) {
        state.alarms.rules = [];
        renderRules();
        return;
      }

      setStatus(els.status(), "Loading rules…");

      const dpId = Number(dp.id ?? dp.datapoint_id ?? dp.data_point_id);
      const res = await api.get("/admin/alarm-rules", { query: { datapoint_id: dpId } });

      // ✅ Support both shapes: Array OR {items:[...]}
      const rules = Array.isArray(res) ? res : (Array.isArray(res?.items) ? res.items : []);
      state.alarms.rules = rules;

      renderRules();
    }

    function fillModalDatapoints(selectedId) {
      const sel = els.mDatapoint();
      if (!sel) return;
      sel.innerHTML = "";
      for (const raw of state.alarms.datapoints) {
        const id = raw.id ?? raw.datapoint_id ?? raw.data_point_id ?? "";
        const label = raw.description || raw._display || raw.label || raw.name || String(id);
        sel.appendChild(el("option", { value: String(id), text: String(label) }));
      }
      if (selectedId) sel.value = String(selectedId);
    }

    function openRuleModal(rule) {
      const canWrite = (can(state.perms, "alarms:write") || can(state.perms, "alarms:admin"));
      if (!canWrite) {
        toast("You don't have permission to change alarm rules.", "error");
        return;
      }
      editingRuleId = rule?.id || null;
      setStatus(els.modalStatus(), "");
      const title = els.modalTitle();
      if (title) title.textContent = editingRuleId ? `Edit Alarm Rule #${editingRuleId}` : "New Alarm Rule";

      const dpId = rule?.datapoint_id || state.alarms.selectedDatapointId;
      fillModalDatapoints(dpId);

      if (els.mName()) els.mName().value = rule?.name || "";
      if (els.mEnabled()) els.mEnabled().checked = rule ? !!rule.enabled : true;
      if (els.mSeverity()) els.mSeverity().value = rule?.severity || "info";
      if (els.mComparison()) els.mComparison().value = rule?.comparison || "above";

      if (els.mWarnEnabled()) els.mWarnEnabled().checked = rule ? !!rule.warning_enabled : false;
      if (els.wThreshold()) els.wThreshold().value = rule?.warning_threshold ?? "";
      if (els.aThreshold()) els.aThreshold().value = rule?.alarm_threshold ?? "";

      if (els.wLow()) els.wLow().value = rule?.warning_threshold_low ?? "";
      if (els.wHigh()) els.wHigh().value = rule?.warning_threshold_high ?? "";
      if (els.aLow()) els.aLow().value = rule?.alarm_threshold_low ?? "";
      if (els.aHigh()) els.aHigh().value = rule?.alarm_threshold_high ?? "";

      if (els.mScheduleEnabled()) els.mScheduleEnabled().checked = rule ? !!rule.schedule_enabled : false;
      if (els.start()) els.start().value = rule?.schedule_start_time ? String(rule.schedule_start_time).slice(0, 5) : "";
      if (els.end()) els.end().value = rule?.schedule_end_time ? String(rule.schedule_end_time).slice(0, 5) : "";
      if (els.tz()) els.tz().value = rule?.schedule_timezone || "";

      updateThresholdUI();
      updateScheduleUI();
      openDialog(els.modal());
    }

    function closeRuleModal() {
      closeDialog(els.modal());
      editingRuleId = null;
    }

    async function submitRuleForm(ev) {
      ev.preventDefault();
      setStatus(els.modalStatus(), "Saving…");

      try {
        const dpId = clampInt(els.mDatapoint()?.value, 0);
        if (!dpId) throw new Error("Datapoint is required");

        const name = String(els.mName()?.value || "").trim();
        if (!name) throw new Error("Name is required");

        const comparison = String(els.mComparison()?.value || "above");
        const isRange = comparison === "outside_range" || comparison === "inside_range";

        const payload = {
          datapoint_id: dpId,
          name,
          enabled: !!els.mEnabled()?.checked,
          severity: String(els.mSeverity()?.value || "info"),
          comparison,
          warning_enabled: !!els.mWarnEnabled()?.checked,
          schedule_enabled: !!els.mScheduleEnabled()?.checked,
          schedule_start_time: els.start()?.value || null,
          schedule_end_time: els.end()?.value || null,
          schedule_timezone: String(els.tz()?.value || "").trim() || null,
        };

        if (isRange) {
          payload.alarm_threshold_low = parseNullableFloat(els.aLow()?.value);
          payload.alarm_threshold_high = parseNullableFloat(els.aHigh()?.value);
          payload.warning_threshold_low = parseNullableFloat(els.wLow()?.value);
          payload.warning_threshold_high = parseNullableFloat(els.wHigh()?.value);
          payload.alarm_threshold = null;
          payload.warning_threshold = null;
        } else {
          payload.alarm_threshold = parseNullableFloat(els.aThreshold()?.value);
          payload.warning_threshold = parseNullableFloat(els.wThreshold()?.value);
          payload.alarm_threshold_low = null;
          payload.alarm_threshold_high = null;
          payload.warning_threshold_low = null;
          payload.warning_threshold_high = null;
        }

        // If schedule disabled, clear schedule fields
        if (!payload.schedule_enabled) {
          payload.schedule_start_time = null;
          payload.schedule_end_time = null;
          payload.schedule_timezone = null;
        }

        if (!payload.warning_enabled) {
          payload.warning_threshold = null;
          payload.warning_threshold_low = null;
          payload.warning_threshold_high = null;
        }

        let res;
        if (editingRuleId) {
          res = await api.put(`/admin/alarm-rules/${editingRuleId}`, { json: payload });
          toast("Rule updated");
        } else {
          res = await api.post("/admin/alarm-rules", { json: payload });
          toast("Rule created");
        }

        // Ensure selected datapoint matches
        state.alarms.selectedDatapointId = res?.datapoint_id || payload.datapoint_id;
        await renderDatapoints();
        closeRuleModal();
        await loadRules();
      } catch (err) {
        setStatus(els.modalStatus(), err?.message || "Failed to save rule", "error");
      }
    }

    async function deleteRule(id) {
      const canWrite = (can(state.perms, "alarms:write") || can(state.perms, "alarms:admin"));
      if (!canWrite) return;
      if (!confirmDanger("Delete this alarm rule?")) return;
      try {
        await api.delete(`/admin/alarm-rules/${id}`);
        toast("Rule deleted");
        await loadRules();
      } catch (err) {
        toast(err?.message || "Failed to delete rule", "error");
      }
    }

    function bind() {
      els.refresh()?.addEventListener("click", async () => {
        try {
          state.alarms.loaded = false;
          await loadDatapoints(true);
          await renderDatapoints();
          await loadRules();
        } catch (err) {
          toast(err?.message || "Failed to refresh", "error");
        }
      });

      els.dpSelect()?.addEventListener("change", async () => {
        const id = clampInt(els.dpSelect()?.value, 0);
        state.alarms.selectedDatapointId = id || null;
        await renderDatapoints();
        await loadRules();
      });

      // Group filter (optional element)
      const groupFilterEl = $("alarms-group-select");
      if (groupFilterEl) {
        groupFilterEl.addEventListener("change", async () => {
          // re-render datapoints according to selected group
          await renderDatapoints();
          // reload rules for current selection (may be null)
          await loadRules();
        });
      }

      els.search()?.addEventListener("input", () => renderRules());

      els.new()?.addEventListener("click", () => openRuleModal(null));

      qs("#alarms-table")?.addEventListener("click", async (ev) => {
        const btn = ev.target.closest("button[data-action]");
        if (!btn) return;
        const action = btn.dataset.action;
        const id = clampInt(btn.dataset.id, 0);
        if (!id) return;
        const rule = (state.alarms.rules || []).find((r) => r.id === id);
        if (action === "edit") openRuleModal(rule);
        if (action === "delete") await deleteRule(id);
      });

      // Optional datapoints table action handler (delegated)
      $("alarms-datapoints-table")?.addEventListener("click", (ev) => {
        const btn = ev.target.closest("button[data-action]");
        if (!btn) return;
        const action = btn.dataset.action;
        if (action !== "add-alarm") return;
        const dpId = clampInt(btn.dataset.dpid ?? btn.dataset.dpId ?? btn.dataset.dp, 0);
        if (!dpId) return;
        state.alarms.selectedDatapointId = dpId;
        openRuleModal(null);
      });

      // Modal
      els.cancel()?.addEventListener("click", () => closeRuleModal());
      els.form()?.addEventListener("submit", submitRuleForm);
      els.mComparison()?.addEventListener("change", updateThresholdUI);
      els.mWarnEnabled()?.addEventListener("change", updateThresholdUI);
      els.mScheduleEnabled()?.addEventListener("change", updateScheduleUI);
    }

    async function show() {
      const canAdmin = can(state.perms, "alarms:admin");
      if (!canAdmin) {
        setStatus(els.status(), "Not authorized", "error");
        return;
      }

      if (!initialized) {
        bind();
        initialized = true;
      }

      // UX: hide write actions if no write permission
      const canWrite = (can(state.perms, "alarms:write") || can(state.perms, "alarms:admin"));
      els.new()?.classList.toggle("hidden", !canWrite);

      try {
        await loadDatapoints(false);
        await renderDatapoints();
        await loadRules();
      } catch (err) {
        setStatus(els.status(), err?.message || "Failed to load alarm rules", "error");
      }
    }

    return { show };
  })();

  // -----------------------------
  // Access control view
  // -----------------------------
  const accessView = (() => {
    let initialized = false;

    function bind() {
      $("btn-access-refresh")?.addEventListener("click", () => refresh());
      $("btn-grants-clear")?.addEventListener("click", () => clearAllGrants());

      $("principal-type")?.addEventListener("change", () => {
        state.access.principalType = $("principal-type").value;
        state.access.principalId = null;
        renderPrincipalOptions();
        refreshGrants();
      });

      $("principal-id")?.addEventListener("change", () => {
        state.access.principalId = clampInt($("principal-id").value, 0) || null;
        refreshGrants();
      });

      $("principal-search")?.addEventListener("input", () => renderPrincipalOptions());
      $("access-search")?.addEventListener("input", () => renderAccessTree());

      $("access-tree")?.addEventListener("change", async (ev) => {
        const toggle = ev.target.closest("input[data-grant-toggle]");
        if (!toggle) return;
        const key = toggle.dataset.nodeKey;
        await onGrantToggleChanged(key);
      });

      $("access-tree")?.addEventListener("click", (ev) => {
        const twisty = ev.target.closest("button[data-toggle]");
        if (twisty) {
          ev.stopPropagation();
          const key = twisty.dataset.toggle;
          if (state.access.collapsed.has(key)) state.access.collapsed.delete(key);
          else state.access.collapsed.add(key);
          renderAccessTree();
          return;
        }

        const row = ev.target.closest("[data-node-key]");
        if (row) {
          state.access.selectedKey = row.dataset.nodeKey;
          renderAccessTree();
          renderAccessPreview();
        }
      });

      $("grants-list")?.addEventListener("click", async (ev) => {
        const btn = ev.target.closest("button[data-grant-id]");
        if (!btn) return;
        const grantId = clampInt(btn.dataset.grantId, 0);
        if (!grantId) return;
        await deleteGrantById(grantId);
      });
    }

    async function show(query) {
      if (!initialized) {
        bind();
        initialized = true;
      }

      // enforce principal options based on permissions
      const pt = $("principal-type");
      const canRole = can(state.perms, "roles:admin");
      const canUser = can(state.perms, "users:admin");

      // If one is missing, hide it
      if (pt) {
        pt.querySelector('option[value="role"]')?.toggleAttribute("disabled", !canRole);
        pt.querySelector('option[value="user"]')?.toggleAttribute("disabled", !canUser);

        if (state.access.principalType === "role" && !canRole) state.access.principalType = canUser ? "user" : "role";
        if (state.access.principalType === "user" && !canUser) state.access.principalType = canRole ? "role" : "user";

        pt.value = state.access.principalType;
      }

      await ensurePrincipalsLoaded();
      await ensureConfigTreeLoaded();

      // Deep link: #access?principal=user&id=7
      if (query && query.principal && query.id) {
        const qType = String(query.principal);
        const qId = clampInt(query.id, 0);
        if ((qType === "role" && canRole) || (qType === "user" && canUser)) {
          state.access.principalType = qType;
          state.access.principalId = qId || null;
          if ($("principal-type")) $("principal-type").value = qType;
        }
      }

      renderPrincipalOptions();
      // Select principal from state, or default to first option
      if (!state.access.principalId) {
        const sel = $("principal-id");
        const first = sel?.options?.[0]?.value;
        state.access.principalId = first ? clampInt(first, 0) : null;
      }
      if ($("principal-id") && state.access.principalId) $("principal-id").value = String(state.access.principalId);

      await refreshGrants();
      renderAccessTree();
      renderGrantsList();
      renderAccessPreview();
    }

    async function ensurePrincipalsLoaded() {
      // roles + users lists for principal selection
      // For typeahead friendliness, we keep them in state.
      try {
        if (can(state.perms, "roles:admin")) {
          const roles = await api.get("/admin/roles");
          state.roles = Array.isArray(roles) ? roles : [];
        }
      } catch {
        // ignore
      }

      try {
        if (can(state.perms, "users:admin")) {
          const users = await api.get("/admin/users");
          state.users = Array.isArray(users) ? users : [];
        }
      } catch {
        // ignore
      }
    }



    function renderPrincipalOptions() {
      const sel = $("principal-id");
      if (!sel) return;

      const type = $("principal-type")?.value || state.access.principalType;
      state.access.principalType = type;

      const term = String($("principal-search")?.value || "").trim().toLowerCase();

      const options = [];
      if (type === "role") {
        for (const r of state.roles || []) {
          if (term && !String(r.name || "").toLowerCase().includes(term)) continue;
          options.push({ id: r.id, label: `${r.name} (#${r.id})` });
        }
      } else {
        for (const u of state.users || []) {
          if (term && !String(u.username || "").toLowerCase().includes(term)) continue;
          options.push({ id: u.id, label: `${u.username} (#${u.id})` });
        }
      }

      sel.innerHTML = "";
      for (const o of options) {
        sel.appendChild(el("option", { value: o.id, text: o.label }));
      }

      // preserve selection if possible
      if (state.access.principalId) {
        const exists = options.some((o) => o.id === state.access.principalId);
        if (exists) sel.value = String(state.access.principalId);
      }
    }

    async function refresh() {
      await ensurePrincipalsLoaded();
      await ensureConfigTreeLoaded();
      renderPrincipalOptions();
      await refreshGrants();
      renderAccessTree();
      renderGrantsList();
      renderAccessPreview();
    }

    async function refreshGrants() {
      const pid = state.access.principalId;
      const ptype = state.access.principalType;

      if (!pid) {
        setStatus("access-status", "Select a principal.", "");
        state.access.grants = [];
        state.access.grantByKey = new Map();
        renderGrantsList();
        renderAccessTree();
        return;
      }

      setStatus("access-status", "Loading grants…");
      try {
        const url = ptype === "role" ? `/admin/access/roles/${pid}/grants` : `/admin/access/users/${pid}/grants`;
        const grants = await api.get(url);
        state.access.grants = Array.isArray(grants) ? grants : [];
        state.access.grantByKey = new Map();
        for (const g of state.access.grants) {
          const key = `${g.resource_type}:${g.resource_id}`;
          state.access.grantByKey.set(key, g);
        }
        setStatus("access-status", `Loaded ${state.access.grants.length} grants.`, "ok");
      } catch (err) {
        state.access.grants = [];
        state.access.grantByKey = new Map();
        setStatus("access-status", err?.message || "Failed to load grants", "error");
      }

      renderGrantsList();
      renderAccessTree();
    }

    async function clearAllGrants() {
      const pid = state.access.principalId;
      if (!pid) return;

      const ptype = state.access.principalType;
      const msg = ptype === "role" ? "Clear ALL grants for this role?" : "Clear ALL grants for this user?";
      if (!confirmDanger(msg)) return;

      try {
        const url = ptype === "role" ? `/admin/access/roles/${pid}/grants` : `/admin/access/users/${pid}/grants`;
        await api.delete(url);
        toast("Grants cleared");
        await refreshGrants();
      } catch (err) {
        toast(err?.message || "Failed to clear grants", "error");
      }
    }

    function accessRank(level) {
      if (!level) return 0;
      return level === "write" ? 2 : 1;
    }

    function maxLevel(a, b) {
      return accessRank(a) >= accessRank(b) ? a : b;
    }

    function computeEffective(nodeInfo, inherited) {
      const explicit = state.access.grantByKey.get(nodeInfo.key) || null;
      const explicitLevel = explicit?.access_level || null;

      const inheritedLevel = inherited?.level || null;

      let effective = null;
      let source = null;
      let sourceKind = null;

      if (explicitLevel && inheritedLevel) {
        effective = maxLevel(explicitLevel, inheritedLevel);
        if (accessRank(explicitLevel) >= accessRank(inheritedLevel)) {
          source = explicit;
          sourceKind = "explicit";
        } else {
          source = inherited?.sourceGrant || null;
          sourceKind = "inherited";
        }
      } else if (explicitLevel) {
        effective = explicitLevel;
        source = explicit;
        sourceKind = "explicit";
      } else if (inheritedLevel) {
        effective = inheritedLevel;
        source = inherited?.sourceGrant || null;
        sourceKind = "inherited";
      }

      return { explicit, explicitLevel, effective, source, sourceKind };
    }

    function renderAccessTree() {
      const root = $("access-tree");
      if (!root) return;

      const term = String($("access-search")?.value || "").trim().toLowerCase();
      root.innerHTML = "";

      const build = (rawNode, nodeKey, inherited) => {
        const info = state.cfgIndex.get(nodeKey);
        if (!info) return null;

        const label = String(info.name || "").toLowerCase();

        // Compute effective access for this node
        const cur = computeEffective(info, inherited);

        // Determine inheritance to pass down
        let nextInherited = inherited;
        if (info.type !== "datapoint") {
          // if explicit includes descendants, it becomes a candidate for inheritance
          const eg = cur.explicit;
          if (eg && eg.include_descendants) {
            const candidate = {
              level: eg.access_level,
              sourceGrant: eg,
              sourceKey: info.key,
            };
            if (!nextInherited) nextInherited = candidate;
            else {
              // choose higher rank; if equal choose nearer (candidate)
              if (accessRank(candidate.level) > accessRank(nextInherited.level)) nextInherited = candidate;
              else if (accessRank(candidate.level) === accessRank(nextInherited.level)) nextInherited = candidate;
            }
          }
        }

        // Children: containers/equipment/datapoints
        const children = [];
        if (info.type === "plc") {
          for (const c of rawNode.containers || []) {
            const ck = `container:${c.id}`;
            const child = build(c, ck, nextInherited);
            if (child) children.push(child);
          }
          for (const dp of rawNode.datapoints || []) {
            const dk = `datapoint:${dp.id}`;
            const child = build(dp, dk, nextInherited);
            if (child) children.push(child);
          }
        } else if (info.type === "container") {
          for (const e of rawNode.equipment || []) {
            const ek = `equipment:${e.id}`;
            const child = build(e, ek, nextInherited);
            if (child) children.push(child);
          }
          for (const dp of rawNode.datapoints || []) {
            const dk = `datapoint:${dp.id}`;
            const child = build(dp, dk, nextInherited);
            if (child) children.push(child);
          }
        } else if (info.type === "equipment") {
          for (const dp of rawNode.datapoints || []) {
            const dk = `datapoint:${dp.id}`;
            const child = build(dp, dk, nextInherited);
            if (child) children.push(child);
          }
        } else if (info.type === "datapoint") {
          // datapoints are leaves
        }

        const selfMatch = !term || label.includes(term);
        const anyChildMatch = children.some((c) => c._matched);
        const matched = selfMatch || anyChildMatch;
        if (!matched) return null;

        const hasChildren = children.length > 0;

        const badges = [];
        badges.push(el("span", { class: "badge", text: info.type }));

        const inheritedLevel = inherited?.level || null;

        if (cur.explicitLevel) {
          const cls = cur.explicitLevel === "write" ? "write" : "read";
          badges.push(el("span", { class: `badge ${cls} explicit`, text: `Explicit ${cur.explicitLevel}` }));
        }
        if (inheritedLevel) {
          const cls = inheritedLevel === "write" ? "write" : "read";
          badges.push(el("span", { class: `badge ${cls} inherited`, text: `Inherited ${inheritedLevel}` }));
        }
        if (!cur.explicitLevel && !inheritedLevel) {
          badges.push(el("span", { class: "badge", text: "No access" }));
        }

        // controls represent explicit grant state only
        const readChecked = !!cur.explicitLevel;
        const writeChecked = cur.explicitLevel === "write";
        const descChecked = cur.explicit ? !!cur.explicit.include_descendants : true;

        const readToggle = el("label", { class: "switch" }, [
          el("input", {
            type: "checkbox",
            dataset: { grantToggle: "read", nodeKey: info.key },
            checked: readChecked ? "true" : null,
          }),
          el("span", { text: "R" }),
        ]);

        const writeToggle = el("label", { class: "switch" }, [
          el("input", {
            type: "checkbox",
            dataset: { grantToggle: "write", nodeKey: info.key },
            checked: writeChecked ? "true" : null,
          }),
          el("span", { text: "W" }),
        ]);

        const descToggle =
          info.type === "plc" || info.type === "container" || info.type === "equipment"
            ? el("label", { class: `switch ${readChecked ? "" : "disabled"}` }, [
                el("input", {
                  type: "checkbox",
                  dataset: { grantToggle: "desc", nodeKey: info.key },
                  checked: descChecked ? "true" : null,
                  disabled: readChecked ? null : "true",
                }),
                el("span", { text: "Desc" }),
              ])
            : null;

        const controls = el("div", { class: "meta" }, [readToggle, writeToggle, descToggle].filter(Boolean));

        const left = el("div", { class: "left" }, [
          hasChildren
            ? el("button", { class: "twisty", type: "button", dataset: { toggle: info.key }, text: state.access.collapsed.has(info.key) ? "▸" : "▾" })
            : el("span", { class: "badge", text: " " }),
          el("span", { class: "title", text: info.name || info.key }),
          ...badges,
        ]);

        const row = el("div", { class: "node", dataset: { nodeKey: info.key } }, [left, controls]);
        if (state.access.selectedKey === info.key) row.classList.add("selected");

        const container = el("div", {}, [row]);
        if (hasChildren) {
          const wrap = el("div", { class: "children" }, children.map((c) => c.el));
          if (state.access.collapsed.has(info.key)) wrap.classList.add("hidden");
          container.appendChild(wrap);
        }

        return { el: container, _matched: matched };
      };

      for (const plc of state.cfgTree || []) {
        const k = `plc:${plc.id}`;
        const rootNode = build(plc, k, null);
        if (rootNode) root.appendChild(rootNode.el);
      }
    }

    function renderGrantsList() {
      const box = $("grants-list");
      if (!box) return;

      const grants = state.access.grants || [];
      if (!state.access.principalId) {
        box.textContent = "Select a principal to load grants.";
        box.classList.add("muted");
        return;
      }

      if (!grants.length) {
        box.textContent = "No explicit grants for this principal.";
        box.classList.add("muted");
        return;
      }

      box.classList.remove("muted");
      box.innerHTML = "";

      // Resolve display names from cfgIndex if available
      const resolveName = (g) => {
        const key = `${g.resource_type}:${g.resource_id}`;
        const info = state.cfgIndex.get(key);
        return info?.name || key;
      };

      for (const g of grants) {
        const name = resolveName(g);
        const top = el("div", { class: "top" }, [
          el("div", {}, [
            el("div", { style: "font-weight:700", text: name }),
            el("div", { class: "sub", text: `${g.resource_type} #${g.resource_id}` }),
          ]),
          el("div", { class: "actions-group" }, [
            el("span", { class: `badge ${g.access_level === "write" ? "write" : "read"} explicit`, text: g.access_level }),
            g.resource_type !== "datapoint" && g.include_descendants
              ? el("span", { class: "badge explicit", text: "Desc" })
              : null,
            el("button", {
              class: "btn small danger",
              type: "button",
              dataset: { grantId: String(g.id) },
              text: "Remove",
            }),
          ]),
        ]);

        box.appendChild(el("div", { class: "item" }, [top]));
      }
    }

    function renderAccessPreview() {
      const box = $("access-preview");
      if (!box) return;

      const key = state.access.selectedKey;
      if (!key) {
        box.textContent = "Select a node to see effective access.";
        box.classList.add("muted");
        return;
      }

      const info = state.cfgIndex.get(key);
      if (!info) return;

      // For preview, compute effective by walking ancestors with include_descendants
      const chain = [];
      let curKey = key;
      while (curKey) {
        const i = state.cfgIndex.get(curKey);
        if (!i) break;
        chain.unshift(i);
        curKey = i.parentKey;
      }

      let inherited = null;
      let computed = null;
      for (const i of chain) {
        computed = computeEffective(i, inherited);
        // update inherited candidate for descendants (same logic as in render)
        if (i.type !== "datapoint") {
          const eg = computed.explicit;
          if (eg && eg.include_descendants) {
            const candidate = { level: eg.access_level, sourceGrant: eg, sourceKey: i.key };
            if (!inherited) inherited = candidate;
            else {
              if (accessRank(candidate.level) > accessRank(inherited.level)) inherited = candidate;
              else if (accessRank(candidate.level) === accessRank(inherited.level)) inherited = candidate;
            }
          }
        }
      }

      const explicit = state.access.grantByKey.get(key) || null;
      const effective = computed?.effective || null;
      const source = computed?.sourceKind === "explicit" ? "Explicit grant" : computed?.sourceKind === "inherited" ? `Inherited from ${computed?.source?.resource_type} #${computed?.source?.resource_id}` : "None";

      box.classList.remove("muted");
      box.innerHTML = "";

      const quickActions = [];
      const hasPrincipal = !!state.access.principalId;

      if (hasPrincipal) {
        // Quick actions for the selected node. These simply upsert the explicit grant.
        if (info.type === "plc" || info.type === "container" || info.type === "equipment") {
          quickActions.push(
            el("button", { class: "btn small", type: "button", text: "Grant Read (Desc)", onclick: () => quickGrant(key, "read", true) })
          );
          quickActions.push(
            el("button", { class: "btn small", type: "button", text: "Grant Write (Desc)", onclick: () => quickGrant(key, "write", true) })
          );
        } else if (info.type === "datapoint") {
          quickActions.push(
            el("button", { class: "btn small", type: "button", text: "Grant Read", onclick: () => quickGrant(key, "read", false) })
          );
          quickActions.push(
            el("button", { class: "btn small", type: "button", text: "Grant Write", onclick: () => quickGrant(key, "write", false) })
          );
        }

        if (explicit?.id) {
          quickActions.push(
            el("button", { class: "btn small danger", type: "button", text: "Clear Explicit", onclick: () => deleteGrantById(explicit.id) })
          );
        }
      }

      box.appendChild(el("div", { class: "item" }, [
        el("div", { class: "top" }, [
          el("div", {}, [
            el("div", { style: "font-weight:700", text: info.name }),
            el("div", { class: "sub", text: `${info.type} #${info.id}` }),
          ]),
          el("div", { class: "actions-group" }, [
            effective
              ? el("span", { class: `badge ${effective === "write" ? "write" : "read"} ${computed.sourceKind === "explicit" ? "explicit" : "inherited"}`, text: `Effective ${effective}` })
              : el("span", { class: "badge", text: "Effective none" }),
          ]),
        ]),
        el("div", { class: "sub", text: `Source: ${source}` }),
        explicit
          ? el("div", { class: "sub", text: `Explicit: ${explicit.access_level}${explicit.resource_type !== "datapoint" ? ` (desc=${explicit.include_descendants ? "on" : "off"})` : ""}` })
          : el("div", { class: "sub", text: "Explicit: none" }),
        quickActions.length
          ? el("div", { class: "row", style: "justify-content:flex-start; gap:8px; flex-wrap:wrap; margin-top:10px;" }, quickActions)
          : null,
      ]));
    }

    async function quickGrant(nodeKey, accessLevel, includeDescendants) {
      const pid = state.access.principalId;
      const ptype = state.access.principalType;
      if (!pid) return;

      const info = state.cfgIndex.get(nodeKey);
      if (!info) return;

      const url = ptype === "role" ? `/admin/access/roles/${pid}/grants` : `/admin/access/users/${pid}/grants`;
      const include_descendants =
        info.type === "plc" || info.type === "container" || info.type === "equipment" ? !!includeDescendants : false;

      try {
        await api.put(url, {
          json: {
            resource_type: info.type,
            resource_id: info.id,
            access_level: accessLevel,
            include_descendants,
          },
        });
        toast("Grant saved");
        await refreshGrants();
        renderAccessPreview();
      } catch (err) {
        toast(err?.message || "Failed to save grant", "error");
      }
    }

    async function onGrantToggleChanged(nodeKey) {
      const pid = state.access.principalId;
      const ptype = state.access.principalType;
      if (!pid) return;

      const info = state.cfgIndex.get(nodeKey);
      if (!info) return;

      const node = qs(`[data-node-key="${CSS.escape(nodeKey)}"]`, $("access-tree"));
      if (!node) return;

      const read = node.querySelector('input[data-grant-toggle="read"]');
      const write = node.querySelector('input[data-grant-toggle="write"]');
      const desc = node.querySelector('input[data-grant-toggle="desc"]');

      // Normalize: write => read, !read => !write
      if (write?.checked) read.checked = true;
      if (!read?.checked && write) write.checked = false;

      // enable/disable desc toggle
      if (desc) {
        desc.disabled = !read.checked;
        desc.closest(".switch")?.classList.toggle("disabled", !read.checked);
      }

      const existing = state.access.grantByKey.get(nodeKey) || null;

      const level = write?.checked ? "write" : read?.checked ? "read" : null;
      const include_descendants =
        info.type === "plc" || info.type === "container" || info.type === "equipment" ? !!desc?.checked : false;

      try {
        if (!level) {
          if (existing) {
            await deleteGrantById(existing.id, { confirm: false });
          }
          return;
        }

        const url = ptype === "role" ? `/admin/access/roles/${pid}/grants` : `/admin/access/users/${pid}/grants`;
        await api.put(url, {
          json: {
            resource_type: info.type,
            resource_id: info.id,
            access_level: level,
            include_descendants,
          },
        });

        toast("Grant saved");
        await refreshGrants();
      } catch (err) {
        toast(err?.message || "Failed to update grant", "error");
        await refreshGrants();
      }
    }

    async function deleteGrantById(grantId, opts = {}) {
      const { confirm = true } = opts || {};
      const pid = state.access.principalId;
      const ptype = state.access.principalType;
      if (!pid || !grantId) return;

      if (confirm && !confirmDanger("Remove this explicit grant?")) return;

      try {
        const url =
          ptype === "role"
            ? `/admin/access/roles/${pid}/grants/${grantId}`
            : `/admin/access/users/${pid}/grants/${grantId}`;
        await api.delete(url);
        toast("Grant removed");
        await refreshGrants();
      } catch (err) {
        toast(err?.message || "Failed to remove grant", "error");
      }
    }

    return { show };
  })();

  // -----------------------------
  // Boot
  // -----------------------------
  document.addEventListener("DOMContentLoaded", async () => {
    if (document.body.classList.contains("admin-body")) {
      await initLoginPage();
      return;
    }
    if (document.body.classList.contains("admin-app")) {
      await initAdminApp();
      return;
    }
  });
})();
