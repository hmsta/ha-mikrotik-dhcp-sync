import assert from "node:assert/strict";
import { test } from "node:test";

globalThis.HTMLElement = class {};
globalThis.customElements = { get: () => true };
const { parseIP, parseSubnet, mergeDiscovery, selectRows, DiscoveryFeed } = await import("../../custom_components/mikrotik_dhcp_sync/frontend/mikrotik-dhcp-browser-card.js");
const tick = () => new Promise((resolve) => setImmediate(resolve));
const device = (n, overrides = {}) => ({ mac_address: n.toString(16).padStart(12, "0"), hostname: `Device ${n}`, ip_address: `192.168.40.${n % 256}`, ...overrides });

test("initial snapshot, incremental MAC upsert, no inferred removals, replacement snapshot", () => {
  const rows = new Map();
  mergeDiscovery(rows, { add: [device(1), device(2)] }, true);
  mergeDiscovery(rows, { add: [device(1, { hostname: "Changed", ip_address: "10.0.0.2" }), device(3)] });
  assert.equal(rows.size, 3);
  assert.equal(rows.get("00:00:00:00:00:01").hostname, "Changed");
  mergeDiscovery(rows, { add: [] }); // Even an empty incremental event is not a deletion.
  assert.equal(rows.size, 3);
  mergeDiscovery(rows, { add: [device(2)] }, true);
  assert.equal(rows.size, 1);
  mergeDiscovery(rows, { add: [] }, true);
  assert.deepEqual(selectRows(rows), { rows: [], total: 0, filtered: 0, pages: 1, page: 0 });
});

test("malformed values are safe; invalid MACs skipped, missing hostname and invalid IP retained as blanks", () => {
  const rows = new Map();
  mergeDiscovery(rows, { add: [null, {}, device(1, { hostname: {}, ip_address: "999.0.0.1" }), device(2, { mac_address: "broken" }), device(3, { hostname: null, ip_address: null })] }, true);
  assert.equal(rows.size, 2);
  assert.equal(selectRows(rows).rows[0].hostname, "");
  assert.equal(selectRows(rows).rows[0].ip_address, "");
  assert.throws(() => mergeDiscovery(rows, {}), /Unsupported/);
  for (const value of [null, 5, "", "1.2.3", "256.0.0.1", "::::", "1:2:3:4:5:6:7:", "1::2::3"]) assert.equal(parseIP(value), null);
});

test("numeric IP ordering including IPv6 and descending", () => {
  const rows = new Map();
  const ips = ["192.168.10.100", "192.168.10.9", "10.0.0.2", "192.168.2.10", "2001:db8::10", "2001:db8::2"];
  mergeDiscovery(rows, { add: ips.map((ip_address, n) => device(n, { ip_address })) }, true);
  const sorted = selectRows(rows, { sort: "ip_address" }).rows.map((row) => row.ip_address);
  assert.deepEqual(sorted, ["10.0.0.2", "192.168.2.10", "192.168.10.9", "192.168.10.100", "2001:db8::2", "2001:db8::10"]);
  assert.deepEqual(selectRows(rows, { sort: "ip_address", descending: true }).rows.map((row) => row.ip_address), sorted.toReversed());
  assert.equal(parseIP("::ffff:192.0.2.1").number, parseIP("::ffff:c000:201").number);
});

test("IPv4 CIDR /22 boundary, /0, /32, non-network input, invalid configuration", () => {
  const rows = new Map();
  mergeDiscovery(rows, { add: ["192.168.39.255", "192.168.40.0", "192.168.43.255", "192.168.44.0", "::1"].map((ip_address, n) => device(n, { ip_address })) }, true);
  assert.equal(selectRows(rows, { subnet: parseSubnet("192.168.41.7/22") }).filtered, 2);
  assert.equal(selectRows(rows, { subnet: parseSubnet("0.0.0.0/0") }).filtered, 4);
  assert.equal(selectRows(rows, { subnet: parseSubnet("192.168.40.0/32") }).filtered, 1);
  for (const cidr of ["192.168.0.0/33", "192.168.0.0/-1", "192.168.0.0", "::/0", {}, "10.0.0.0/2/3"]) assert.throws(() => parseSubnet(cidr));
});

test("case insensitive search across all columns, filters before pagination, 2,000 entries", () => {
  const rows = new Map();
  mergeDiscovery(rows, { add: Array.from({ length: 2000 }, (_, n) => device(n)) }, true);
  assert.equal(selectRows(rows).rows.length, 50);
  assert.equal(selectRows(rows, { pageSize: 25 }).pages, 80);
  assert.equal(selectRows(rows, { page: 999, pageSize: 100 }).page, 19);
  assert.equal(selectRows(rows, { page: -1 }).page, 0);
  assert.equal(selectRows(rows, { search: "DEVICE 1999", page: 50 }).rows[0].hostname, "Device 1999");
  assert.equal(selectRows(rows, { search: "00:00:00:00:07:cf" }).filtered, 1);
  assert.ok(selectRows(rows, { search: "192.168.40.1" }).filtered > 0);
  assert.equal(selectRows(rows, { search: "no match" }).pages, 1);
});

class Connection {
  connected = true;
  listeners = new Map();
  calls = [];
  active = 0;
  addEventListener(name, cb) { this.listeners.set(name, cb); }
  removeEventListener(name, cb) { if (this.listeners.get(name) === cb) this.listeners.delete(name); }
  fire(name) { this.listeners.get(name)?.(); }
  subscribeMessage(cb, message) {
    assert.deepEqual(message, { type: "dhcp/subscribe_discovery" });
    return new Promise((resolve, reject) => {
      this.calls.push({ cb, reject, ack: () => { this.active++; resolve(async () => { this.active--; }); } });
    });
  }
}

test("shared feed, errors, retry, reconnect snapshot, manual refresh and cleanup", async () => {
  const conn = new Connection();
  const feed = new DiscoveryFeed(conn);
  const detach = feed.attach(() => {});
  const detach2 = feed.attach(() => {});
  await tick();
  assert.equal(conn.calls.length, 1);
  conn.calls[0].reject({ code: "unauthorized" });
  await tick();
  assert.match(feed.status, /Administrator/);
  feed.refresh(); await tick();
  conn.calls[1].ack();
  conn.calls[1].cb({ add: [device(1), device(2)] });
  await tick();
  assert.equal(feed.devices.size, 2);
  conn.connected = false; conn.fire("disconnected");
  assert.match(feed.status, /Disconnected/);
  conn.connected = true; conn.fire("ready");
  // HA's connection reuses the callback on its automatic resubscription.
  conn.calls[1].cb({ add: [device(2)] });
  assert.equal(feed.devices.size, 1);
  assert.equal(conn.calls.length, 2); // No card-initiated duplicate reconnect.
  feed.refresh(); feed.refresh(); await tick();
  assert.equal(conn.active, 0);
  assert.equal(conn.calls.length, 3);
  conn.calls[2].ack(); conn.calls[2].cb({ add: [] }); await tick();
  assert.equal(feed.devices.size, 0);
  detach(); assert.equal(conn.active, 1);
  detach2(); await tick();
  assert.equal(conn.active, 0);
  assert.equal(conn.listeners.size, 0);
});

test("late subscription acknowledgement after detach and immediate reattach is serialized", async () => {
  const conn = new Connection();
  const feed = new DiscoveryFeed(conn);
  const detach = feed.attach(() => {});
  await tick(); detach();
  const detach2 = feed.attach(() => {});
  await tick();
  assert.equal(conn.calls.length, 1);
  conn.calls[0].cb({ add: [device(1)] });
  assert.equal(feed.devices.size, 0);
  conn.calls[0].ack(); await tick();
  assert.equal(conn.active, 0);
  assert.equal(conn.calls.length, 2);
  conn.calls[1].ack(); await tick(); detach2(); await tick();
  assert.equal(conn.active, 0);
});

test("wait while disconnected, handle unsupported endpoint and authentication failures", async () => {
  const conn = new Connection(); conn.connected = false;
  const feed = new DiscoveryFeed(conn);
  const detach = feed.attach(() => {});
  await tick(); assert.equal(conn.calls.length, 0);
  conn.connected = true; conn.fire("ready"); await tick();
  conn.calls[0].reject({ code: "unknown_command" }); await tick();
  assert.match(feed.status, /Enable Home Assistant/);
  feed.refresh(); await tick();
  conn.calls[1].reject(new Error("test failure")); await tick();
  assert.match(feed.status, /test failure/);
  conn.fire("reconnect-error"); assert.match(feed.status, /Sign in/);
  detach();
});

test("late rejected acknowledgement after removal does not resurrect a subscription", async () => {
  const conn = new Connection();
  const feed = new DiscoveryFeed(conn);
  const detach = feed.attach(() => {});
  await tick(); detach();
  conn.calls[0].reject({ code: "unauthorized" }); await tick();
  assert.equal(conn.calls.length, 1);
  assert.equal(feed.session, null);
  assert.equal(conn.listeners.size, 0);
});
