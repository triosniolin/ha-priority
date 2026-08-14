/* Minimal DOM stub: enough to instantiate the cards and drive their render
 * paths, so the logic is exercised even though we cannot click the real thing. */

const registry = {};
const made = [];

class El {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this._html = "";
    this.style = { textContent: "" };
    this.className = "";
  }
  set innerHTML(v) {
    this._html = String(v);
  }
  get innerHTML() {
    return this._html;
  }
  appendChild(c) {
    this.children.push(c);
    return c;
  }
  querySelector(sel) {
    return this._find(sel)[0] || null;
  }
  querySelectorAll(sel) {
    return this._find(sel);
  }
  _find(sel) {
    // Only needs to find the elements the cards actually wire handlers onto.
    const attr = /\[([a-z-]+)(?:="([^"]*)")?\]/.exec(sel);
    const out = [];
    const re = attr
      ? new RegExp(`${attr[1]}="([^"]*)"`, "g")
      : new RegExp(`id="${(sel || "").replace("#", "")}"`, "g");
    let m;
    while ((m = re.exec(this._html))) {
      const stub = new El("stub");
      stub._attrs = { [attr ? attr[1] : "id"]: m[1] };
      stub.getAttribute = (k) => {
        const mm = new RegExp(`${k}="([^"]*)"`).exec(this._html);
        return stub._attrs[k] !== undefined ? stub._attrs[k] : mm && mm[1];
      };
      out.push(stub);
    }
    return out;
  }
  dispatchEvent() {}
  attachShadow() {
    this.shadowRoot = new El("shadow-root");
    this.shadowRoot._byId = {};
    this.shadowRoot.getElementById = (id) => {
      if (!new RegExp(`id="${id}"`).test(this.shadowRoot.innerHTML)) return null;
      if (!this.shadowRoot._byId[id]) {
        const e = new El("stub#" + id);
        e.dataset = {};
        e.textContent = "";
        e.disabled = false;
        this.shadowRoot._byId[id] = e;
      }
      return this.shadowRoot._byId[id];
    };
    return this.shadowRoot;
  }
  remove() {
    this._removed = true;
  }
  setAttribute(k, v) {
    (this._attrs = this._attrs || {})[k] = v;
  }
}

global.HTMLElement = El;
global.CustomEvent = class {
  constructor(t, o) {
    Object.assign(this, o, { type: t });
  }
};
global.document = { createElement: (t) => new El(t) };
global.window = {
  customCards: [],
  setInterval: () => 1,
  clearInterval: () => {},
  setTimeout: (fn, ms) => setTimeout(fn, ms),
};
global.customElements = {
  define: (n, c) => {
    registry[n] = c;
    made.push(n);
  },
};
// The more-info patch guards on `window.customElements`, which in a browser is
// the same object as the bare global. Mirror that here or the patch no-ops.
global.window.customElements = global.customElements;
global.console.info = () => {};

require(require("path").join(__dirname, "..", "custom_components", "priority", "frontend", "priority-card.js"));

const calls = [];
const hass = {
  states: {
    "sensor.active_overrides": {
      state: "1",
      attributes: {
        overrides: {
          "light.living_room": {
            priority: 1,
            priority_name: "Manual Emergency",
            service: "light.turn_on",
            friendly_name: "Living room light",
            written_by: "user:abc",
            expires_at: new Date(Date.now() + 95000 + 2000).toISOString(),
          },
        },
      },
    },
    "light.living_room": {
      state: "on",
      attributes: { friendly_name: "Living room light" },
    },
    "cover.garage": { state: "closed", attributes: { friendly_name: "Garage" } },
    "lock.front": { state: "locked", attributes: { friendly_name: "Front" } },
  },
  callService: (d, s, data) => calls.push({ d, s, data }),
};

let fails = 0;
const ok = (cond, msg) => {
  if (!cond) {
    console.log("  FAIL:", msg);
    fails++;
  } else console.log("  ok:", msg);
};

console.log("registered:", made.join(", "));
ok(made.includes("priority-overrides-card"), "overrides card registered");
ok(made.includes("priority-control-card"), "control card registered");
ok(window.customCards.length === 2, "both cards in the picker");

console.log("\n-- overrides card --");
const oc = new registry["priority-overrides-card"]();
oc.setConfig({});
oc.hass = hass;
const ocHtml = oc._body.innerHTML;
ok(ocHtml.includes("Living room light"), "renders the entity name");
ok(ocHtml.includes("Manual Emergency"), "renders the level");
ok(/releases in 1m/.test(ocHtml), "counts the lease down: " + /releases in [^<]*/.exec(ocHtml)[0]);
oc._relinquish("light.living_room", 1);
ok(
  calls.length === 1 && calls[0].s === "relinquish" && calls[0].data.priority === 1,
  "release calls priority.relinquish with the right level"
);

console.log("\n-- overrides card: more than one level on one entity --");
// The sensor used to publish only the winning slot, so an entity held at both
// Manual and Automatic looked like it was held once and the level underneath
// had silently vanished.
const multiHass = {
  states: {
    "sensor.active_overrides": {
      state: "1",
      attributes: {
        overrides: {
          "switch.pump": {
            priority: 3,
            priority_name: "Manual",
            service: "switch.turn_on",
            friendly_name: "Pump",
            written_by: "user:Ada",
            expires_at: null,
            levels: {
              3: {
                priority_name: "Manual",
                service: "switch.turn_on",
                written_by: "user:Ada",
                expires_at: null,
              },
              4: {
                priority_name: "Automatic",
                service: "switch.turn_on",
                written_by: "automation.dusk",
                expires_at: null,
              },
            },
          },
        },
      },
    },
  },
  callService: (d, s, data) => calls.push({ d, s, data }),
};
const ocMulti = new registry["priority-overrides-card"]();
ocMulti.setConfig({});
ocMulti.hass = multiHass;
const multiHtml = ocMulti._body.innerHTML;
ok(
  (multiHtml.match(/class="row/g) || []).length === 2,
  "renders one row per held level"
);
ok(
  multiHtml.includes("Manual") && multiHtml.includes("Automatic"),
  "shows both levels, not just the winner"
);
ok(
  (multiHtml.match(/Pump/g) || []).length === 1,
  "entity name appears once, as the group heading"
);
ok(multiHtml.includes(" · driving"), "marks which level is in control");
ok(
  multiHtml.includes("automation.dusk"),
  "attributes the level the automation set"
);
ok(
  /data-release="switch.pump" data-priority="4"/.test(multiHtml),
  "the level underneath has its own Release button"
);

console.log("\n-- control card --");
const cc = new registry["priority-control-card"]();
cc.setConfig({ entities: ["light.living_room", "cover.garage", "lock.front"] });
cc.hass = hass;
ok(cc._controls.innerHTML.includes("1 - Manual Emergency"), "priority dropdown populated");
ok(cc._controls.innerHTML.includes("30 minutes"), "lease presets populated");
ok(cc._rows.innerHTML.includes("Manual Emergency"), "shows the held badge");
ok(cc._rows.innerHTML.includes(">Open<"), "cover uses Open, not On");
ok(cc._rows.innerHTML.includes(">Unlock<"), "lock uses Unlock, not Off");

calls.length = 0;
cc._priority = 1;
cc._ttl = 1800;
cc._command("light.living_room", "on");
ok(
  calls[0].d === "light" && calls[0].s === "turn_on",
  "light on -> light.turn_on"
);
ok(calls[0].data.priority === 1, "sends the chosen priority");
ok(calls[0].data.priority_ttl === 1800, "sends the chosen lease");

calls.length = 0;
cc._command("cover.garage", "off");
ok(calls[0].s === "close_cover", "cover off -> close_cover");

calls.length = 0;
cc._command("lock.front", "on");
ok(calls[0].s === "lock", "lock on -> lock");

calls.length = 0;
cc._priority = 5;
cc._ttl = 1800;
cc._command("light.living_room", "on");
ok(
  calls[0].data.priority_ttl === undefined,
  "no lease sent at Default (the integration rejects it)"
);

let threw = false;
try {
  new registry["priority-control-card"]().setConfig({});
} catch (e) {
  threw = true;
}
ok(threw, "config without entities is rejected with a clear error");


console.log("\n-- tile feature --");
const Feat = registry["priority-feature"];
ok(!!Feat, "priority-feature registered");
ok(
  window.customCardFeatures && window.customCardFeatures.length === 1,
  "feature offered to the tile card"
);
ok(
  Feat.isSupported({ entity_id: "switch.pump" }) &&
    Feat.isSupported({ entity_id: "cover.garage" }) &&
    Feat.isSupported({ entity_id: "input_boolean.guest_mode" }) &&
    Feat.isSupported({ entity_id: "light.living_room" }),
  "supported across every arbitrated domain, not just light"
);
ok(
  !Feat.isSupported({ entity_id: "sensor.temperature" }) &&
    !Feat.isSupported({ entity_id: "binary_sensor.door" }),
  "not offered on domains that cannot be commanded"
);
ok(!Feat.isSupported(undefined), "undefined stateObj handled");

console.log("\n-- shared row (pickers are modifiers, not commands) --");
const Row = registry["priority-row"];
ok(!!Row, "priority-row registered");

const mkRow = (entityId, array) => {
  const r = new Row();
  r._hass = hass;
  r._stateObj = { entity_id: entityId };
  r._array = array || null;
  r._build();
  return r;
};

let r = mkRow("switch.pump");
ok(r.shadowRoot.innerHTML.includes("Manual Emergency"), "renders the levels");
ok(
  !/id="on"|id="off"/.test(r.shadowRoot.innerHTML),
  "row offers NO command buttons - the stock controls do the commanding"
);
ok(
  r.shadowRoot.getElementById("hint").textContent.includes("Normal behaviour"),
  "defaults to Default with a plain-English hint"
);
ok(r.shadowRoot.getElementById("t").disabled === true, "lease disabled at Default");

// The bug that made the dropdowns unusable: rebuilding on every hass update.
const before = r.shadowRoot.innerHTML;
r.hass = { ...hass };
r.hass = { ...hass };
r.hass = { ...hass };
ok(
  r.shadowRoot.innerHTML === before,
  "repeated hass updates do NOT rebuild the DOM (this is what collapsed the dropdowns)"
);
ok(r._built === true, "row stays built across hass updates");

r._priority = 1;
r._paintStatus();
ok(
  r.shadowRoot.getElementById("hint").textContent.includes("Manual Emergency"),
  "hint updates by text, not by rebuild"
);
ok(r.shadowRoot.getElementById("t").disabled === false, "lease enabled once armed");

r = mkRow("sensor.temperature");
ok(r.shadowRoot.innerHTML === "", "non-arbitrated domain renders nothing");

console.log("\n-- full priority tree --");
const tree = {
  effective_priority: 1,
  slots: {
    "1": {
      domain: "light",
      service: "turn_on",
      data: {},
      // +30s so the value sits safely inside the 24-minute bucket; an exact
      // boundary floors to 23 as soon as any time elapses, which made this flaky.
      expires_at: new Date(Date.now() + 24 * 60000 + 30000).toISOString(),
    },
    "4": {
      domain: "light",
      service: "turn_off",
      data: {},
      expires_at: new Date(Date.now() + 3600000 + 30000).toISOString(),
    },
    "5": { domain: "light", service: "turn_on", data: {}, expires_at: null },
  },
};
r = mkRow("light.living_room", tree);
r._paintSlots();
const slotsHtml = r.shadowRoot.getElementById("slots").innerHTML;
ok(
  slotsHtml.includes("Manual Emergency") && slotsHtml.includes("Automatic"),
  "levels shown by NAME, not as PRI n"
);
ok(!/PRI \d/.test(slotsHtml), "no bare priority numbers left in the tree");
ok(slotsHtml.includes("Default"), "shows the Default slot too");
ok(/Manual Emergency[\s\S]*?ON/.test(slotsHtml), "Manual Emergency reads ON");
ok(/>Automatic<[\s\S]*?OFF/.test(slotsHtml), "Automatic reads OFF");
ok(slotsHtml.includes("24m left"), "counts down its lease: 24m left");
ok(slotsHtml.includes("1h 0m left"), "second lease counts down too");
ok(/class="slot win"/.test(slotsHtml), "the winning slot is marked");
ok(
  (slotsHtml.match(/class="slot/g) || []).length === 3,
  "empty levels omitted, occupied ones all shown"
);

console.log("\n-- per-level release --");
ok(
  (slotsHtml.match(/data-rel-p=/g) || []).length === 2,
  "a Release button on each override level"
);
ok(
  /data-rel-p="1"/.test(slotsHtml) && /data-rel-p="4"/.test(slotsHtml),
  "buttons carry the level they release"
);
ok(
  !/data-rel-p="5"/.test(slotsHtml),
  "no Release on Default - nothing underneath for it to fall back to"
);

calls.length = 0;
r._releaseOne(4);
ok(
  calls[0].d === "priority" &&
    calls[0].s === "relinquish" &&
    calls[0].data.priority === 4 &&
    calls[0].data.entity_id === "light.living_room",
  "releasing one level calls relinquish for exactly that level"
);
ok(calls[0].s !== "relinquish_all", "and does NOT clear the others");

// The whole-array button must say what it does.
r._array = { effective_priority: 1, slots: tree.slots };
r._paintStatus();
ok(
  r.shadowRoot.getElementById("rel-wrap").innerHTML.includes("Release all"),
  'the array-wide button reads "Release all", since relinquish_all is what it calls'
);
calls.length = 0;
r._release();
ok(calls[0].s === "relinquish_all", "and it really does call relinquish_all");

// The countdown ticks once a second; rebuilding the list that often would put a
// button under the pointer and destroy it - the dropdown bug again.
const htmlBefore = r.shadowRoot.getElementById("slots").innerHTML;
r._paintSlots();
r._paintSlots();
r._paintSlots();
ok(
  r.shadowRoot.getElementById("slots").innerHTML === htmlBefore,
  "repeated ticks do NOT rebuild the slot list (buttons stay clickable)"
);
r._array = {
  effective_priority: 4,
  slots: { "4": tree.slots["4"], "5": tree.slots["5"] },
};
r._paintSlots();
ok(
  r.shadowRoot.getElementById("slots").innerHTML !== htmlBefore,
  "but a real change to the tree does rebuild it"
);

r = mkRow("light.living_room", { effective_priority: null, slots: {} });
r._paintSlots();
ok(
  r.shadowRoot.getElementById("slots").innerHTML.includes("No commands recorded"),
  "empty array says so rather than rendering nothing"
);

r = mkRow("light.living_room", {
  effective_priority: 3,
  slots: {
    "3": {
      domain: "light",
      service: "turn_on",
      data: { brightness_pct: 47 },
      expires_at: null,
    },
  },
});
r._paintSlots();
ok(
  r.shadowRoot.getElementById("slots").innerHTML.includes("ON 47%"),
  "shows the commanded value, not just on/off"
);
ok(
  !r.shadowRoot.getElementById("slots").innerHTML.includes("left"),
  "a slot with no lease shows no countdown"
);

console.log("\n-- command interception --");
const internals = window.__priorityInternals;
const SEL = internals.selections;
SEL.clear();

// Build a hass whose callService we can observe, then wrap it the way the row does.
const mkHass = () => {
  const h = { states: hass.states, callService: (d, s, data, target) => { calls.push({ d, s, data, target }); } };
  internals.wrapCallService(h);
  return h;
};

calls.length = 0;
let h = mkHass();
h.callService("light", "turn_on", { entity_id: "light.living_room", brightness_pct: 47 });
ok(calls[0].data.priority === undefined, "nothing selected -> ordinary call passes through untouched");
ok(calls[0].data.brightness_pct === 47, "payload preserved");

// Now arm a selection the way the picker does.
const armed = mkRow("light.living_room");
armed._priority = 1;
armed._ttl = 1800;
armed._select(1, 1800);
ok(SEL.get("light.living_room").priority === 1, "picker arms the selection");

calls.length = 0;
h = mkHass();
h.callService("light", "turn_on", { entity_id: "light.living_room", brightness_pct: 47 });
ok(calls[0].data.priority === 1, "armed -> the stock brightness command carries the level");
ok(calls[0].data.priority_ttl === 1800, "armed -> and the lease");
ok(calls[0].data.brightness_pct === 47, "armed -> original payload still intact");

calls.length = 0;
h.callService("light", "turn_off", { entity_id: "light.living_room" });
ok(calls[0].data.priority === 1, "applies to any arbitrated service, not just turn_on");

calls.length = 0;
h.callService("light", "turn_on", { entity_id: "light.other" });
ok(calls[0].data.priority === undefined, "only the armed entity is affected");

calls.length = 0;
h.callService("homeassistant", "update_entity", { entity_id: "light.living_room" });
ok(
  calls[0].data.priority === undefined,
  "never injected into a service that would reject it"
);

calls.length = 0;
h.callService("light", "turn_on", {}, { entity_id: ["light.living_room"] });
ok(calls[0].data.priority === 1, "resolves entities from the target block too");

// Back to Default disarms.
armed._select(5, 1800);
ok(!SEL.has("light.living_room"), "Default clears the selection");
calls.length = 0;
h.callService("light", "turn_on", { entity_id: "light.living_room" });
ok(calls[0].data.priority === undefined, "disarmed -> pass-through again");

// Closing the dialog must disarm, or a later toggle elsewhere would carry it.
armed._select(1, 0);
armed.disconnectedCallback();
ok(!SEL.has("light.living_room"), "disconnect clears the selection");

// Double-wrapping must not stack.
const h2 = mkHass();
internals.wrapCallService(h2);
internals.wrapCallService(h2);
calls.length = 0;
h2.callService("light", "turn_on", { entity_id: "light.living_room" });
ok(calls.length === 1, "wrapping is idempotent - one call in, one call out");

console.log("\n-- more-info injection --");
const inject = window.__priorityInternals.injectPriorityRow;
ok(typeof inject === "function", "injection helper exposed for testing");

// It reaches into compiled frontend internals, so the thing that actually
// matters is that it never throws and never half-renders, whatever shape the
// host turns out to be after some future HA update.
const bad = [
  undefined,
  null,
  {},
  { shadowRoot: null },
  { shadowRoot: new El("s") },
  { shadowRoot: new El("s"), hass: null, stateObj: { entity_id: "light.a" } },
  { shadowRoot: new El("s"), hass, stateObj: null },
  { shadowRoot: new El("s"), hass, stateObj: {} },
  { shadowRoot: new El("s"), hass, entityId: "nope.missing" },
];
let injThrew = null;
for (const b of bad) {
  try {
    inject(b);
  } catch (e) {
    injThrew = injThrew || e.message;
  }
}
ok(!injThrew, "injection survives every malformed host: " + (injThrew || "none"));

// A well-formed host must actually get a row.
const goodHost = { shadowRoot: new El("s"), hass, stateObj: hass.states["light.living_room"] };
goodHost.stateObj.entity_id = "light.living_room";
let appended = null;
goodHost.shadowRoot.appendChild = (c) => (appended = c);
goodHost.shadowRoot.querySelector = () => null;
inject(goodHost);
ok(appended && appended.tagName === "priority-row", "injects a row for an arbitrated entity");
ok(
  appended && appended.stateObj && appended.stateObj.entity_id === "light.living_room",
  "row wired to the entity"
);
ok(appended && appended.hass === hass, "row wired to hass");

// A non-arbitrated entity must not get one.
const sensorHost = {
  shadowRoot: new El("s"),
  hass,
  stateObj: { entity_id: "sensor.temperature" },
};
let sensorAppended = false;
sensorHost.shadowRoot.appendChild = () => (sensorAppended = true);
sensorHost.shadowRoot.querySelector = () => null;
inject(sensorHost);
ok(!sensorAppended, "no row on a sensor");

// The prototype patch must chain, not replace, the original updated().
let originalRan = false;
let patchedHost = null;
class FakeMoreInfo {
  updated() {
    originalRan = true;
  }
}
const defined = {};
global.customElements.whenDefined = (t) => Promise.resolve(defined[t]);
global.customElements.get = (t) => defined[t];
defined["ha-more-info-info"] = FakeMoreInfo;
defined["more-info-content"] = FakeMoreInfo;
window.__priorityInternals.patchMoreInfo();
setTimeout(() => {
  const inst = new FakeMoreInfo();
  inst.shadowRoot = new El("s");
  inst.shadowRoot.querySelector = () => null;
  inst.shadowRoot.appendChild = (c) => (patchedHost = c);
  inst.hass = hass;
  inst.stateObj = { entity_id: "switch.pump" };
  inst.updated({});
  ok(originalRan, "patched updated() still calls the original");
  ok(
    patchedHost && patchedHost.tagName === "priority-row",
    "patched updated() injects the row"
  );
  ok(FakeMoreInfo.__priorityPatched === true, "patch is marked, so it cannot double-apply");

  console.log(fails ? `\n${fails} FAILURES` : "\nall card checks passed");
  process.exit(fails ? 1 : 0);
}, 10);
