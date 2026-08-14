/*
 * priority-overrides-card
 *
 * Shows every entity currently held above the Default level, what is holding
 * it, who set it, and how long its lease has left - with a one-click release
 * per row.
 *
 * Deliberately dependency-free vanilla custom elements: no build step, no CDN,
 * nothing to break when Home Assistant bumps its frontend. It reads only
 * `sensor.active_overrides`, whose attributes already carry everything needed.
 */

const PRIORITY_COLORS = {
  1: "var(--error-color, #db4437)",
  2: "var(--warning-color, #ffa600)",
  3: "var(--info-color, #039be5)",
  4: "var(--success-color, #43a047)",
  5: "var(--secondary-text-color)",
};

// Must track PRIORITY_NAMES in const.py.
const PRIORITY_LABELS = {
  1: "Manual Emergency",
  2: "Automatic Emergency",
  3: "Manual",
  4: "Automatic",
  5: "Default",
};

class PriorityOverridesCard extends HTMLElement {
  static getStubConfig() {
    return { entity: "sensor.active_overrides", title: "Priority overrides" };
  }

  setConfig(config) {
    this._config = {
      entity: "sensor.active_overrides",
      title: "Priority overrides",
      ...(config || {}),
    };
    this._root = null;
  }

  getCardSize() {
    const n = Object.keys(this._overrides() || {}).length;
    return 1 + Math.max(1, n);
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
    // Keep the lease countdowns moving without waiting on a state change.
    if (!this._timer) {
      this._timer = window.setInterval(() => this._renderRowsOnly(), 1000);
    }
  }

  disconnectedCallback() {
    if (this._timer) {
      window.clearInterval(this._timer);
      this._timer = null;
    }
  }

  _overrides() {
    const st = this._hass && this._hass.states[this._config.entity];
    return (st && st.attributes && st.attributes.overrides) || {};
  }

  _remaining(expiresAt) {
    if (!expiresAt) return null;
    const ms = new Date(expiresAt).getTime() - Date.now();
    if (ms <= 0) return "expiring";
    const s = Math.floor(ms / 1000);
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    if (h > 0) return `${h}h ${m}m`;
    if (m > 0) return `${m}m ${sec}s`;
    return `${sec}s`;
  }

  _relinquish(entityId, priority) {
    this._hass.callService("priority", "relinquish", {
      entity_id: entityId,
      priority: priority,
    });
  }

  _relinquishAll() {
    const ids = Object.keys(this._overrides());
    if (!ids.length) return;
    this._hass.callService("priority", "relinquish_all", { entity_id: ids });
  }

  _moreInfo(entityId) {
    // Must be a CustomEvent: a plain Event drops `detail`, and the frontend
    // reads entityId from exactly there.
    this.dispatchEvent(
      new CustomEvent("hass-more-info", {
        bubbles: true,
        composed: true,
        detail: { entityId },
      })
    );
  }

  _render() {
    if (!this._root) {
      this._root = document.createElement("ha-card");
      this._root.header = this._config.title;
      this._style = document.createElement("style");
      this._style.textContent = `
        .body { padding: 0 16px 8px; }
        .empty {
          padding: 8px 0 16px;
          color: var(--secondary-text-color);
        }
        .group {
          padding: 6px 0 4px;
          border-bottom: 1px solid var(--divider-color);
        }
        .group:last-child { border-bottom: none; }
        .row {
          display: flex;
          align-items: center;
          gap: 12px;
          padding: 6px 0;
        }
        .row.driving .meta { color: var(--primary-text-color); }
        .pill {
          flex: 0 0 auto;
          min-width: 78px;
          text-align: center;
          padding: 3px 8px;
          border-radius: 12px;
          font-size: 0.75rem;
          font-weight: 600;
          color: #fff;
        }
        .main { flex: 1 1 auto; min-width: 0; }
        .name {
          font-weight: 500;
          cursor: pointer;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .name:hover { text-decoration: underline; }
        .meta {
          font-size: 0.75rem;
          color: var(--secondary-text-color);
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .lease {
          flex: 0 0 auto;
          font-size: 0.75rem;
          font-variant-numeric: tabular-nums;
          color: var(--secondary-text-color);
        }
        .footer {
          display: flex;
          justify-content: flex-end;
          padding: 4px 8px 12px;
        }
      `;
      this._body = document.createElement("div");
      this._body.className = "body";
      this._footer = document.createElement("div");
      this._footer.className = "footer";
      this._root.appendChild(this._style);
      this._root.appendChild(this._body);
      this._root.appendChild(this._footer);
      this.appendChild(this._root);
    }
    this._renderRowsOnly();
  }

  _renderRowsOnly() {
    if (!this._body || !this._hass) return;
    const overrides = this._overrides();
    const ids = Object.keys(overrides).sort(
      (a, b) => overrides[a].priority - overrides[b].priority
    );

    if (!ids.length) {
      this._body.innerHTML =
        '<div class="empty">Nothing is overridden. Everything is running at Default.</div>';
      this._footer.innerHTML = "";
      return;
    }

    this._body.innerHTML = ids
      .map((id) => {
        const o = overrides[id];
        // Older sensor payloads carried only the winning slot. Fall back to it
        // so a cached card against a new sensor, or the reverse, still renders.
        const levels = o.levels || {
          [String(o.priority)]: o,
        };
        const rows = Object.keys(levels)
          .map(Number)
          .sort((a, b) => a - b)
          .map((p) => {
            const lv = levels[String(p)];
            const left = this._remaining(lv.expires_at);
            const by = lv.written_by
              ? String(lv.written_by).replace(/^user:/, "")
              : "unknown";
            const driving = p === o.priority;
            return `
          <div class="row${driving ? " driving" : ""}">
            <div class="pill" style="background:${
              PRIORITY_COLORS[p] || "var(--primary-color)"
            }">${p} · ${lv.priority_name}</div>
            <div class="main">
              <div class="meta">${lv.service} · set by ${by}${
              driving ? " · driving" : ""
            }</div>
            </div>
            <div class="lease">${left ? "releases in " + left : "held"}</div>
            <mwc-button dense data-release="${id}" data-priority="${p}">Release</mwc-button>
          </div>`;
          })
          .join("");
        return `
          <div class="group">
            <div class="name" data-entity="${id}">${o.friendly_name || id}</div>
            ${rows}
          </div>`;
      })
      .join("");

    this._body.querySelectorAll("[data-release]").forEach((btn) => {
      btn.onclick = () =>
        this._relinquish(
          btn.getAttribute("data-release"),
          Number(btn.getAttribute("data-priority"))
        );
    });
    this._body.querySelectorAll("[data-entity]").forEach((el) => {
      el.onclick = () => this._moreInfo(el.getAttribute("data-entity"));
    });

    this._footer.innerHTML = `<mwc-button dense id="rall">Release all</mwc-button>`;
    const all = this._footer.querySelector("#rall");
    if (all) all.onclick = () => this._relinquishAll();
  }
}

customElements.define("priority-overrides-card", PriorityOverridesCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "priority-overrides-card",
  name: "Priority Overrides",
  description:
    "Entities currently held above the Default priority level, with one-click release.",
  preview: true,
});

console.info("%c PRIORITY-OVERRIDES-CARD ", "background:#039be5;color:#fff");


/*
 * priority-control-card
 *
 * The operating end of the system. The overrides card shows what is held and
 * lets you release it; this one is how you take hold in the first place.
 *
 * Pick a level and a lease at the top, then act on any entity in the list. The
 * two controls are shared rather than repeated per row because the common case
 * is "put these on at Manual Emergency for half an hour", not a different level
 * for every light.
 *
 * Example:
 *   type: custom:priority-control-card
 *   title: Emergency lighting
 *   default_priority: 1
 *   default_ttl: 1800
 *   entities:
 *     - light.living_room
 *     - light.outdoor_lights_2
 */

const TTL_PRESETS = [
  { value: 0, label: "No limit" },
  { value: 300, label: "5 minutes" },
  { value: 900, label: "15 minutes" },
  { value: 1800, label: "30 minutes" },
  { value: 3600, label: "1 hour" },
  { value: 7200, label: "2 hours" },
  { value: 14400, label: "4 hours" },
  { value: 28800, label: "8 hours" },
];

// Domains whose "on" and "off" are not called turn_on / turn_off.
const DOMAIN_ACTIONS = {
  cover: { on: "open_cover", off: "close_cover", onLabel: "Open", offLabel: "Close" },
  valve: { on: "open_valve", off: "close_valve", onLabel: "Open", offLabel: "Close" },
  lock: { on: "lock", off: "unlock", onLabel: "Lock", offLabel: "Unlock" },
};

class PriorityControlCard extends HTMLElement {
  static getStubConfig(hass) {
    const first =
      hass && Object.keys(hass.states).find((e) => e.startsWith("light."));
    return {
      title: "Priority control",
      default_priority: 3,
      default_ttl: 0,
      entities: first ? [first] : [],
    };
  }

  setConfig(config) {
    if (!config || !Array.isArray(config.entities)) {
      throw new Error(
        "priority-control-card: `entities` must be a list of entity ids"
      );
    }
    this._config = {
      title: "Priority control",
      default_priority: 3,
      default_ttl: 0,
      ...config,
    };
    this._priority = Number(this._config.default_priority) || 3;
    this._ttl = Number(this._config.default_ttl) || 0;
    this._root = null;
  }

  getCardSize() {
    return 2 + (this._config.entities || []).length;
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _overridesSensor() {
    const st = this._hass && this._hass.states["sensor.active_overrides"];
    return (st && st.attributes && st.attributes.overrides) || {};
  }

  _actions(entityId) {
    const domain = entityId.split(".")[0];
    return (
      DOMAIN_ACTIONS[domain] || {
        on: "turn_on",
        off: "turn_off",
        onLabel: "On",
        offLabel: "Off",
      }
    );
  }

  _command(entityId, which) {
    const domain = entityId.split(".")[0];
    const acts = this._actions(entityId);
    const data = {
      entity_id: entityId,
      priority: this._priority,
    };
    // A lease is meaningless at Default - there is nothing below to fall back
    // to - and the integration rejects it outright, so do not send one.
    if (this._ttl > 0 && this._priority < 5) {
      data.priority_ttl = this._ttl;
    }
    this._hass.callService(domain, which === "on" ? acts.on : acts.off, data);
  }

  _release(entityId) {
    this._hass.callService("priority", "relinquish_all", {
      entity_id: entityId,
    });
  }

  _render() {
    if (!this._hass) return;
    if (!this._root) {
      this._root = document.createElement("ha-card");
      this._root.header = this._config.title;
      const style = document.createElement("style");
      style.textContent = `
        .controls {
          display: flex;
          gap: 12px;
          padding: 4px 16px 12px;
          flex-wrap: wrap;
          align-items: flex-end;
        }
        .field { display: flex; flex-direction: column; gap: 4px; flex: 1 1 150px; }
        .field label {
          font-size: 0.7rem;
          text-transform: uppercase;
          letter-spacing: 0.04em;
          color: var(--secondary-text-color);
        }
        select {
          padding: 8px;
          border-radius: 6px;
          border: 1px solid var(--divider-color);
          background: var(--card-background-color, var(--ha-card-background));
          color: var(--primary-text-color);
          font: inherit;
          width: 100%;
        }
        .note {
          padding: 0 16px 8px;
          font-size: 0.75rem;
          color: var(--secondary-text-color);
        }
        .rows { padding: 0 16px 8px; }
        .row {
          display: flex;
          align-items: center;
          gap: 10px;
          padding: 8px 0;
          border-top: 1px solid var(--divider-color);
        }
        .nm { flex: 1 1 auto; min-width: 0; }
        .nm .t {
          font-weight: 500;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .nm .s { font-size: 0.75rem; color: var(--secondary-text-color); }
        .held {
          font-size: 0.7rem;
          font-weight: 600;
          padding: 2px 6px;
          border-radius: 10px;
          color: #fff;
          white-space: nowrap;
        }
        .missing { color: var(--error-color, #db4437); }
      `;
      this._controls = document.createElement("div");
      this._controls.className = "controls";
      this._note = document.createElement("div");
      this._note.className = "note";
      this._rows = document.createElement("div");
      this._rows.className = "rows";
      this._root.appendChild(style);
      this._root.appendChild(this._controls);
      this._root.appendChild(this._note);
      this._root.appendChild(this._rows);
      this.appendChild(this._root);
      this._renderControls();
    }
    this._renderRows();
  }

  _renderControls() {
    const prioOpts = [1, 2, 3, 4, 5]
      .map(
        (p) =>
          `<option value="${p}"${p === this._priority ? " selected" : ""}>${p} - ${
            PRIORITY_LABELS[p]
          }</option>`
      )
      .join("");
    const ttlOpts = TTL_PRESETS.map(
      (t) =>
        `<option value="${t.value}"${
          t.value === this._ttl ? " selected" : ""
        }>${t.label}</option>`
    ).join("");

    this._controls.innerHTML = `
      <div class="field">
        <label for="p">Priority</label>
        <select id="p">${prioOpts}</select>
      </div>
      <div class="field">
        <label for="t">Hold for</label>
        <select id="t">${ttlOpts}</select>
      </div>`;

    this._controls.querySelector("#p").onchange = (e) => {
      this._priority = Number(e.target.value);
      this._renderControls();
      this._renderRows();
    };
    this._controls.querySelector("#t").onchange = (e) => {
      this._ttl = Number(e.target.value);
      this._renderControls();
      this._renderRows();
    };
  }

  _renderRows() {
    const overrides = this._overridesSensor();

    this._note.textContent =
      this._priority === 5
        ? "Default: behaves exactly as a normal command. The last one wins."
        : this._ttl > 0
        ? `Commands take hold at ${PRIORITY_LABELS[this._priority]} and release themselves automatically.`
        : `Commands take hold at ${PRIORITY_LABELS[this._priority]} until released.`;

    this._rows.innerHTML = (this._config.entities || [])
      .map((id) => {
        const st = this._hass.states[id];
        if (!st) {
          return `<div class="row"><div class="nm"><div class="t missing">${id}</div><div class="s">not found</div></div></div>`;
        }
        const acts = this._actions(id);
        const held = overrides[id];
        const badge = held
          ? `<span class="held" style="background:${
              PRIORITY_COLORS[held.priority]
            }">${held.priority_name}</span>`
          : "";
        return `
          <div class="row">
            <div class="nm">
              <div class="t">${st.attributes.friendly_name || id}</div>
              <div class="s">${st.state}</div>
            </div>
            ${badge}
            <mwc-button dense data-on="${id}">${acts.onLabel}</mwc-button>
            <mwc-button dense data-off="${id}">${acts.offLabel}</mwc-button>
            ${
              held
                ? `<mwc-button dense data-rel="${id}">Release</mwc-button>`
                : ""
            }
          </div>`;
      })
      .join("");

    this._rows.querySelectorAll("[data-on]").forEach((b) => {
      b.onclick = () => this._command(b.getAttribute("data-on"), "on");
    });
    this._rows.querySelectorAll("[data-off]").forEach((b) => {
      b.onclick = () => this._command(b.getAttribute("data-off"), "off");
    });
    this._rows.querySelectorAll("[data-rel]").forEach((b) => {
      b.onclick = () => this._release(b.getAttribute("data-rel"));
    });
  }
}

customElements.define("priority-control-card", PriorityControlCard);

window.customCards.push({
  type: "priority-control-card",
  name: "Priority Control",
  description:
    "Command entities at a chosen priority level, with an optional lease.",
  preview: false,
});


/* ===================================================================
 * Shared priority row
 *
 * One element, used by both the tile-card feature and the more-info
 * injection. Everything below is domain-agnostic on purpose: there is no
 * per-domain code anywhere, only the DOMAIN_ACTIONS verb table above, so a
 * light, a switch, a cover, an input_boolean and a valve all behave the same.
 * =================================================================== */

// Must track ARBITRATED_SERVICES in const.py.
const ARBITRATED_DOMAINS = new Set([
  "light",
  "switch",
  "fan",
  "cover",
  "climate",
  "water_heater",
  "humidifier",
  "lock",
  "valve",
  "media_player",
  "input_boolean",
  "input_number",
]);

// Must track ARBITRATED_SERVICES in const.py. Used to decide whether a call is
// one the integration will accept a priority on - sending the field to a
// service that does not declare it would fail schema validation and break an
// ordinary click.
const ARBITRATED_SERVICES = {
  light: ["turn_on", "turn_off", "toggle"],
  switch: ["turn_on", "turn_off", "toggle"],
  fan: [
    "turn_on",
    "turn_off",
    "toggle",
    "set_percentage",
    "set_preset_mode",
    "set_direction",
    "oscillate",
  ],
  cover: [
    "open_cover",
    "close_cover",
    "stop_cover",
    "toggle",
    "set_cover_position",
    "set_cover_tilt_position",
    "open_cover_tilt",
    "close_cover_tilt",
    "stop_cover_tilt",
  ],
  climate: [
    "turn_on",
    "turn_off",
    "toggle",
    "set_temperature",
    "set_hvac_mode",
    "set_fan_mode",
    "set_preset_mode",
    "set_humidity",
    "set_swing_mode",
  ],
  water_heater: ["turn_on", "turn_off", "set_temperature", "set_operation_mode"],
  humidifier: ["turn_on", "turn_off", "toggle", "set_humidity", "set_mode"],
  lock: ["lock", "unlock", "open"],
  valve: [
    "open_valve",
    "close_valve",
    "stop_valve",
    "toggle",
    "set_valve_position",
  ],
  media_player: ["turn_on", "turn_off", "toggle", "volume_set"],
  input_boolean: ["turn_on", "turn_off", "toggle"],
  input_number: ["set_value"],
};

/* -------------------------------------------------------------------
 * Command interception
 *
 * The pickers are modifiers, not commands. You pick a level and a lease, then
 * use the entity's ordinary controls - the toggle, the brightness slider, the
 * position handle - and those commands carry the level.
 *
 * That means catching the service calls the built-in controls make.
 * `hass.callService` is the single funnel every one of them goes through, so
 * it is wrapped once and consults the selection map below. With nothing
 * selected the map is empty and the wrapper is a pure pass-through, so an
 * ordinary click behaves exactly as it always did.
 * ------------------------------------------------------------------- */

// entity_id -> { priority, ttl }. Only non-Default selections are ever stored.
const SELECTIONS = new Map();

function _isArbitrated(domain, service) {
  const list = ARBITRATED_SERVICES[domain];
  return !!list && list.indexOf(service) !== -1;
}

function _targetEntities(data, target) {
  const out = [];
  [data && data.entity_id, target && target.entity_id].forEach((v) => {
    if (!v) return;
    if (Array.isArray(v)) out.push(...v);
    else out.push(v);
  });
  return out;
}

function _wrapCallService(hass) {
  if (!hass || typeof hass.callService !== "function") return;
  if (hass.callService.__priorityWrapped) return;

  const original = hass.callService.bind(hass);
  const wrapped = function (domain, service, data, target, ...rest) {
    try {
      if (SELECTIONS.size && _isArbitrated(domain, service)) {
        const ids = _targetEntities(data, target);
        const sel = ids.map((id) => SELECTIONS.get(id)).find(Boolean);
        if (sel) {
          data = { ...(data || {}), priority: sel.priority };
          if (sel.ttl > 0 && sel.priority < 5) data.priority_ttl = sel.ttl;
        }
      }
    } catch (err) {
      // An ordinary click must never fail because of this.
      console.debug("priority: call interception skipped", err);
    }
    return original(domain, service, data, target, ...rest);
  };
  wrapped.__priorityWrapped = true;

  try {
    hass.callService = wrapped;
  } catch (err) {
    console.debug("priority: could not wrap callService", err);
  }
}

const ROW_STYLE = `
  :host { display: block; }
  .wrap {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    flex-wrap: wrap;
    padding: 8px 0 4px;
  }
  .hint {
    text-align: center;
    font-size: 0.75rem;
    color: var(--secondary-text-color);
    padding-bottom: 6px;
  }
  select:disabled { opacity: 0.5; }
  .slots {
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding: 4px 0 8px;
    font-size: 0.78rem;
  }
  .slot {
    display: flex;
    align-items: baseline;
    gap: 8px;
    padding: 3px 8px;
    border-radius: 6px;
    opacity: 0.65;
  }
  /* The winning slot is the one actually driving the device; everything else
     is queued underneath it. */
  .slot.win {
    opacity: 1;
    background: var(--secondary-background-color);
    font-weight: 600;
  }
  .slot.none { opacity: 0.6; font-style: italic; }
  .lvl {
    flex: 0 0 auto;
    min-width: 132px;
    font-weight: 600;
    white-space: nowrap;
  }
  .act { flex: 1 1 auto; }
  .rem {
    flex: 0 0 auto;
    font-variant-numeric: tabular-nums;
    color: var(--secondary-text-color);
    min-width: 62px;
    text-align: right;
  }
  .rel-one {
    flex: 0 0 auto;
    padding: 2px 8px;
    font-size: 0.72rem;
    border-radius: 12px;
    border: 1px solid var(--divider-color);
    background: transparent;
    color: var(--secondary-text-color);
    cursor: pointer;
  }
  .rel-one:hover {
    background: var(--secondary-background-color);
    color: var(--primary-text-color);
  }
  /* Keeps the Default row's columns lined up with the ones that have a button. */
  .rel-spacer { flex: 0 0 auto; width: 62px; }
  select {
    padding: 6px 8px;
    border-radius: 6px;
    border: 1px solid var(--divider-color);
    background: var(--card-background-color, var(--ha-card-background));
    color: var(--primary-text-color);
    font: inherit;
    font-size: 0.85rem;
  }
  button {
    padding: 6px 12px;
    border-radius: 16px;
    border: 1px solid var(--divider-color);
    background: var(--card-background-color, var(--ha-card-background));
    color: var(--primary-text-color);
    font: inherit;
    font-size: 0.85rem;
    cursor: pointer;
  }
  button:hover { background: var(--secondary-background-color); }
  .held {
    font-size: 0.7rem;
    font-weight: 600;
    padding: 3px 8px;
    border-radius: 10px;
    color: #fff;
  }
  .spacer { flex: 1 1 auto; }
`;

const SERVICE_LABELS = {
  turn_on: "ON",
  turn_off: "OFF",
  toggle: "TOGGLE",
  open_cover: "OPEN",
  close_cover: "CLOSE",
  stop_cover: "STOP",
  open_valve: "OPEN",
  close_valve: "CLOSE",
  lock: "LOCK",
  unlock: "UNLOCK",
  set_temperature: "SETPOINT",
  set_cover_position: "POSITION",
  set_value: "VALUE",
};

function _serviceLabel(slot) {
  if (!slot) return "";
  const base = SERVICE_LABELS[slot.service] || slot.service.toUpperCase();
  const d = slot.data || {};
  const detail =
    d.brightness_pct !== undefined
      ? ` ${d.brightness_pct}%`
      : d.temperature !== undefined
      ? ` ${d.temperature}°`
      : d.position !== undefined
      ? ` ${d.position}%`
      : "";
  return base + detail;
}

function _remainingText(expiresAt) {
  if (!expiresAt) return "";
  const ms = new Date(expiresAt).getTime() - Date.now();
  if (ms <= 0) return "expiring";
  const s = Math.floor(ms / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h > 0) return `${h}h ${m}m left`;
  if (m > 0) return `${m}m left`;
  return `${s}s left`;
}

class PriorityRow extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    // Default, always. Opening a dialog must never arm an override on its own -
    // the row is inert until a level is deliberately picked.
    this._priority = 5;
    this._ttl = 0;
    this._array = null;
    this._built = false;
  }

  connectedCallback() {
    this._build();
    this._fetch();
    // Countdowns tick locally off expires_at; no polling needed for them.
    if (!this._tick) {
      this._tick = window.setInterval(() => this._paintSlots(), 1000);
    }
  }

  disconnectedCallback() {
    if (this._tick) {
      window.clearInterval(this._tick);
      this._tick = null;
    }
    // A level chosen inside a dialog applies to what you do while looking at
    // it. Leaving it armed would mean a toggle elsewhere later silently
    // carried Manual Emergency.
    const id = this._entityId();
    if (id) SELECTIONS.delete(id);
  }

  set hass(hass) {
    const prev = this._hass;
    this._hass = hass;
    // A new hass object arrives on every state change, so re-wrap each time
    // rather than assuming one wrap lasts forever.
    _wrapCallService(hass);
    this._build();
    // Slots 1-4 changing is announced by the overrides sensor; re-read then.
    const sensor = hass && hass.states["sensor.active_overrides"];
    const prevSensor = prev && prev.states["sensor.active_overrides"];
    if (sensor !== prevSensor) this._fetch();
    this._paintStatus();
  }
  get hass() {
    return this._hass;
  }

  set stateObj(stateObj) {
    const changed =
      !this._stateObj ||
      !stateObj ||
      this._stateObj.entity_id !== stateObj.entity_id;
    this._stateObj = stateObj;
    if (changed) {
      this._array = null;
      this._built = false;
      this.shadowRoot.innerHTML = "";
      this._build();
      this._fetch();
    }
    this._paintStatus();
  }
  get stateObj() {
    return this._stateObj;
  }

  _entityId() {
    return this._stateObj && this._stateObj.entity_id;
  }

  _select(priority, ttl) {
    const id = this._entityId();
    if (!id) return;
    if (priority >= 5) SELECTIONS.delete(id);
    else SELECTIONS.set(id, { priority, ttl });
  }

  _release() {
    const id = this._entityId();
    if (!id) return;
    this._hass.callService("priority", "relinquish_all", { entity_id: id });
    // Optimistic: the sensor change will refresh this properly a moment later.
    window.setTimeout(() => this._fetch(), 400);
  }

  async _fetch() {
    const id = this._entityId();
    if (!id || !this._hass || !this._hass.connection) return;
    const domain = id.split(".")[0];
    if (!ARBITRATED_DOMAINS.has(domain)) return;
    try {
      const res = await this._hass.connection.sendMessagePromise({
        type: "call_service",
        domain: "priority",
        service: "get",
        service_data: { entity_id: id },
        return_response: true,
      });
      // The websocket wrapper calls it `response`, the REST one
      // `service_response`. Tolerate both rather than betting on one.
      const payload =
        (res && (res.response || res.service_response)) || res || {};
      const arrays = payload.arrays;
      this._array = (arrays && arrays[id]) || null;
      this._paintSlots();
    } catch (err) {
      // A dashboard must not break because the array could not be read.
      console.debug("priority: could not read array", err);
    }
  }

  /* The DOM is built exactly once. Rebuilding it on every hass update - which
   * arrives on every state change in the house - tore the <select> out from
   * under the pointer and made the dropdowns impossible to use. Everything
   * after this only rewrites text. */
  _build() {
    if (this._built || !this._stateObj) return;
    const domain = (this._entityId() || "").split(".")[0];
    if (!ARBITRATED_DOMAINS.has(domain)) {
      this.shadowRoot.innerHTML = "";
      return;
    }

    const prio = [1, 2, 3, 4, 5]
      .map(
        (p) =>
          `<option value="${p}"${p === this._priority ? " selected" : ""}>${
            PRIORITY_LABELS[p]
          }</option>`
      )
      .join("");
    const ttl = TTL_PRESETS.map(
      (t) =>
        `<option value="${t.value}"${
          t.value === this._ttl ? " selected" : ""
        }>${t.label}</option>`
    ).join("");

    this.shadowRoot.innerHTML = `
      <style>${ROW_STYLE}</style>
      <div class="wrap">
        <select id="p" title="Priority level for commands issued here">${prio}</select>
        <select id="t" title="How long the command holds">${ttl}</select>
        <span id="rel-wrap"></span>
      </div>
      <div class="hint" id="hint"></div>
      <div class="slots" id="slots"></div>`;

    const $ = (s) => this.shadowRoot.getElementById(s);
    $("p").onchange = (e) => {
      this._priority = Number(e.target.value);
      this._select(this._priority, this._ttl);
      this._paintStatus();
    };
    $("t").onchange = (e) => {
      this._ttl = Number(e.target.value);
      this._select(this._priority, this._ttl);
      this._paintStatus();
    };

    this._built = true;
    this._paintStatus();
  }

  _paintStatus() {
    if (!this._built) return;
    const $ = (s) => this.shadowRoot.getElementById(s);
    const armed = this._priority < 5;

    const t = $("t");
    if (t) t.disabled = !armed;

    const hint = $("hint");
    if (hint) {
      hint.textContent = armed
        ? `Controls above will command at ${PRIORITY_LABELS[this._priority]}${
            this._ttl > 0 ? "" : ", until released"
          }.`
        : "Normal behaviour. The last command wins.";
    }

    const relWrap = $("rel-wrap");
    if (relWrap) {
      const held =
        this._array &&
        this._array.effective_priority !== null &&
        this._array.effective_priority < 5;
      const wanted = held ? "yes" : "no";
      if (relWrap.dataset.held !== wanted) {
        relWrap.dataset.held = wanted;
        // "Release" was a lie - it calls relinquish_all and clears every level
        // at once, which matters now that each level can be released on its own.
        relWrap.innerHTML = held
          ? `<button id="rel" title="Clear every level above Default">Release all</button>`
          : "";
        const rel = $("rel");
        if (rel) rel.onclick = () => this._release();
      }
    }

    this._paintSlots();
  }

  _releaseOne(priority) {
    const id = this._entityId();
    if (!id) return;
    this._hass.callService("priority", "relinquish", {
      entity_id: id,
      priority: priority,
    });
    window.setTimeout(() => this._fetch(), 400);
  }

  /* A signature of what the tree currently contains. The countdown ticks once a
   * second, and rebuilding the list that often would put a button under the
   * pointer that is destroyed a moment later - the same class of bug that made
   * the dropdowns unusable. So the list is only rebuilt when its *contents*
   * change; otherwise the ticker just rewrites the remaining-time text. */
  _slotsKey() {
    const slots = (this._array && this._array.slots) || {};
    const winner = this._array && this._array.effective_priority;
    return (
      [1, 2, 3, 4, 5]
        .map((p) => {
          const s = slots[String(p)];
          return s
            ? `${p}:${s.service}:${JSON.stringify(s.data || {})}:${
                s.expires_at || ""
              }`
            : "";
        })
        .join("|") + "#" + winner
    );
  }

  _tickCountdowns() {
    const el = this.shadowRoot.getElementById("slots");
    if (!el || !el.querySelectorAll) return;
    el.querySelectorAll("[data-exp]").forEach((node) => {
      const exp = node.getAttribute("data-exp");
      if (exp) node.textContent = _remainingText(exp);
    });
  }

  /* The whole tree, not just the winner: seeing that Manual Emergency is
   * holding ON for another 24 minutes while Automatic wants OFF underneath is
   * the entire point of an array rather than a flag. */
  _paintSlots() {
    if (!this._built) return;
    const el = this.shadowRoot.getElementById("slots");
    if (!el) return;

    const key = this._slotsKey();
    if (key === this._slotsRendered) {
      this._tickCountdowns();
      return;
    }
    this._slotsRendered = key;

    const slots = (this._array && this._array.slots) || {};
    const winner = this._array && this._array.effective_priority;
    const rows = [];
    for (let p = 1; p <= 5; p++) {
      const slot = slots[String(p)];
      if (!slot) continue;
      rows.push(
        `<div class="slot${p === winner ? " win" : ""}">
           <span class="lvl" style="color:${PRIORITY_COLORS[p]}">${
          PRIORITY_LABELS[p]
        }</span>
           <span class="act">${_serviceLabel(slot)}</span>
           <span class="rem"${
             slot.expires_at ? ` data-exp="${slot.expires_at}"` : ""
           }>${_remainingText(slot.expires_at)}</span>
           ${
             // Default is not an override - there is nothing underneath for it
             // to fall back to, so releasing it would change nothing and the
             // button would be a lie.
             p < 5
               ? `<button class="rel-one" data-rel-p="${p}" title="Release ${PRIORITY_LABELS[p]}">Release</button>`
               : `<span class="rel-spacer"></span>`
           }
         </div>`
      );
    }

    el.innerHTML = rows.length
      ? rows.join("")
      : `<div class="slot none">No commands recorded yet.</div>`;

    if (el.querySelectorAll) {
      el.querySelectorAll("[data-rel-p]").forEach((btn) => {
        btn.onclick = () =>
          this._releaseOne(Number(btn.getAttribute("data-rel-p")));
      });
    }
  }
}

customElements.define("priority-row", PriorityRow);


/* ===================================================================
 * Tile-card feature
 *
 * `window.customCardFeatures` is a supported extension point, so this renders
 * *inside the built-in tile card* rather than in a card of ours, and it should
 * survive frontend updates. One feature covers every arbitrated domain.
 *
 *   type: tile
 *   entity: switch.pump
 *   features:
 *     - type: custom:priority-feature
 * =================================================================== */

class PriorityTileFeature extends HTMLElement {
  static getStubConfig() {
    return { type: "custom:priority-feature" };
  }

  static isSupported(stateObj) {
    return (
      !!stateObj && ARBITRATED_DOMAINS.has(stateObj.entity_id.split(".")[0])
    );
  }

  setConfig(config) {
    this._config = config || {};
  }

  set hass(hass) {
    this._hass = hass;
    this._sync();
  }

  set stateObj(stateObj) {
    this._stateObj = stateObj;
    this._sync();
  }

  _sync() {
    if (!this._hass || !this._stateObj) return;
    if (!PriorityTileFeature.isSupported(this._stateObj)) {
      this.innerHTML = "";
      return;
    }
    if (!this._row) {
      this._row = document.createElement("priority-row");
      this.appendChild(this._row);
    }
    this._row.hass = this._hass;
    this._row.stateObj = this._stateObj;
  }
}

customElements.define("priority-feature", PriorityTileFeature);

window.customCardFeatures = window.customCardFeatures || [];
window.customCardFeatures.push({
  type: "priority-feature",
  name: "Priority",
  supported: PriorityTileFeature.isSupported,
  configurable: false,
});


/* ===================================================================
 * More-info injection
 *
 * The part with no supported API behind it.
 *
 * `ha-more-info-info` is the shared container that wraps every per-domain body
 * (`more-info-light`, `more-info-switch`, ...), so patching it once covers all
 * domains rather than needing a hook per domain.
 *
 * This reaches into compiled frontend internals and may stop working on any
 * Home Assistant update. It is therefore written to fail closed: every step is
 * guarded, and anything unexpected leaves the dialog exactly as it was rather
 * than half-rendered or broken. The worst realistic outcome is that the
 * priority row silently stops appearing - the tile-card feature above is the
 * supported fallback for exactly that day.
 * =================================================================== */

function _injectPriorityRow(host) {
  if (!host || !host.shadowRoot) return;
  const hass = host.hass;
  const stateObj =
    host.stateObj ||
    (hass && host.entityId ? hass.states[host.entityId] : undefined);
  if (!hass || !stateObj || !stateObj.entity_id) return;

  const domain = stateObj.entity_id.split(".")[0];
  let row = host.shadowRoot.querySelector("priority-row[data-priority-row]");

  if (!ARBITRATED_DOMAINS.has(domain)) {
    if (row) row.remove();
    return;
  }

  if (!row) {
    row = document.createElement("priority-row");
    row.setAttribute("data-priority-row", "");
    row.style.marginTop = "8px";
    host.shadowRoot.appendChild(row);
  }
  row.hass = hass;
  row.stateObj = stateObj;
}

function _patchMoreInfo() {
  if (!window.customElements || !customElements.whenDefined) return;
  // ONE tag only. `ha-more-info-info` and `more-info-content` are nested, so
  // patching both injected the row twice - once under the entity's controls
  // and once again at the very bottom of the dialog. `more-info-content` is
  // the inner host that swaps in the per-domain body, so appending to its
  // shadow root puts the row directly beneath the controls it modifies, which
  // is where it belongs.
  ["more-info-content"].forEach((tag) => {
    customElements
      .whenDefined(tag)
      .then(() => {
        const Cls = customElements.get(tag);
        if (!Cls || Cls.__priorityPatched) return;
        Cls.__priorityPatched = true;

        const proto = Cls.prototype;
        const originalUpdated = proto.updated;
        proto.updated = function (changed) {
          if (originalUpdated) originalUpdated.call(this, changed);
          try {
            _injectPriorityRow(this);
          } catch (err) {
            // Never let a UI nicety break the dialog.
            console.debug("priority: more-info injection skipped", err);
          }
        };
      })
      .catch(() => {});
  });
}

_patchMoreInfo();

// Exposed so the node test harness can exercise the injection and the patch
// directly. Nothing in the UI reads these.
window.__priorityInternals = {
  injectPriorityRow: _injectPriorityRow,
  patchMoreInfo: _patchMoreInfo,
  wrapCallService: _wrapCallService,
  selections: SELECTIONS,
  arbitratedDomains: ARBITRATED_DOMAINS,
  isArbitrated: _isArbitrated,
};

console.info(
  "%c PRIORITY-UI ",
  "background:#039be5;color:#fff",
  "cards + tile feature + more-info row loaded"
);
