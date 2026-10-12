// Optional real-browser smoke test. Install Playwright for development only.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
const source = await readFile(new URL("../../custom_components/mikrotik_dhcp_sync/frontend/mikrotik-dhcp-browser-card.js", import.meta.url));
const server = createServer((req, res) => {
  res.setHeader("Content-Type", req.url === "/card.js" ? "text/javascript" : "text/html");
  res.end(req.url === "/card.js" ? source : `<!doctype html><html><head><style>
    body { margin:16px; font-family:Arial,sans-serif; --primary-text-color:#212121; --secondary-text-color:#606060; --card-background-color:#fff; --secondary-background-color:#f4f4f4; --divider-color:#ddd; --primary-color:#03a9f4; }
    ha-card { display:block; border:1px solid var(--divider-color); border-radius:12px; background:var(--card-background-color); }
    mikrotik-dhcp-browser-card { max-width:800px; }
    </style></head><body><script type="module" src="/card.js"></script></body></html>`);
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
try {
  const page = await browser.newPage({ viewport: { width: 1000, height: 900 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.evaluate(async () => {
    await customElements.whenDefined("mikrotik-dhcp-browser-card");
    for (const config of [{ page_size: 0 }, { page_size: "50" }, { title: 1 }, { subnet: "192.168.0.0/33" }]) {
      let rejected = false;
      try { document.createElement("mikrotik-dhcp-browser-card").setConfig(config); } catch { rejected = true; }
      if (!rejected) throw new Error("Invalid configuration accepted");
    }
    window.records = Array.from({ length: 2000 }, (_, n) => ({ mac_address: n.toString(16).padStart(12, "0"), hostname: `Device ${n}`, ip_address: `192.168.${40 + Math.floor(n / 256)}.${n % 256}` }));
    records[0].hostname = '<img src=x onerror="alert(1)">';
    window.subscriptions = 0; window.active = 0; window.callbacks = new Set(); window.listeners = new Map();
    window.connection = {
      connected: true,
      addEventListener: (name, cb) => listeners.set(name, cb),
      removeEventListener: (name) => listeners.delete(name),
      subscribeMessage: async (cb) => {
        subscriptions++; active++; callbacks.add(cb);
        cb({ add: records });
        return async () => { active--; callbacks.delete(cb); };
      },
    };
    window.hass = { connection, user: { is_admin: true } };
    window.card = document.createElement("mikrotik-dhcp-browser-card");
    card.setConfig({ page_size: 25 }); card.hass = hass; document.body.append(card);
  });
  const card = page.locator("mikrotik-dhcp-browser-card").first();
  await card.locator("tbody tr").last().waitFor();
  assert.equal(await card.locator("tbody tr").count(), 25);
  assert.match(await card.locator("h2").innerText(), /2000 devices/);
  assert.equal(await card.locator("tbody img").count(), 0);
  // HA state churn must neither subscribe again nor replace the DOM rows.
  assert.equal(await page.evaluate(async () => {
    const row = card.shadowRoot.querySelector("tbody tr");
    for (let i = 0; i < 100; i++) card.hass = { ...hass, states: {} };
    await new Promise(requestAnimationFrame);
    return row === card.shadowRoot.querySelector("tbody tr") && subscriptions === 1;
  }), true);
  await card.getByRole("searchbox").fill("dEvIcE 1999");
  await page.waitForFunction(() => card.shadowRoot.querySelector("tbody").textContent.includes("Device 1999"));
  assert.equal(await card.locator("tbody tr").count(), 1);
  await card.getByRole("searchbox").fill("");
  await card.getByRole("button", { name: "IP Address" }).click();
  await page.waitForFunction(() => card.shadowRoot.querySelector("th[data-sort=ip_address]").getAttribute("aria-sort") === "ascending");
  assert.match(await card.locator("tbody tr").first().innerText(), /192.168.40.0/);
  await card.getByRole("combobox").selectOption("100");
  await page.waitForFunction(() => card.shadowRoot.querySelectorAll("tbody tr").length === 100);
  await card.getByRole("button", { name: "Next page", exact: true }).click();
  await page.waitForFunction(() => card.shadowRoot.querySelector(".page").textContent === "2 / 20");
  await page.evaluate(() => { window.card2 = document.createElement("mikrotik-dhcp-browser-card"); card2.setConfig({ subnet: "192.168.40.0/22" }); card2.hass = hass; document.body.append(card2); });
  await page.waitForFunction(() => card2.shadowRoot.querySelector(".counts").textContent.includes("1024 matching"));
  assert.equal(await page.evaluate(() => subscriptions), 1);
  // Removal stays until an explicit resnapshot, including when viewing last page.
  await page.evaluate(() => { records = records.slice(0, 1); for (const cb of callbacks) cb({ add: [] }); });
  assert.match(await card.locator("h2").innerText(), /2000 devices/);
  await card.getByRole("button", { name: "Refresh", exact: true }).click();
  await page.waitForFunction(() => card.shadowRoot.querySelector("h2").textContent.includes("1 devices"));
  assert.equal(await card.locator(".page").innerText(), "1 / 1");
  assert.equal(await page.evaluate(() => active), 1);
  // Restore enough rows to verify responsive table scrolling and both themes.
  await page.evaluate(() => {
    card2.remove();
    for (const cb of callbacks) cb({ add: [{ ...records[0], hostname: "shelly-pump" }] });
    for (const cb of callbacks) cb({ add: Array.from({ length: 100 }, (_, n) => ({ mac_address: (n + 1).toString(16).padStart(12, "0"), hostname: `shelly-device-${n}`, ip_address: `192.168.40.${n}` })) });
  });
  await card.getByRole("combobox").selectOption("25");
  await page.setViewportSize({ width: 375, height: 850 });
  await page.waitForFunction(() => card.shadowRoot.querySelectorAll("tbody tr").length === 25);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  if (process.env.CARD_SCREENSHOT_DIR) await page.screenshot({ path: `${process.env.CARD_SCREENSHOT_DIR}/dhcp-card-light.png` });
  await page.evaluate(() => {
    document.body.style.cssText = "background:#111;--primary-text-color:#eee;--secondary-text-color:#bbb;--card-background-color:#222;--secondary-background-color:#303030;--divider-color:#444";
  });
  if (process.env.CARD_SCREENSHOT_DIR) await page.screenshot({ path: `${process.env.CARD_SCREENSHOT_DIR}/dhcp-card-dark.png` });
  await page.evaluate(() => { card.hass = { ...hass, user: { is_admin: false } }; });
  await page.waitForFunction(() => card.shadowRoot.querySelector(".status").textContent.includes("Administrator"));
  assert.equal(await page.evaluate(() => active), 0);
  assert.equal(await card.locator("tbody tr").count(), 1); // Empty-state row, no data.
  await page.evaluate(() => { card.hass = hass; });
  await page.waitForFunction(() => active === 1);
  await page.evaluate(() => { card.remove(); document.body.append(card); });
  await page.waitForFunction(() => active === 1 && !card._feed.session.cancelled);
  // Replacing the entire HA connection also releases the old feed.
  await page.evaluate(() => { card.hass = { ...hass, connection: { ...connection } }; });
  await page.waitForFunction(() => active === 1);
  await page.evaluate(() => card.remove());
  await page.waitForFunction(() => active === 0);
  assert.equal(await page.evaluate(() => listeners.size), 0);
  assert.deepEqual(errors, []);
  console.log("Browser checks passed: 2,000 devices, search, sort, pagination, CIDR, sharing, refresh, state churn, safe rendering, mobile themes, authorization, cleanup.");
} finally {
  await browser.close();
  await new Promise((resolve) => server.close(resolve));
}
