// Anonymous pool consumer for HTTPS browsers and Node.js 22+.
import {readBytes, parseJSON, digest, decodeConfig} from "./consume.mjs";
const paths = ["pool/servers.json", "pool/countries.json"];
const hex = /^[0-9a-f]{64}$/;
const check = (ok, message) => { if (!ok) throw new Error(message); };
const integer = (x, min, max) => Number.isSafeInteger(x) && x >= min && x <= max;
const timestamp = value => {
  check(typeof value === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(value), "Invalid pool time");
  const time = Date.parse(value);
  check(Number.isFinite(time) && new Date(time).toISOString() === value, "Invalid pool date");
  return time;
};

export function validatePoolIndex(index, previous = null, maxAgeHours = null) {
  check(index?.kind === "vpngate-pool" && index.schema_version === 1, "Unsupported pool schema");
  check(index.source_url === "https://www.vpngate.net/api/iphone/" && /^[0-9a-f]{40}$/.test(index.data_commit), "Invalid pool source/commit");
  const fetched = timestamp(index.source_fetched_at);
  check(timestamp(index.index_generated_at) >= Math.max(fetched, timestamp(index.generated_at)), "Invalid generation times");
  check(fetched <= Date.now() + 300000, "Future pool index");
  if (maxAgeHours !== null) check(Date.now() - fetched <= maxAgeHours * 3600000, "Pool is too old");
  check(integer(index.server_count, 0, 5000), "Invalid pool count");
  for (const path of [...paths, "pool/probe-plan.json", "pool/state.json"]) {
    const meta = index.files?.[path];
    check(meta && hex.test(meta.sha256) && integer(meta.bytes, 1, path === "pool/probe-plan.json" ? 65536 : 16777216), "Invalid file descriptor");
  }
  if (previous) {
    const old = timestamp(previous.source_fetched_at);
    check(fetched >= old, "Refusing older pool index");
    if (fetched === old) check(index.data_commit === previous.data_commit &&
      paths.every(p => index.files[p].sha256 === previous.files[p].sha256 && index.files[p].bytes === previous.files[p].bytes), "Conflicting pool index");
  }
  return index;
}

async function download(repo, index, path, meta, read) {
  for (const url of [`https://cdn.jsdelivr.net/gh/${repo}@${index.data_commit}/${path}`,
    `https://raw.githubusercontent.com/${repo}/${index.data_commit}/${path}`]) {
    try {
      const bytes = new Uint8Array(await read(url, meta.bytes));
      check(bytes.length === meta.bytes && await digest(bytes) === meta.sha256, "Pool integrity mismatch");
      return bytes;
    } catch { /* Retry the same immutable version through Raw. */ }
  }
  throw new Error(`CDN and same-commit Raw failed: ${path}`);
}

export async function verifyPoolCatalog(index, files) {
  validatePoolIndex(index);
  for (const path of paths) check(files[path]?.length === index.files[path].bytes &&
    await digest(files[path]) === index.files[path].sha256, "Catalog integrity mismatch");
  const catalog = parseJSON(files[paths[0]]), grouped = parseJSON(files[paths[1]]);
  for (const data of [catalog, grouped]) check(data.kind === "vpngate-pool" && data.schema_version === 1 &&
    data.source_fetched_at === index.source_fetched_at && data.server_count === index.server_count, "Mixed pool files");
  check(Array.isArray(catalog.servers) && catalog.servers.length === index.server_count, "Pool count mismatch");
  const groups = new Map(), configs = new Map();
  let lastId = "";
  for (const row of catalog.servers) {
    check(/^v1:[0-9a-f]{64}$/.test(row.id) && row.id > lastId, "Unsorted or duplicate pool ID");
    lastId = row.id;
    check(typeof row.hostname === "string" && row.hostname.length <= 254 && typeof row.ip === "string", "Invalid host/IP");
    const host = row.hostname.trim().toLowerCase().replace(/\.$/, "");
    check(host && host.split(".").every(s => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(s)), "Invalid hostname");
    if (row.ip.includes(":")) check(new URL(`http://[${row.ip}]/`).hostname.slice(1,-1) === row.ip, "Invalid IPv6");
    else check(row.ip.split(".").length === 4 && row.ip.split(".").every(s => /^(0|[1-9]\d{0,2})$/.test(s) && +s <= 255), "Invalid IPv4");
    check(row.id === "v1:" + await digest(new TextEncoder().encode(`vpngate-node-v1\0${host}\0${row.ip}`)), "Pool ID mismatch");
    check(timestamp(row.first_seen_at) <= timestamp(row.last_seen_at) && timestamp(row.last_seen_at) <= timestamp(index.source_fetched_at), "Invalid observation times");
    check(typeof row.present_in_latest_source === "boolean" && (!row.present_in_latest_source || row.last_seen_at === index.source_fetched_at), "Invalid presence");
    check(row.country_code === null || /^[A-Z]{2}$/.test(row.country_code), "Invalid country");
    check(row.country_name === null || typeof row.country_name === "string", "Invalid country name");
    for (const key of ["score", "ping_ms", "speed_bps", "num_vpn_sessions"]) check(row[key] === null || integer(row[key],0,Number.MAX_SAFE_INTEGER), "Invalid upstream metric");
    check(hex.test(row.openvpn_config_sha256) && integer(row.openvpn_config_bytes, 1, 131072), "Invalid config content descriptor");
    check(row.config?.path === `pool/configs/${row.openvpn_config_sha256}.json` && hex.test(row.config.sha256) && integer(row.config.bytes,1,176000), "Invalid config reference");
    const prior = configs.get(row.config.path);
    check(!prior || prior.sha256 === row.config.sha256 && prior.bytes === row.config.bytes, "Conflicting config reference");
    configs.set(row.config.path, row.config);
    const probe = row.tcp_probe;
    check(probe?.probe_source === "cloudflare_workers" && ["reachable","unreachable","unknown","not_applicable"].includes(probe.status) &&
      integer(probe.consecutive_failures,0,10000), "Invalid TCP status");
    for (const key of ["checked_at","last_success_at"]) if (probe[key] !== null) check(timestamp(probe[key]) <= timestamp(index.index_generated_at) + 300000, "Future probe");
    for (const key of ["round","last_failure_round","worker_id","connect_ms"]) check(probe[key] === null ||
      integer(probe[key],0,key === "worker_id" ? 1 : key === "connect_ms" ? 3000 : 1e9), "Invalid probe number");
    check((probe.checked_at === null) === (probe.round === null) && (probe.round === null) === (probe.worker_id === null), "Incomplete probe identity");
    check(probe.last_success_at === null || probe.checked_at !== null && timestamp(probe.last_success_at) <= timestamp(probe.checked_at), "Invalid success time");
    check(!probe.consecutive_failures || probe.last_failure_round === probe.round && probe.status === "unreachable", "Invalid failure round");
    check(Array.isArray(row.probe_targets) && row.probe_targets.length <= 8 && row.probe_targets.every(e => e.ip === row.ip && integer(e.port,1,65535)), "Invalid probe endpoints");
    check((probe.status === "reachable") === (probe.connected_endpoint !== null && probe.connect_ms !== null), "Invalid connected endpoint");
    check(probe.connected_endpoint === null || row.probe_targets.some(e => e.ip === probe.connected_endpoint.ip && e.port === probe.connected_endpoint.port), "Unlisted connected endpoint");
    const group = groups.get(row.country_code) ?? {count:0,names:new Set()};
    group.count++; if (row.country_name) group.names.add(row.country_name);
    groups.set(row.country_code, group);
  }
  check([...configs.values()].reduce((n,c) => n+c.bytes, 0) <= 67108864, "Pool configuration capacity exceeded");
  check(Array.isArray(grouped.countries) && grouped.countries.length === groups.size, "Country count mismatch");
  for (const group of grouped.countries) {
    const expected = groups.get(group.code);
    check(expected && expected.count === group.server_count && JSON.stringify([...expected.names].sort()) === JSON.stringify(group.names), "Country totals mismatch");
    groups.delete(group.code);
  }
  return {index, files, servers:catalog.servers, countries:grouped.countries};
}

export async function loadPool({repo = "GeorgeXie2333/vpngate-list-mirror", previous = null, read = readBytes, maxAgeHours = null} = {}) {
  check(/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repo), "Invalid repository");
  const bytes = await read(`https://raw.githubusercontent.com/${repo}/main/pool/latest.json`,65536,{cache:"no-store"});
  check(bytes.length <= 65536, "Oversized index");
  const index = validatePoolIndex(parseJSON(bytes),previous?.index,maxAgeHours), files = {};
  for (const path of paths) files[path] = previous && previous.index.files[path].sha256 === index.files[path].sha256 &&
    previous.index.files[path].bytes === index.files[path].bytes ? previous.files[path] : await download(repo,index,path,index.files[path],read);
  return verifyPoolCatalog(index,files);
}

export async function loadPoolConfig(pool, row, {repo = "GeorgeXie2333/vpngate-list-mirror", read = readBytes} = {}) {
  check(/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repo), "Invalid repository");
  check(pool.servers.includes(row), "Select a node from the verified pool");
  const body = await download(repo,pool.index,row.config.path,row.config,read), config = parseJSON(body);
  check(config.kind === "vpngate-pool-config" && config.schema_version === 1, "Unsupported config schema");
  const decoded = decodeConfig(config);
  check(decoded.length === row.openvpn_config_bytes && decoded.length === config.openvpn_config_bytes &&
    await digest(decoded) === row.openvpn_config_sha256 && config.openvpn_config_sha256 === row.openvpn_config_sha256, "Configuration integrity mismatch");
  return decoded;
}

if (typeof process !== "undefined" && process.versions?.node && process.argv[1]) {
  const {fileURLToPath} = await import("node:url"), {resolve,dirname,basename} = await import("node:path");
  if (resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
    const {writeFile,rename,unlink,mkdir} = await import("node:fs/promises");
    let pending;
    try {
      const [country="JP",output="selected.ovpn"] = process.argv.slice(2);
      const repo = process.env.VPNGATE_REPO || "GeorgeXie2333/vpngate-list-mirror";
      const pool = await loadPool({repo});
      const node = pool.servers.filter(n => n.country_code === country.toUpperCase()).sort((a,b) => b.last_seen_at.localeCompare(a.last_seen_at))[0];
      check(node,"No matching country");
      const config = await loadPoolConfig(pool,node,{repo}), target = resolve(output);
      await mkdir(dirname(target),{recursive:true});
      pending = resolve(dirname(target),`.${basename(target)}.${crypto.randomUUID()}.pending`);
      await writeFile(pending,config,{flag:"wx"}); await rename(pending,target); pending = null;
      console.log(`Saved ${output}; node last seen ${node.last_seen_at}; TCP ${node.tcp_probe.status}; no VPN started`);
    } catch (error) { console.error(error.message); process.exitCode = 1; }
    finally { if (pending) await unlink(pending).catch(() => {}); }
  }
}
