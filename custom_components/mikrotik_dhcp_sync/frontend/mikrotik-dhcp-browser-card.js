/* Optional Lovelace card. No RouterOS requests, entities, timers, or dependencies. */
export function parseIP(value) {
  if (typeof value !== "string") return null;
  if (/^\d{1,3}(\.\d{1,3}){3}$/.test(value)) {
    const parts = value.split(".").map(Number);
    if (parts.some((part) => part > 255)) return null;
    return { version: 4, number: parts.reduce((n, part) => n * 256n + BigInt(part), 0n) };
  }
  // Also retain and numerically sort IPv6 records from other DHCP sources.
  let expanded = value;
  if (expanded.includes(".")) {
    const end = expanded.lastIndexOf(":");
    if (end < 0) return null;
    const v4 = parseIP(expanded.slice(end + 1));
    if (!v4 || v4.version !== 4) return null;
    expanded = `${expanded.slice(0, end)}:${(v4.number >> 16n).toString(16)}:${(v4.number & 65535n).toString(16)}`;
  }
  if (!expanded.includes(":") || !/^[\da-f:]+$/i.test(expanded)) return null;
  const halves = expanded.split("::");
  if (halves.length > 2) return null;
  const left = halves[0] ? halves[0].split(":") : [];
  const right = halves.length === 2 && halves[1] ? halves[1].split(":") : [];
  const missing = 8 - left.length - right.length;
  if ((halves.length === 1 && missing !== 0) || (halves.length === 2 && missing < 1)) return null;
  const parts = [...left, ...Array(halves.length === 2 ? missing : 0).fill("0"), ...right];
  if (parts.some((part) => !/^[\da-f]{1,4}$/i.test(part))) return null;
  return { version: 6, number: parts.reduce((n, part) => n * 65536n + BigInt(`0x${part}`), 0n) };
}

export function parseSubnet(value) {
  if (!value) return null;
  if (typeof value !== "string") throw new Error("subnet must be an IPv4 CIDR, e.g. 192.168.40.0/22");
  const parts = value.split("/");
  const ip = parseIP(parts[0]);
  if (parts.length !== 2 || !ip || ip.version !== 4 || !/^\d{1,2}$/.test(parts[1]) || Number(parts[1]) > 32) {
    throw new Error("subnet must be an IPv4 CIDR, e.g. 192.168.40.0/22");
  }
  const shift = BigInt(32 - Number(parts[1]));
  return { shift, network: ip.number >> shift };
}

export function mergeDiscovery(devices, event, snapshot = false) {
  if (!event || !Array.isArray(event.add)) throw new Error("Unsupported DHCP discovery response");
  if (snapshot) devices.clear();
  for (const row of event.add) {
    if (!row || typeof row.mac_address !== "string") continue;
    const mac = row.mac_address.replace(/[:-]/g, "");
    if (!/^[\da-f]{12}$/i.test(mac)) continue;
    const mac_address = mac.toUpperCase().match(/../g).join(":");
    const hostname = typeof row.hostname === "string" ? row.hostname.trim() : "";
    const ip = parseIP(row.ip_address);
    const ip_address = ip ? row.ip_address : "";
    devices.set(mac_address, { mac_address, hostname, ip_address, ip,
      search: `${hostname}\n${ip_address}\n${mac_address}`.toLowerCase() });
  }
}

const collator = new Intl.Collator(undefined, { sensitivity: "base", numeric: true });
export function selectRows(devices, { search = "", subnet = null, sort = "hostname", descending = false, page = 0, pageSize = 50 } = {}) {
  const query = search.trim().toLowerCase();
  const rows = [...devices.values()].filter((row) => (!query || row.search.includes(query)) &&
    (!subnet || (row.ip?.version === 4 && (row.ip.number >> subnet.shift) === subnet.network)));
  rows.sort((a, b) => {
    let result;
    if (sort === "ip_address") {
      if (!a.ip || !b.ip) result = a.ip ? -1 : b.ip ? 1 : 0;
      else result = a.ip.version - b.ip.version || (a.ip.number < b.ip.number ? -1 : a.ip.number > b.ip.number ? 1 : 0);
    } else result = collator.compare(a[sort], b[sort]);
    return (result || collator.compare(a.mac_address, b.mac_address)) * (descending ? -1 : 1);
  });
  const pages = Math.max(1, Math.ceil(rows.length / pageSize));
  page = Math.max(0, Math.min(page, pages - 1));
  return { rows: rows.slice(page * pageSize, (page + 1) * pageSize), filtered: rows.length, total: devices.size, pages, page };
}

function errorText(error) {
  if (error?.code === "unauthorized") return "Administrator access is required to view Home Assistant’s DHCP cache.";
  if (error?.code === "unknown_command") return "DHCP Browser is unavailable. Enable Home Assistant’s dhcp integration and check version compatibility.";
  return `Unable to subscribe to DHCP Browser: ${error?.message || error?.code || "connection failed"}. Try Refresh.`;
}

// One subscription per HA connection, shared even by multiple card instances.
const feeds = new WeakMap();
export class DiscoveryFeed {
  constructor(connection) {
    this.connection = connection;
    this.devices = new Map();
    this.listeners = new Set();
    this.status = "Loading DHCP cache…";
    this.session = null;
    this.disconnected = () => {
      if (this.session) this.session.first = true;
      this.status = "Disconnected; waiting for Home Assistant…";
      this.emit();
    };
    // subscribeMessage automatically resubscribes. Its next event is a snapshot.
    this.ready = () => { if (!this.session) this.start(); };
    this.reconnectError = () => { this.status = "Home Assistant authentication failed. Sign in again."; this.emit(); };
  }
  attach(listener) {
    this.listeners.add(listener);
    if (this.listeners.size === 1) {
      this.connection.addEventListener("disconnected", this.disconnected);
      this.connection.addEventListener("ready", this.ready);
      this.connection.addEventListener("reconnect-error", this.reconnectError);
      this.start();
    }
    listener(this);
    return () => {
      this.listeners.delete(listener);
      if (this.listeners.size) return;
      this.connection.removeEventListener("disconnected", this.disconnected);
      this.connection.removeEventListener("ready", this.ready);
      this.connection.removeEventListener("reconnect-error", this.reconnectError);
      this.stop();
      this.devices.clear();
    };
  }
  emit() { for (const listener of this.listeners) listener(this); }
  start() {
    if (this.session || !this.listeners.size) return;
    if (!this.connection.connected) { this.status = "Waiting for Home Assistant connection…"; this.emit(); return; }
    const session = { first: true, cancelled: false };
    this.session = session;
    this.status = "Loading DHCP cache…";
    this.emit();
    session.promise = Promise.resolve().then(() => this.connection.subscribeMessage((event) => {
      if (session.cancelled) return;
      try {
        mergeDiscovery(this.devices, event, session.first);
        session.first = false;
        this.status = "";
      } catch (error) { this.status = errorText(error); }
      this.emit();
    }, { type: "dhcp/subscribe_discovery" })).catch((error) => {
      if (!session.cancelled) {
        this.session = null;
        this.status = errorText(error);
        this.emit();
      }
      return null;
    });
  }
  stop() {
    const session = this.session;
    if (!session || session.cancelled) return;
    session.cancelled = true;
    // Await a late subscription acknowledgement before releasing it. Do not
    // open a replacement concurrently, including disconnect/re-attach races.
    session.promise.then((unsubscribe) => unsubscribe?.()).catch(() => {
      // HA may have already dropped the subscription with the old socket.
    }).finally(() => {
      if (this.session === session) this.session = null;
      this.start();
    });
  }
  refresh() {
    if (this.session) {
      this.stop();
      this.status = "Refreshing DHCP cache…";
      this.emit();
    }
    else this.start();
  }
}

export class MikrotikDhcpBrowserCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._page = 0;
    this._search = "";
    this._sort = "hostname";
    this._descending = false;
  }
  setConfig(config) {
    if (!config || (config.page_size !== undefined && ![25, 50, 100].includes(config.page_size))) {
      throw new Error("page_size must be 25, 50, or 100");
    }
    if (config.title !== undefined && typeof config.title !== "string") throw new Error("title must be a string");
    this._subnet = parseSubnet(config.subnet);
    this._config = { title: "DHCP Browser", page_size: 50, ...config };
    this._pageSize = this._config.page_size;
    this._page = 0;
    if (!this._built) this._build();
    this._connect();
    this._scheduleRender();
  }
  set hass(hass) {
    const changed = this._hass?.connection !== hass.connection || this._hass?.user?.is_admin !== hass.user?.is_admin;
    this._hass = hass;
    if (changed) { this._release(); this._connect(); this._scheduleRender(); }
  }
  connectedCallback() { this._connect(); this._scheduleRender(); }
  disconnectedCallback() {
    this._release();
    cancelAnimationFrame(this._frame);
    this._frame = null;
  }
  _release() { this._detach?.(); this._detach = null; this._feed = null; }
  _connect() {
    if (!this.isConnected || !this._config || !this._hass?.connection || this._detach || this._hass.user?.is_admin === false) return;
    const connection = this._hass.connection;
    if (!feeds.has(connection)) feeds.set(connection, new DiscoveryFeed(connection));
    this._feed = feeds.get(connection);
    this._detach = this._feed.attach(() => this._scheduleRender());
  }
  _scheduleRender() {
    if (!this._built || !this.isConnected || this._frame != null) return;
    this._frame = requestAnimationFrame(() => { this._frame = null; this._renderRows(); });
  }
  _build() {
    this._built = true;
    // All dynamic content is assigned via textContent, never parsed as HTML.
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; min-width:0; width:100%; color:var(--primary-text-color); }
        ha-card { display:block; max-width:100%; overflow:hidden; width:100%; }
        ha-card,.wrap,table,th,td,button { user-select:text; -webkit-user-select:text; }
        .wrap { box-sizing:border-box; min-width:0; padding:12px; width:100%; }
        .header,.controls,.footer { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
        .header,.controls { margin-bottom:8px; }
        h2 { font-size:18px; font-weight:400; margin:0; flex:1; }
        button,input,select { font:inherit; color:var(--primary-text-color); }
        button,input,select { box-sizing:border-box; background:var(--card-background-color,var(--ha-card-background,#fff)); border:1px solid var(--divider-color,#aaa); border-radius:6px; min-height:32px; padding:0 8px; font-size:13px; }
        button { cursor:pointer; }
        button:disabled { opacity:.45; cursor:default; }
        button:focus-visible,input:focus-visible,select:focus-visible { outline:2px solid var(--primary-color); outline-offset:2px; }
        .search { flex:1 1 220px; min-width:160px; }
        input { width:100%; cursor:text; user-select:text; -webkit-user-select:text; }
        input:focus { border-color:var(--primary-color,#03a9f4); }
        label { font-size:13px; }
        .scroll { overflow-x:auto; overflow-y:visible; width:100%; }
        table { width:100%; min-width:max-content; border-collapse:collapse; font-size:13px; line-height:normal; user-select:text; -webkit-user-select:text; }
        th,td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--divider-color,#ddd); white-space:nowrap; }
        td { user-select:text; -webkit-user-select:text; cursor:text; }
        th { background:var(--card-background-color,var(--ha-card-background,#fff)); position:sticky; top:0; z-index:1; }
        th button { border:0; background:transparent; min-height:0; padding:0; font-weight:400; white-space:nowrap; }
        td:not(:first-child) { white-space:nowrap; font-variant-numeric:tabular-nums; }
        .status,.note { color:var(--secondary-text-color); font-size:12px; margin-top:8px; }
        .status { margin:0 0 8px; }
        .status:empty { display:none; }
        .footer { font-size:12px; margin-top:8px; }
        .counts,.page { color:var(--secondary-text-color); }
        .counts { flex:1; min-width:140px; }
        @media(max-width:760px) { .wrap { padding:10px; } }
      </style>
      <ha-card>
        <div class="wrap">
        <div class="header"><h2></h2></div>
        <div class="controls"><label class="search"><input type="search" placeholder="Search hostname, IP or MAC" aria-label="Search devices" autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false"></label>
          <button class="refresh" title="Reload the cache, including removals">Refresh</button>
          <label>Rows <select aria-label="Rows per page"><option>25</option><option selected>50</option><option>100</option></select></label></div>
        <div class="status" role="status"></div>
        <div class="scroll" tabindex="0" role="region" aria-label="DHCP devices">
          <table><thead><tr><th scope="col" data-sort="hostname"><button>Hostname</button></th><th scope="col" data-sort="ip_address"><button>IP Address</button></th><th scope="col" data-sort="mac_address"><button>MAC Address</button></th></tr></thead><tbody></tbody></table>
        </div>
        <div class="footer"><span class="counts" aria-live="polite"></span><button class="previous" aria-label="Previous page">Previous</button><span class="page"></span><button class="next" aria-label="Next page">Next</button></div>
        <div class="note">Home Assistant DHCP cache · Removals appear after Refresh or reconnect.</div>
        </div>
      </ha-card>`;
    const root = this.shadowRoot;
    root.querySelector("input").addEventListener("input", (event) => { this._search = event.target.value; this._page = 0; this._scheduleRender(); });
    root.querySelector("select").addEventListener("change", (event) => { this._pageSize = Number(event.target.value); this._page = 0; this._scheduleRender(); });
    root.querySelector(".refresh").addEventListener("click", () => this._feed?.refresh());
    root.querySelector(".previous").addEventListener("click", () => { this._page--; this._scheduleRender(); });
    root.querySelector(".next").addEventListener("click", () => { this._page++; this._scheduleRender(); });
    for (const th of root.querySelectorAll("th")) th.querySelector("button").addEventListener("click", () => {
      this._descending = this._sort === th.dataset.sort ? !this._descending : false;
      this._sort = th.dataset.sort;
      this._page = 0;
      this._scheduleRender();
    });
  }
  _renderRows() {
    const root = this.shadowRoot;
    const result = selectRows(this._feed?.devices || new Map(), { search: this._search, subnet: this._subnet,
      sort: this._sort, descending: this._descending, page: this._page, pageSize: this._pageSize });
    this._page = result.page;
    root.querySelector("h2").textContent = `${this._config.title} — ${result.total} devices`;
    root.querySelector("select").value = String(this._pageSize);
    const unauthorized = this._hass?.user?.is_admin === false;
    root.querySelector(".status").textContent = unauthorized ? errorText({ code: "unauthorized" }) : (this._feed?.status || "");
    root.querySelector(".refresh").disabled = !this._feed || this._feed.session?.cancelled === true;
    root.querySelector(".counts").textContent = `${result.filtered} matching / ${result.total} total`;
    root.querySelector(".page").textContent = `${result.page + 1} / ${result.pages}`;
    root.querySelector(".previous").disabled = !result.page;
    root.querySelector(".next").disabled = result.page + 1 >= result.pages;
    for (const th of root.querySelectorAll("th")) {
      const direction = this._sort === th.dataset.sort ? (this._descending ? "descending" : "ascending") : "none";
      th.setAttribute("aria-sort", direction);
      const label = { hostname: "Hostname", ip_address: "IP Address", mac_address: "MAC Address" }[th.dataset.sort];
      th.querySelector("button").textContent = label + (direction === "none" ? "" : direction === "ascending" ? " ↑" : " ↓");
    }
    // Keep selection intact when a notification does not change this page.
    const rowKey = JSON.stringify([result.total > 0, result.rows.map((row) => [row.hostname, row.ip_address, row.mac_address])]);
    if (rowKey === this._renderedRows) return;
    this._renderedRows = rowKey;
    const fragment = document.createDocumentFragment();
    for (const row of result.rows) {
      const tr = document.createElement("tr");
      for (const value of [row.hostname || "—", row.ip_address || "—", row.mac_address]) {
        const td = document.createElement("td");
        td.textContent = value;
        tr.append(td);
      }
      fragment.append(tr);
    }
    if (!result.rows.length) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 3;
      td.textContent = result.total ? "No devices match your filters." : "No discovered devices.";
      tr.append(td); fragment.append(tr);
    }
    root.querySelector("tbody").replaceChildren(fragment);
  }
  getCardSize() { return 5; }
  getGridOptions() { return { columns: "full" }; }
  static getStubConfig() { return { title: "DHCP Browser", page_size: 50, grid_options: { columns: "full" } }; }
}

if (!customElements.get("mikrotik-dhcp-browser-card")) {
  customElements.define("mikrotik-dhcp-browser-card", MikrotikDhcpBrowserCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "mikrotik-dhcp-browser-card", name: "DHCP Browser", description: "Browse Home Assistant’s DHCP discovery cache (administrator required)." });
}
