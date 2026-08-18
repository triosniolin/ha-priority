/* End-to-end card tests in a real browser.
 *
 * The node harness in card_test.js stubs the DOM, so it happily passes code
 * that cannot work: it has no layout, no CSS, no real event dispatch, and no
 * stacking contexts. Every visual and interaction bug in this project has
 * escaped it - a native <select> that collapsed on Android, an ha-select that
 * registered no items, a picker that would not open on a click.
 *
 * This runs the actual card in headless Chromium and drives it with real
 * clicks. It cannot tell us how something looks, but it can tell us whether a
 * click opens a menu and whether that menu is on screen.
 *
 *   node tests/e2e_test.js
 */

const path = require("path");
const { chromium } = require("playwright");

const CARD = path.join(
  __dirname,
  "..",
  "custom_components",
  "priority",
  "frontend",
  "priority-card.js"
);

let fails = 0;
const ok = (cond, msg) => {
  if (!cond) {
    console.log("  FAIL:", msg);
    fails++;
  } else console.log("  ok:", msg);
};

// Enough of hass for the row to build, fetch its array, and issue commands.
const HASS_STUB = `{
  states: {
    "switch.pump": { entity_id: "switch.pump", state: "off",
                     attributes: { friendly_name: "Pump" } },
    "sensor.active_overrides": { state: "0", attributes: { overrides: {} } },
  },
  callService: (d, s, data) => { window.__calls.push({ d, s, data }); },
  connection: {
    sendMessagePromise: async () => ({
      response: { arrays: { "switch.pump": {
        effective_priority: 3,
        slots: { "3": { service: "turn_on", data: {}, expires_at: null } },
      } } },
    }),
  },
}`;

async function makeRow(page, { wrapperStyle = "" } = {}) {
  await page.setContent(`<!doctype html><html><body style="margin:0">
    <div id="host" style="${wrapperStyle}"></div>
  </body></html>`);
  await page.addScriptTag({ path: CARD });
  await page.evaluate(`(async () => {
    window.__calls = [];
    const row = document.createElement("priority-row");
    document.getElementById("host").appendChild(row);
    row.hass = ${HASS_STUB};
    row.stateObj = { entity_id: "switch.pump" };
    window.__row = row;
    await new Promise((r) => setTimeout(r, 50));
  })()`);
}

// Playwright's CSS engine pierces open shadow roots on its own, so the row's
// internals address exactly like ordinary elements.
const pick = (id) => `#${id}`;

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));

  console.log("-- picker opens on a real click --");
  await makeRow(page);
  ok(!errors.length, "card loads without throwing: " + (errors[0] || "clean"));

  ok(
    (await page.textContent(pick("p-label"))).trim() === "Default",
    "closed picker shows the level name"
  );
  ok(!(await page.isVisible(pick("p-menu"))), "menu starts closed");

  await page.click(pick("p"));
  ok(await page.isVisible(pick("p-menu")), "clicking the button opens the menu");

  // Open is not enough - it has to be somewhere a person can reach.
  const box = await page.locator(pick("p-menu")).boundingBox();
  const vp = page.viewportSize();
  ok(
    box && box.width > 0 && box.height > 0,
    `menu has a real size: ${JSON.stringify(box)}`
  );
  ok(
    box && box.x >= 0 && box.y >= 0 && box.x < vp.width && box.y < vp.height,
    `menu is inside the viewport: ${JSON.stringify(box)}`
  );

  const optCount = await page.locator("#p-menu .opt").count();
  ok(optCount === 5, `every level is an option, got ${optCount}`);

  console.log("\n-- choosing a level --");
  await page.click('.opt[data-v="1"]');
  ok(
    (await page.textContent(pick("p-label"))).trim() === "Manual Emergency",
    "choosing updates the closed picker"
  );
  ok(!(await page.isVisible(pick("p-menu"))), "and closes the menu");
  ok(
    (await page.evaluate("window.__row._priority")) === 1,
    "and commits the level"
  );

  console.log("\n-- inside a transformed ancestor --");
  // The more-info dialog animates on a transform, and a transformed ancestor
  // makes position:fixed resolve against that ancestor instead of the viewport.
  // A menu that works on a bare page can vanish inside the dialog.
  await makeRow(page, { wrapperStyle: "transform: translateZ(0); width: 420px;" });
  await page.click(pick("p"));
  ok(
    await page.isVisible(pick("p-menu")),
    "menu still opens inside a transformed ancestor"
  );
  const tbox = await page.locator(pick("p-menu")).boundingBox();
  ok(
    tbox && tbox.width > 0 && tbox.height > 0,
    `menu still has a real size there: ${JSON.stringify(tbox)}`
  );

  console.log("\n-- inside a scrolling dialog body --");
  // Closer to the real more-info dialog: a fixed-height scroll container, a
  // transform on an ancestor, and enough content above the row that it has been
  // scrolled to. A transformed ancestor makes position:fixed resolve against
  // that ancestor, and the ancestor's own overflow then clips it.
  await page.setContent(`<!doctype html><html><body style="margin:0">
    <div style="transform: translateZ(0);">
      <div id="dialog" style="height: 400px; overflow-y: auto; width: 420px;
                              border: 1px solid #ccc;">
        <div style="height: 600px;">filler above the row</div>
        <div id="host"></div>
        <div style="height: 600px;">filler below the row</div>
      </div>
    </div>
  </body></html>`);
  await page.addScriptTag({ path: CARD });
  await page.evaluate(`(async () => {
    window.__calls = [];
    const row = document.createElement("priority-row");
    document.getElementById("host").appendChild(row);
    row.hass = ${HASS_STUB};
    row.stateObj = { entity_id: "switch.pump" };
    window.__row = row;
    document.getElementById("dialog").scrollTop = 560;
    await new Promise((r) => setTimeout(r, 50));
  })()`);

  await page.click(pick("p"));
  const dbox = await page.locator(pick("p-menu")).boundingBox();
  ok(
    await page.isVisible(pick("p-menu")),
    "menu opens inside a scrolling dialog body"
  );
  ok(
    dbox && dbox.width > 0 && dbox.height > 0,
    `menu is not collapsed there: ${JSON.stringify(dbox)}`
  );
  // The real question: can a person actually click an option?
  const reachable = await page.evaluate(() => {
    const row = window.__row;
    const opt = row.shadowRoot.querySelector('.opt[data-v="1"]');
    const r = opt.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return "option has no size";
    const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    if (!hit) return "nothing at the option's centre - it is off screen";
    // Walk down through shadow roots to see what really takes the click.
    let top = hit;
    while (top && top.shadowRoot) {
      const inner = top.shadowRoot.elementFromPoint(
        r.left + r.width / 2,
        r.top + r.height / 2
      );
      if (!inner || inner === top) break;
      top = inner;
    }
    return top === opt ? "ok" : "covered by <" + top.tagName.toLowerCase() + ">";
  });
  ok(reachable === "ok", `an option is actually clickable: ${reachable}`);

  console.log("\n-- under constant state churn --");
  // A real house pushes a new hass object on every state change, and the row
  // repaints on each one plus once a second for the countdowns. If any of that
  // touches the picker, a click never completes: mousedown and mouseup have to
  // land on the same element.
  await makeRow(page);
  await page.evaluate(`(() => {
    window.__churn = setInterval(() => {
      window.__row.hass = ${HASS_STUB};
    }, 60);
  })()`);
  await page.click(pick("p"));
  ok(
    await page.isVisible(pick("p-menu")),
    "the picker still opens while state updates are pouring in"
  );
  await page.click('.opt[data-v="2"]');
  ok(
    (await page.evaluate("window.__row._priority")) === 2,
    "and an option can still be chosen"
  );
  await page.evaluate("clearInterval(window.__churn)");

  console.log("\n-- an unrelated scroll must not dismiss it --");
  // The menu closes on scroll so it never floats away from the button it was
  // positioned against. But a capture-phase scroll listener on window fires for
  // a scroll of anything on the page, and a real mouse click can scroll the
  // focused button into view - so the menu shut again the instant it opened.
  await page.setContent(`<!doctype html><html><body style="margin:0">
    <div id="elsewhere" style="height:100px; overflow-y:auto; width:200px;">
      <div style="height:900px">an unrelated scrolling list</div>
    </div>
    <div id="host"></div>
  </body></html>`);
  await page.addScriptTag({ path: CARD });
  await page.evaluate(`(async () => {
    window.__calls = [];
    const row = document.createElement("priority-row");
    document.getElementById("host").appendChild(row);
    row.hass = ${HASS_STUB};
    row.stateObj = { entity_id: "switch.pump" };
    window.__row = row;
    await new Promise((r) => setTimeout(r, 50));
  })()`);
  await page.click(pick("p"));
  ok(await page.isVisible(pick("p-menu")), "menu opens");
  await page.evaluate(`(async () => {
    document.getElementById("elsewhere").scrollTop = 200;
    await new Promise((r) => setTimeout(r, 50));
  })()`);
  ok(
    await page.isVisible(pick("p-menu")),
    "menu survives a scroll of something else entirely"
  );

  console.log("\n-- floating dialog, menu flipped above it --");
  // Reported from the field: the picker works when the viewport is narrow
  // enough that HA renders more-info as a full-screen sheet, and fails when it
  // is wide enough to float the dialog over the dashboard. A floating dialog is a bounded,
  // scrolling box, and MDC animates it on a transform - which makes our
  // position:fixed menu resolve against that box and get clipped by it. The
  // menu still reports a perfectly healthy rect while being invisible.
  await page.setViewportSize({ width: 1280, height: 500 });
  await page.setContent(`<!doctype html><html><body style="margin:0">
    <div style="transform: translateZ(0);">
      <div id="dialog" style="position:fixed; left:100px; top:300px; width:420px;
                              height:180px; overflow:auto; background:#fff;
                              border:1px solid #ccc;">
        <div id="host"></div>
        <div style="height:600px">filler below</div>
      </div>
    </div>
  </body></html>`);
  await page.addScriptTag({ path: CARD });
  await page.evaluate(`(async () => {
    window.__calls = [];
    const row = document.createElement("priority-row");
    document.getElementById("host").appendChild(row);
    row.hass = ${HASS_STUB};
    row.stateObj = { entity_id: "switch.pump" };
    window.__row = row;
    await new Promise((r) => setTimeout(r, 50));
  })()`);
  await page.click(pick("p"));
  const fbox = await page.locator(pick("p-menu")).boundingBox();
  ok(
    await page.isVisible(pick("p-menu")),
    `menu reports itself open and laid out: ${JSON.stringify(fbox)}`
  );
  // The honest test: is a pixel of it actually on screen and hittable?
  const hittable = await page.evaluate(() => {
    const opt = window.__row.shadowRoot.querySelector('.opt[data-v="1"]');
    const r = opt.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return "option has no size";
    const x = r.left + r.width / 2, y = r.top + r.height / 2;
    if (x < 0 || y < 0 || x > innerWidth || y > innerHeight) return "off screen";
    let top = document.elementFromPoint(x, y);
    if (!top) return "nothing there";
    while (top && top.shadowRoot) {
      const inner = top.shadowRoot.elementFromPoint(x, y);
      if (!inner || inner === top) break;
      top = inner;
    }
    return top === opt ? "ok" : "clipped away - hit <" + top.tagName.toLowerCase() + ">";
  });
  ok(hittable === "ok", `an option is really on screen and clickable: ${hittable}`);
  await page.setViewportSize({ width: 1280, height: 900 });

  console.log("\n-- the button toggles the list --");
  await makeRow(page);
  await page.click(pick("p"));
  ok(await page.isVisible(pick("p-menu")), "opens");
  await page.click(pick("p"));
  ok(!(await page.isVisible(pick("p-menu"))), "and a second click closes it");

  console.log("\n-- control card pickers --");
  // The control card renders into light DOM inside a masonry column, so it has
  // its own set of ways to go wrong, and its own inline pickers.
  await page.setContent(`<!doctype html><html><body style="margin:0">
    <div id="host" style="width: 420px;"></div>
  </body></html>`);
  await page.addScriptTag({ path: CARD });
  await page.evaluate(`(async () => {
    window.__calls = [];
    const card = document.createElement("priority-control-card");
    card.setConfig({ entities: ["switch.pump"], default_priority: 5, default_ttl: 0 });
    document.getElementById("host").appendChild(card);
    card.hass = ${HASS_STUB};
    window.__card = card;
    await new Promise((r) => setTimeout(r, 50));
  })()`);

  ok(
    !(await page.isVisible('[data-menu="p"]')),
    "control card list starts closed"
  );
  await page.click('[data-pick="p"]');
  ok(await page.isVisible('[data-menu="p"]'), "clicking opens it");

  const cbox = await page.locator('[data-menu="p"]').boundingBox();
  ok(
    cbox && cbox.width > 0 && cbox.height > 0,
    `control card list is laid out: ${JSON.stringify(cbox)}`
  );
  const creach = await page.evaluate(() => {
    const opt = document.querySelector('[data-menu="p"] .opt[data-v="1"]');
    const r = opt.getBoundingClientRect();
    const x = r.left + r.width / 2, y = r.top + r.height / 2;
    if (x < 0 || y < 0 || x > innerWidth || y > innerHeight) return "off screen";
    let top = document.elementFromPoint(x, y);
    while (top && top.shadowRoot) {
      const inner = top.shadowRoot.elementFromPoint(x, y);
      if (!inner || inner === top) break;
      top = inner;
    }
    return top === opt ? "ok" : "covered by <" + top.tagName.toLowerCase() + ">";
  });
  ok(creach === "ok", `an option is on screen and clickable: ${creach}`);

  await page.click('[data-menu="p"] .opt[data-v="1"]');
  ok(
    (await page.evaluate("window.__card._priority")) === 1,
    "choosing commits the level"
  );
  ok(!(await page.isVisible('[data-menu="p"]')), "and closes the list");
  ok(
    (await page.textContent('[data-label="p"]')).trim() === "1 - Manual Emergency",
    "and the button shows it"
  );

  // Opening one must not leave the other open underneath.
  await page.click('[data-pick="p"]');
  await page.click('[data-pick="t"]');
  ok(
    !(await page.isVisible('[data-menu="p"]')) &&
      (await page.isVisible('[data-menu="t"]')),
    "opening one list closes the other"
  );

  // The command has to carry what the pickers say.
  await page.click('[data-menu="t"] .opt[data-v="1800"]');
  await page.click('text=On');
  const call = await page.evaluate("window.__calls[window.__calls.length - 1]");
  ok(
    call && call.d === "switch" && call.s === "turn_on",
    `commands the entity: ${JSON.stringify(call)}`
  );
  ok(call && call.data.priority === 1, "at the chosen level");
  ok(call && call.data.priority_ttl === 1800, "with the chosen lease");

  console.log("\n-- tile card feature, compact --");
  // A tile card can be half a column, about 230px, and the sections grid gives
  // it a fixed height. Two things therefore must hold: the pickers fit on one
  // line, and opening a list does not make the row taller. Getting either wrong
  // paints the row straight through the bottom edge of the tile.
  await page.setContent(`<!doctype html><html><body style="margin:0">
    <div id="tile" style="width: 230px;"></div>
  </body></html>`);
  await page.addScriptTag({ path: CARD });
  await page.evaluate(`(async () => {
    window.__calls = [];
    const row = document.createElement("priority-row");
    row.setAttribute("compact", "");
    document.getElementById("tile").appendChild(row);
    row.hass = ${HASS_STUB};
    row.stateObj = { entity_id: "switch.pump" };
    window.__row = row;
    await new Promise((r) => setTimeout(r, 60));
  })()`);

  const compact = await page.evaluate(() => {
    const row = window.__row;
    const picks = [...row.shadowRoot.querySelectorAll(".pick")];
    const tops = [...new Set(picks.map((c) => Math.round(c.getBoundingClientRect().top)))];
    return { lines: tops.length, height: Math.round(row.getBoundingClientRect().height) };
  });
  ok(compact.lines === 1, `both pickers sit on one line, got ${compact.lines}`);
  // The native-select version measured 120px in this exact container. Staying
  // at or under that is the bar: it is the layout the tile card was sized for.
  ok(
    compact.height <= 120,
    `compact row is no taller than the version it replaced: ${compact.height} <= 120`
  );

  const openedHeight = await page.evaluate(async () => {
    window.__row.shadowRoot.querySelector(".pick").click();
    await new Promise((r) => setTimeout(r, 50));
    return Math.round(window.__row.getBoundingClientRect().height);
  });
  ok(
    openedHeight === compact.height,
    `opening a list does not grow the tile row: ${compact.height} -> ${openedHeight}`
  );
  const treach = await page.evaluate(() => {
    const opt = window.__row.shadowRoot.querySelector('.opt[data-v="1"]');
    const r = opt.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return "option has no size";
    const x = r.left + r.width / 2, y = r.top + r.height / 2;
    let top = document.elementFromPoint(x, y);
    while (top && top.shadowRoot) {
      const inner = top.shadowRoot.elementFromPoint(x, y);
      if (!inner || inner === top) break;
      top = inner;
    }
    return top === opt ? "ok" : "covered by <" + top.tagName.toLowerCase() + ">";
  });
  ok(treach === "ok", `an option is reachable over the tile: ${treach}`);

  console.log("\n-- control card in a sections grid --");
  // In a sections view HA gives each card a computed row span and an ha-card
  // of height:100%. A list that grows the card does not get more
  // room, it just spills out the bottom. So the list overlays instead, and the
  // card's own height must not budge when it opens.
  await page.setContent(`<!doctype html><html><body style="margin:0">
    <div id="cell" style="height: 260px; width: 420px; overflow: visible;">
      <div id="host" style="height: 100%;"></div>
    </div>
    <div id="below" style="height: 40px; background: #eee;">what is underneath</div>
  </body></html>`);
  await page.addScriptTag({ path: CARD });
  await page.evaluate(`(async () => {
    window.__calls = [];
    const card = document.createElement("priority-control-card");
    card.setConfig({ entities: ["switch.pump"], default_priority: 5, default_ttl: 0 });
    document.getElementById("host").appendChild(card);
    card.hass = ${HASS_STUB};
    window.__card = card;
    await new Promise((r) => setTimeout(r, 50));
  })()`);

  const heightBefore = await page.evaluate(
    "document.querySelector('priority-control-card').getBoundingClientRect().height"
  );
  await page.click('[data-pick="t"]');
  const heightAfter = await page.evaluate(
    "document.querySelector('priority-control-card').getBoundingClientRect().height"
  );
  ok(
    Math.abs(heightAfter - heightBefore) < 1,
    `opening the list does not change the card height: ${heightBefore} -> ${heightAfter}`
  );

  const lreach = await page.evaluate(() => {
    const opt = document.querySelector('[data-menu="t"] .opt[data-v="1800"]');
    const r = opt.getBoundingClientRect();
    const x = r.left + r.width / 2, y = r.top + r.height / 2;
    if (r.width === 0 || r.height === 0) return "option has no size";
    if (x < 0 || y < 0 || x > innerWidth || y > innerHeight) return "off screen";
    let top = document.elementFromPoint(x, y);
    while (top && top.shadowRoot) {
      const inner = top.shadowRoot.elementFromPoint(x, y);
      if (!inner || inner === top) break;
      top = inner;
    }
    return top === opt ? "ok" : "clipped or covered - hit <" + top.tagName.toLowerCase() + ">";
  });
  ok(lreach === "ok", `the lease list is reachable over the card: ${lreach}`);

  // It floats over the rows, so it has to go away when you tap elsewhere -
  // including elsewhere inside this same card, which is exactly where the rows
  // it is covering are.
  await page.click("#below");
  ok(
    !(await page.isVisible('[data-menu="t"]')),
    "a click outside the card closes the list"
  );
  await page.click('[data-pick="t"]');
  ok(await page.isVisible('[data-menu="t"]'), "reopens");
  await page.click(".note");
  ok(
    !(await page.isVisible('[data-menu="t"]')),
    "a click elsewhere inside the card closes it too"
  );

  await browser.close();
  console.log(fails ? `\n${fails} FAILURES` : "\nall e2e checks passed");
  process.exit(fails ? 1 : 0);
})();
