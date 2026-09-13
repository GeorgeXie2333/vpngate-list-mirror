import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { PATHS, decodeConfig, digest, loadSnapshot, normalizeSourceHostname, validateIndex, verifySnapshot } from "../examples/consume.mjs";

const golden = JSON.parse(await readFile(new URL("./fixtures/snapshot.json", import.meta.url), "utf8"));
const fresh = () => ({ index: structuredClone(golden.index),
  files: Object.fromEntries(Object.entries(golden.files).map(([p, b]) => [p, new Uint8Array(Buffer.from(b, "base64"))])) });
const json = value => new TextEncoder().encode(JSON.stringify(value));

function reader(snapshot, calls = [], badCDN = false) {
  return async (url, limit) => {
    calls.push(url);
    if (url.endsWith("/latest.json")) return json(snapshot.index);
    if (badCDN && url.includes("cdn.jsdelivr.net")) return json({ bad: "cached content" });
    const path = PATHS.find(p => url.endsWith("/" + p));
    assert.ok(path, "Unexpected request");
    assert.equal(limit, snapshot.index.files[path].bytes);
    return snapshot.files[path];
  };
}

test("complete snapshot, country selection, and exact decoded configuration", async () => {
  const input = fresh();
  const calls = [];
  const snapshot = await loadSnapshot({ repo: "OWNER/REPO", read: reader(input, calls) });
  assert.equal(snapshot.servers.length, 2);
  const node = snapshot.servers.find(n => n.country_code === "JP");
  const config = decodeConfig(node);
  assert.equal(await digest(config), node.openvpn_config_sha256);
  assert.match(new TextDecoder().decode(config), /up \/this-command-must-never-run/);
  assert.equal(calls.length, 4);
  assert.ok(calls.slice(1).every(url => url.includes("@" + input.index.data_commit)));
});

test("source identifiers allow underscores while rejecting malformed labels", () => {
  assert.equal(normalizeSourceHostname(" _UNREGISTERED_vpn335506854. "), "_unregistered_vpn335506854");
  assert.equal(normalizeSourceHostname("_" + "a".repeat(62)), "_" + "a".repeat(62));
  for (const value of ["", "bad/host", "bad host", "bad\nhost", "bad\n.host", "bad\0host", "bad..host",
    "-bad", "bad-", "bad:443", "bad@host", "\u202ehost", "_" + "a".repeat(63), Array(4).fill("a".repeat(63)).join(".")])
    assert.throws(() => normalizeSourceHostname(value), /Invalid hostname/);
});

test("unregistered source identifier loads with consistent file hashes and stable ID", async () => {
  const input = fresh(), host = "_unregistered_vpn335506854";
  const servers = JSON.parse(new TextDecoder().decode(input.files[PATHS[1]]));
  const node = servers.servers.find(row => row.hostname === "vpn-example");
  const config = node.openvpn_config_base64;
  node.hostname = host;
  node.id = "v1:2d982db7bbb3bbcabc99d990b5ff43a505d48ba2d28c8de586ffdd02d285501c";
  servers.servers.sort((a, b) => a.id.localeCompare(b.id));
  input.files[PATHS[0]] = new TextEncoder().encode(new TextDecoder().decode(input.files[PATHS[0]])
    .replace("vpn-example,", `${host},`));
  servers.source_csv_sha256 = await digest(input.files[PATHS[0]]);
  const countries = JSON.parse(new TextDecoder().decode(input.files[PATHS[2]]));
  countries.source_csv_sha256 = servers.source_csv_sha256;
  input.files[PATHS[1]] = json(servers);
  input.files[PATHS[2]] = json(countries);
  for (const path of PATHS) Object.assign(input.index.files[path],
    {bytes: input.files[path].length, sha256: await digest(input.files[path])});
  const snapshot = await loadSnapshot({read: reader(input)});
  assert.equal(snapshot.servers.find(row => row.id === node.id).hostname, host);
  assert.equal(snapshot.servers.find(row => row.id === node.id).openvpn_config_base64, config);
});

test("bad CDN content falls back to Raw at the same full SHA", async () => {
  const calls = [];
  const input = fresh();
  await loadSnapshot({ repo: "OWNER/REPO", read: reader(input, calls, true) });
  assert.equal(calls.length, 7);
  assert.ok(calls.filter(url => url.includes("/data/") && url.includes("raw.githubusercontent.com"))
    .every(url => url.includes("/" + input.index.data_commit + "/")));
});

test("older index is rejected without changing the previous snapshot", async () => {
  const input = fresh();
  const previous = await verifySnapshot(input.index, input.files);
  const original = previous.index.fetched_at;
  input.index = { ...input.index, fetched_at: "2026-08-31T00:00:00.000Z" };
  await assert.rejects(loadSnapshot({ previous, read: reader(input) }), /older index/);
  assert.equal(previous.index.fetched_at, original);
});

test("same data commit renews freshness without downloading data", async () => {
  const input = fresh();
  const previous = await verifySnapshot(input.index, input.files);
  input.index = { ...input.index, fetched_at: "2026-09-01T01:00:00.000Z", index_generated_at: "2026-09-01T01:00:00.000Z" };
  const calls = [];
  const current = await loadSnapshot({ previous, read: reader(input, calls) });
  assert.equal(calls.length, 1);
  assert.equal(current.index.data_commit, previous.index.data_commit);
  assert.notEqual(current.index.fetched_at, previous.index.fetched_at);
});

test("mixed versions fail even if the individual file hash matches its manifest", async () => {
  const input = fresh();
  const path = "data/countries.json";
  const country = JSON.parse(new TextDecoder().decode(input.files[path]));
  country.source_csv_sha256 = "f".repeat(64);
  input.files[path] = json(country);
  input.index.files[path] = { ...input.index.files[path], bytes: input.files[path].length, sha256: await digest(input.files[path]) };
  await assert.rejects(verifySnapshot(input.index, input.files), /Mixed snapshot/);
});

test("corrupt data never resolves as a new snapshot", async () => {
  const input = fresh();
  input.files[PATHS[1]][0] ^= 1;
  await assert.rejects(loadSnapshot({ read: reader(input) }), /CDN and same-commit Raw failed/);
});

test("unsupported schemas, excessive sizes, wrong types and invalid timestamps fail", () => {
  const { index } = fresh();
  for (const change of [{ schema_version: 2 }, { server_count: true }, { data_commit: "main" },
    { fetched_at: "2026-02-30T00:00:00.000Z" }, { fetched_at: "2026-09-01T00:00:00+08:00" }]) {
    assert.throws(() => validateIndex({ ...index, ...change }));
  }
  index.files[PATHS[0]].bytes = 1e9;
  assert.throws(() => validateIndex(index));
});

test("unknown optional fields are accepted; Base64 padding is strict", async () => {
  const input = fresh();
  input.index.future_field = "allowed";
  await verifySnapshot(input.index, input.files);
  assert.throws(() => decodeConfig({ openvpn_config_base64: "YR==" }));
  assert.throws(() => decodeConfig({ openvpn_config_base64: "aGVsbG8=\n" }));
});
