// Dependency-free ES module for browsers (HTTPS) and Node.js 22+.
// Pass the previous verified snapshot to loadSnapshot. Assign its result only
// after it resolves; on rejection, the previous complete snapshot is unchanged.

export const PATHS = ["data/vpngate.csv", "data/servers.json", "data/countries.json"];
const SOURCE = "https://www.vpngate.net/api/iphone/";
const encoder = new TextEncoder();
const decoder = new TextDecoder("utf-8", { fatal: true });
const HEX = /^[0-9a-f]{64}$/;
const UTC = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/;
const assert = (ok, message) => { if (!ok) throw new Error(message); };
const count = (n, min = 1, max = 5000) => Number.isSafeInteger(n) && n >= min && n <= max;
const bytesOf = value => value instanceof Uint8Array ? value : new Uint8Array(value);
export const parseJSON = bytes => JSON.parse(decoder.decode(bytes));

// Source metadata may contain underscores; this is not a DNS destination check.
export function normalizeSourceHostname(value) {
  assert(typeof value === "string" && value.length <= 254, "Invalid hostname");
  const host = value.trim().toLowerCase().replace(/\.$/, "");
  assert(host.length > 0 && host.length <= 253 && !/[^a-z0-9_.-]/.test(host) && host.split(".").every(label =>
    /^[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?$/.test(label)), "Invalid hostname");
  return host;
}

export async function digest(bytes) {
  return [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))]
    .map(x => x.toString(16).padStart(2, "0")).join("");
}

function timestamp(value) {
  assert(typeof value === "string" && UTC.test(value), "Invalid UTC timestamp");
  const ms = Date.parse(value);
  assert(Number.isFinite(ms) && new Date(ms).toISOString() === value, "Invalid UTC date");
  return ms;
}

export function validateIndex(index, previous = null, maxAgeHours = null) {
  assert(index?.schema_version === 1, "Unsupported schema_version");
  assert(index.source_url === SOURCE, "Unexpected source_url");
  assert(Object.hasOwn(index, "source_updated_at"), "Missing source_updated_at");
  if (index.source_updated_at !== null) timestamp(index.source_updated_at);
  assert(typeof index.data_commit === "string" && /^[0-9a-f]{40}$/.test(index.data_commit), "Invalid full commit SHA");
  assert(count(index.server_count), "Invalid server count");
  assert(count(index.source_record_count, index.server_count), "Invalid source record count");
  assert(count(index.country_count, 1, index.server_count), "Invalid country count");
  assert(/^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/actions\/runs\/[0-9]+$/.test(index.workflow_run_url), "Invalid run URL");
  const fetched = timestamp(index.fetched_at);
  const generated = timestamp(index.generated_at);
  assert(timestamp(index.index_generated_at) >= Math.max(fetched, generated), "Inconsistent timestamps");
  assert(fetched <= Date.now() + 300000, "Unexpected future snapshot");
  if (maxAgeHours !== null) assert(Date.now() - fetched <= maxAgeHours * 3600000, "Snapshot is too old");
  for (const path of PATHS) {
    const meta = index.files?.[path];
    assert(meta && typeof meta.sha256 === "string" && HEX.test(meta.sha256), "Invalid file hash");
    assert(count(meta.bytes, 1, 16 * 1024 * 1024), "Invalid file size");
    assert(meta.server_count === index.server_count, "Inconsistent file counts");
  }
  if (previous) {
    const old = timestamp(previous.fetched_at);
    assert(fetched >= old, "Refusing an older index");
    if (fetched === old) {
      assert(index.data_commit === previous.data_commit && sameFiles(index, previous), "Conflicting index");
    }
  }
  return index;
}

function sameFiles(a, b) {
  return PATHS.every(path => ["sha256", "bytes", "server_count"].every(key => a.files[path][key] === b.files[path][key]));
}

export async function readBytes(url, limit, options = {}) {
  const response = await fetch(url, {
    credentials: "omit", signal: AbortSignal.timeout(20000), ...options
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  assert(response.body, "Missing response body");
  const reader = response.body.getReader();
  const chunks = [];
  let size = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      assert(size <= limit, "Response exceeds size limit");
      chunks.push(value);
    }
  } catch (error) {
    await reader.cancel().catch(() => {});
    throw error;
  } finally {
    reader.releaseLock();
  }
  const result = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.byteLength; }
  return result;
}

async function verifiedFile(repo, index, path, read) {
  const meta = index.files[path];
  const urls = [
    `https://cdn.jsdelivr.net/gh/${repo}@${index.data_commit}/${path}`,
    `https://raw.githubusercontent.com/${repo}/${index.data_commit}/${path}`
  ];
  for (const url of urls) {
    try {
      const bytes = bytesOf(await read(url, meta.bytes));
      assert(bytes.byteLength === meta.bytes && await digest(bytes) === meta.sha256, "Integrity mismatch");
      return bytes;
    } catch { /* Same-commit fallback; never substitute a branch URL. */ }
  }
  throw new Error(`CDN and same-commit Raw failed: ${path}`);
}

export function decodeConfig(server) {
  const value = server.openvpn_config_base64;
  assert(typeof value === "string" && value.length > 0 && value.length <= 174764 &&
    /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value), "Invalid configuration Base64");
  const binary = atob(value);
  assert(btoa(binary) === value && binary.length > 0 && binary.length <= 131072, "Noncanonical configuration");
  return Uint8Array.from(binary, char => char.charCodeAt(0));
}

export async function verifySnapshot(index, files) {
  validateIndex(index);
  for (const path of PATHS) {
    const bytes = files[path];
    assert(bytes instanceof Uint8Array && bytes.byteLength === index.files[path].bytes &&
      await digest(bytes) === index.files[path].sha256, `Integrity mismatch: ${path}`);
  }
  const nodes = parseJSON(files[PATHS[1]]);
  const countries = parseJSON(files[PATHS[2]]);
  for (const data of [nodes, countries]) {
    assert(data.schema_version === 1 && data.server_count === index.server_count &&
      data.source_csv_sha256 === index.files[PATHS[0]].sha256, "Mixed snapshot metadata");
  }
  assert(Array.isArray(nodes.servers) && nodes.servers.length === index.server_count, "Node count mismatch");
  assert(Array.isArray(countries.countries) && countries.countries.length === index.country_count, "Country count mismatch");
  const groups = new Map();
  let lastId = "";
  for (const node of nodes.servers) {
    assert(typeof node.id === "string" && /^v1:[0-9a-f]{64}$/.test(node.id) && node.id > lastId, "Duplicate or unsorted ID");
    lastId = node.id;
    const host = normalizeSourceHostname(node.hostname);
    assert(typeof node.ip === "string", "Invalid address");
    if (node.ip.includes(":")) {
      assert(new URL(`http://[${node.ip}]/`).hostname.slice(1, -1) === node.ip, "Noncanonical IPv6");
    } else {
      const octets = node.ip.split(".");
      assert(octets.length === 4 && octets.every(x => /^(?:0|[1-9][0-9]{0,2})$/.test(x) && Number(x) <= 255), "Invalid IPv4");
    }
    assert(node.id === "v1:" + await digest(encoder.encode(`vpngate-node-v1\0${host}\0${node.ip}`)), "Node ID mismatch");
    assert(node.country_code === null || typeof node.country_code === "string" && /^[A-Z]{2}$/.test(node.country_code) && !["XX", "ZZ"].includes(node.country_code), "Invalid country code");
    assert(node.country_name === null || typeof node.country_name === "string" && node.country_name.length > 0, "Invalid country name");
    for (const key of ["score", "ping_ms", "speed_bps", "num_vpn_sessions"]) {
      assert(node[key] === null || count(node[key], 0, Number.MAX_SAFE_INTEGER), "Invalid metric");
    }
    const config = decodeConfig(node);
    assert(config.length === node.openvpn_config_bytes && await digest(config) === node.openvpn_config_sha256, "Configuration integrity mismatch");
    const group = groups.get(node.country_code) ?? { count: 0, names: new Set() };
    group.count++;
    if (node.country_name !== null) group.names.add(node.country_name);
    groups.set(node.country_code, group);
  }
  assert(groups.size === countries.countries.length, "Country groups mismatch");
  for (const group of countries.countries) {
    const expected = groups.get(group.code);
    assert(expected && group.server_count === expected.count && Array.isArray(group.names) &&
      JSON.stringify([...group.names].sort()) === JSON.stringify([...expected.names].sort()), "Country totals/names mismatch");
    groups.delete(group.code);
  }
  return { index, files, servers: nodes.servers, countries: countries.countries };
}

export async function loadSnapshot({ repo = "GeorgeXie2333/vpngate-list-mirror", previous = null,
  read = readBytes, maxAgeHours = null } = {}) {
  assert(/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repo), "Expected OWNER/REPO");
  const raw = bytesOf(await read(`https://raw.githubusercontent.com/${repo}/main/latest.json`, 65536, { cache: "no-store" }));
  assert(raw.length <= 65536, "Index too large");
  const index = validateIndex(parseJSON(raw), previous?.index, maxAgeHours);
  const files = {};
  for (const path of PATHS) {
    files[path] = previous && index.data_commit === previous.index.data_commit && sameFiles(index, previous.index)
      ? previous.files[path] : await verifiedFile(repo, index, path, read);
  }
  return verifySnapshot(index, files);
}

if (typeof process !== "undefined" && process.versions?.node && process.argv[1]) {
  const { fileURLToPath } = await import("node:url");
  const { resolve, dirname, basename } = await import("node:path");
  if (resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
    const { writeFile, rename, unlink, mkdir } = await import("node:fs/promises");
    let temporary;
    try {
      const [country = "JP", output = "selected.ovpn"] = process.argv.slice(2);
      const snapshot = await loadSnapshot({ repo: process.env.VPNGATE_REPO || undefined });
      const selected = snapshot.servers.find(server => server.country_code === country.toUpperCase());
      assert(selected, "No node for the requested country");
      const target = resolve(output);
      await mkdir(dirname(target), { recursive: true });
      temporary = resolve(dirname(target), `.${basename(target)}.${crypto.randomUUID()}.pending`);
      await writeFile(temporary, decodeConfig(selected), { flag: "wx" });
      await rename(temporary, target);
      temporary = null;
      console.log(`Verified ${snapshot.index.server_count} nodes; fetched ${snapshot.index.fetched_at}; data ${snapshot.index.data_commit}`);
      if (Date.now() - timestamp(snapshot.index.fetched_at) > 3 * 3600000) console.warn("Snapshot is older than 3 hours");
      console.log(`Saved ${output}; configuration was not executed`);
    } catch (error) {
      console.error(`Refresh/export failed: ${error.message}; previous output was retained`);
      process.exitCode = 1;
    } finally {
      if (temporary) await unlink(temporary).catch(() => {});
    }
  }
}
