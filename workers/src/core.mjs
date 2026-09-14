// Pure orchestration; native socket/KV/network adapters are supplied by worker.mjs.
const HEX = /^[0-9a-f]{64}$/;
const SHA = /^[0-9a-f]{40}$/;
const NODE = /^v1:[0-9a-f]{64}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
export const assert = (ok, message) => { if (!ok) throw new Error(message); };
export const roundOf = ms => Math.floor(ms / 21600000);
export const bucketOf = id => parseInt(id.slice(3, 11), 16) % 144;
export function parseWorkerId(value) {
  assert(value === "0" || value === "1" || value === 0 || value === 1, "Invalid WORKER_ID");
  return Number(value);
}
export const stamp = value => {
  assert(typeof value === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(value), "Invalid time");
  const n = Date.parse(value);
  assert(Number.isFinite(n) && new Date(n).toISOString() === value, "Invalid date");
  return n;
};
export async function hash(bytes) {
  return [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))].map(b => b.toString(16).padStart(2, "0")).join("");
}

function ipv4(value) {
  if (!/^(0|[1-9]\d{0,2})(\.(0|[1-9]\d{0,2})){3}$/.test(value)) return null;
  const p = value.split(".").map(Number);
  if (p.some(n => n > 255)) return null;
  return p;
}

export function publicIP(value) {
  if (typeof value !== "string" || value.includes("%")) return false;
  const p = ipv4(value);
  if (p) {
    const [a,b,c] = p;
    return !(a === 0 || a === 10 || a === 127 || a >= 224 ||
      (a === 100 && b >= 64 && b <= 127) || (a === 169 && b === 254) ||
      (a === 172 && b >= 16 && b <= 31) || (a === 192 && (b === 168 ||
        (b === 0 && (c === 0 || c === 2)) || (b === 88 && c === 99))) ||
      (a === 198 && (b === 18 || b === 19 || (b === 51 && c === 100))) ||
      (a === 203 && b === 0 && c === 113));
  }
  // Conservative global IPv6 subset. Reject transition/mapped/special addresses.
  if (!/^[0-9a-f:]+$/.test(value) || value.split("::").length > 2) return false;
  const halves = value.split("::");
  const left = halves[0] ? halves[0].split(":") : [];
  const right = halves.length === 2 && halves[1] ? halves[1].split(":") : [];
  if (![...left, ...right].every(s => /^[0-9a-f]{1,4}$/.test(s))) return false;
  const missing = 8 - left.length - right.length;
  if ((halves.length === 1 && missing !== 0) || (halves.length === 2 && missing < 1)) return false;
  const words = [...left, ...Array(missing).fill("0"), ...right].map(s => parseInt(s,16));
  return words[0] >= 0x2000 && words[0] <= 0x3fff &&
    !(words[0] === 0x2001 && (words[1] < 0x200 || words[1] === 0xdb8)) &&
    words[0] !== 0x2002 && !(words[0] === 0x3fff && words[1] < 0x1000);
}

export function validateTarget(target) {
  assert(target && NODE.test(target.id) && HEX.test(target.config_sha256) && publicIP(target.ip), "Invalid target identity/address");
  assert(Array.isArray(target.endpoints) && target.endpoints.length >= 1 && target.endpoints.length <= 8, "Invalid endpoints");
  assert(target.endpoints.every(e => e.ip === target.ip && Number.isInteger(e.port) && e.port >= 1 && e.port <= 65535), "Invalid endpoint address/port");
  assert(new Set(target.endpoints.map(e => e.port)).size === target.endpoints.length, "Duplicate endpoints");
  stamp(target.last_seen_at);
  if (target.checked_at !== null) stamp(target.checked_at);
  assert(target.last_probe_round === null || (Number.isSafeInteger(target.last_probe_round) && target.last_probe_round >= 0), "Invalid prior round");
  return target;
}

export async function readBytes(url, limit, {fetcher = fetch, timeout = 10000} = {}) {
  const response = await fetcher(url, {redirect: "manual", signal: AbortSignal.timeout(timeout),
    headers: {"Accept": "application/json"}});
  if (response.status !== 200) {
    await response.body?.cancel();
    throw new Error(`Input HTTP ${response.status}`);
  }
  assert(response.body, "Missing input body");
  const reader = response.body.getReader(), chunks = [];
  let count = 0;
  try {
    for (;;) {
      const {done, value} = await reader.read();
      if (done) break;
      count += value.byteLength;
      assert(count <= limit, "Input exceeds limit");
      chunks.push(value);
    }
  } catch (error) { await reader.cancel().catch(() => {}); throw error; }
  finally { reader.releaseLock(); }
  const output = new Uint8Array(count);
  let offset = 0;
  for (const value of chunks) { output.set(value, offset); offset += value.length; }
  return output;
}

const parse = bytes => JSON.parse(new TextDecoder("utf-8", {fatal: true}).decode(bytes));
export async function checkedFile(repo, commit, path, meta, read = readBytes, max = 65536) {
  assert(meta && HEX.test(meta.sha256) && Number.isSafeInteger(meta.bytes) && meta.bytes > 0 && meta.bytes <= max, "Invalid file descriptor");
  const bytes = await read(`https://raw.githubusercontent.com/${repo}/${commit}/${path}`, meta.bytes);
  assert(bytes.byteLength === meta.bytes && await hash(bytes) === meta.sha256, "Input hash mismatch");
  return parse(bytes);
}

export async function loadWork(env, scheduledTime, {read = readBytes, now = Date.now} = {}) {
  const worker = parseWorkerId(env.WORKER_ID), repo = env.REPOSITORY;
  assert(/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repo), "Invalid repository");
  const invokedAt = now();
  assert(Number.isSafeInteger(scheduledTime) && scheduledTime >= 0 &&
    scheduledTime <= invokedAt + 300000 && invokedAt - scheduledTime <= 10800000, "Stale scheduled invocation");
  const slot = Math.floor((scheduledTime % 21600000) / 300000), bucket = worker * 72 + slot;
  const index = parse(await read(`https://raw.githubusercontent.com/${repo}/main/pool/latest.json`, 65536));
  assert(index.kind === "vpngate-pool" && index.schema_version === 1 && SHA.test(index.data_commit), "Unsupported pool index");
  const age = now() - stamp(index.source_fetched_at);
  assert(age >= -300000 && age <= 10800000, "Stale source observations");
  const meta = index.files?.["pool/probe-plan.json"];
  const plan = await checkedFile(repo, index.data_commit, "pool/probe-plan.json", meta, read);
  assert(plan.schema_version === 1 && plan.bucket_count === 144 && plan.source_fetched_at === index.source_fetched_at,
    "Mixed probe plan");
  assert(Array.isArray(plan.controls) && plan.controls.length <= 5, "Invalid controls");
  for (const target of plan.controls) { validateTarget(target); assert(target.endpoints.length === 1 && target.last_seen_at === index.source_fetched_at, "Invalid control"); }
  const common = {controls: plan.controls, worker_id: worker, round: roundOf(now()),
    scheduled_at: new Date(scheduledTime).toISOString(), data_commit: index.data_commit, plan_sha256: meta.sha256};
  if (plan.task_protocol_version !== undefined) {
    assert(plan.task_protocol_version === 2 && plan.task_manifest?.path === "pool/task-plan.json", "Unsupported task protocol");
    const manifest = await checkedFile(repo, index.data_commit, "pool/task-plan.json", plan.task_manifest, read);
    assert(manifest.schema_version === 2 && manifest.normal_interval_seconds === 14400 && manifest.retry_interval_seconds === 3600,
      "Invalid task manifest");
    const taskSlot = Math.floor(scheduledTime / 300000), part = manifest.tasks?.[`${worker}:${taskSlot}`];
    const identity = {...common, task_slot: taskSlot, task_manifest_sha256: plan.task_manifest.sha256};
    // A missing/expired assignment is not permission to probe another slot.
    if (!part) return {...identity, targets: []};
    const path = `pool/tasks/${worker}-${taskSlot}.json`;
    assert(part.path === path, "Invalid task path");
    const task = await checkedFile(repo, index.data_commit, path, part, read);
    assert(task.schema_version === 2 && task.worker_id === worker && task.slot === taskSlot &&
      stamp(task.scheduled_at) === taskSlot * 300000 + (worker === 0 ? 120000 : 240000), "Mixed task identity");
    assert(Array.isArray(task.targets) && task.targets.length <= 35, "Invalid task targets");
    const endpoints = new Set();
    for (const target of task.targets) {
      validateTarget(target); stamp(target.next_probe_at);
      assert(Math.floor(bucketOf(target.id) / 72) === worker, "Wrong task partition");
      target.endpoints.forEach(e => endpoints.add(`${e.ip}:${e.port}`));
    }
    assert(endpoints.size <= 35 && new Set(task.targets.map(t => t.id)).size === task.targets.length, "Oversized/duplicate task");
    return {...identity, round: roundOf(now()), targets: task.targets, task_path: path, task_sha256: part.sha256};
  }
  const part = plan.buckets?.[String(bucket)], path = `pool/probes/${String(bucket).padStart(3,"0")}.json`;
  assert(part?.path === path, "Invalid bucket path");
  const work = await checkedFile(repo, index.data_commit, path, part, read, 262144);
  assert(work.schema_version === 1 && work.bucket === bucket && Array.isArray(work.targets), "Invalid bucket");
  for (const target of work.targets) { validateTarget(target); assert(bucketOf(target.id) === bucket, "Wrong target bucket"); }
  assert(new Set(work.targets.map(t => t.id)).size === work.targets.length, "Duplicate bucket node");
  // Keep the scheduled bucket, but record the actual observation round. A delayed
  // invocation must never manufacture a failure for a round that already ended.
  return {...common, round: roundOf(now()), targets: work.targets, bucket, bucket_sha256: part.sha256};
}

export function classify(error) {
  const message = String(error?.message ?? error).toLowerCase();
  if (/prohibit|disallow|proxy request failed|loop detected|resource|limit/.test(message)) return "platform";
  return /\b(econnrefused|connection refused)\b/.test(message) ? "refused" : "platform";
}

export async function connectOnce(endpoint, connect,
  {timeout = 3000, now = Date.now, closeTimeout = 1000, onUnclosed = () => {}} = {}) {
  const base = {ip: endpoint.ip, port: endpoint.port};
  if (!publicIP(endpoint.ip) || !Number.isInteger(endpoint.port) || endpoint.port < 1 || endpoint.port > 65535 || endpoint.port === 25)
    return {...base, status: "unknown", error: "unsupported", connect_ms: null};
  let socket, timer, result, closed;
  const started = now();
  try {
    socket = connect({hostname: endpoint.ip, port: endpoint.port}, {secureTransport: "off", allowHalfOpen: false});
    // A fulfilled closed promise also confirms closure when close() is slow or
    // rejects. Rejection alone does not prove that a connection slot is free.
    closed = new Promise(resolve => { socket.closed.then(() => resolve(true), () => {}); });
    const deadline = new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("probe_timeout")), timeout); });
    await Promise.race([socket.opened, deadline]);
    result = {...base, status: "reachable", error: null, connect_ms: Math.max(0, Math.min(3000, Math.round(now() - started)))};
  } catch (error) {
    const reason = error?.message === "probe_timeout" ? "timeout" : classify(error);
    result = {...base, status: reason === "platform" ? "unknown" : "unreachable", error: reason, connect_ms: null};
  } finally {
    clearTimeout(timer);
    if (socket) {
      // workerd may wait for opened before close() releases the connection. A
      // timed-out close must keep this lane reserved, without stopping others.
      let closingTimer;
      let confirmed = false;
      let closeRejected = false;
      const released = Promise.race([closed,
          Promise.resolve().then(() => socket.close()).then(() => true, () => {
            closeRejected = true;
            return closed;
          })]);
      try {
        confirmed = await Promise.race([released,
          new Promise(resolve => { closingTimer = setTimeout(() => resolve(false), closeTimeout); })]);
      }
      finally { clearTimeout(closingTimer); }
      if (!confirmed) {
        onUnclosed(released);
        // A completed handshake remains evidence of reachability, regardless of
        // cleanup. Keep ambiguous failures conservative when closure is unknown.
        if (result.status !== "reachable") result = {...base, status: "unknown", error: "platform", connect_ms: null};
      }
      result.close_confirmed = confirmed;
      result.cleanup_error = confirmed ? null : closeRejected ? "close_rejected" : "close_timeout";
    }
  }
  return result;
}

function outcome(target, results) {
  const endpoints = target.endpoints.map(e => results.get(`${e.ip}:${e.port}`) ?? {...e, status: "unknown", error: "budget", connect_ms: null});
  return {id: target.id, config_sha256: target.config_sha256, endpoints,
    status: endpoints.some(e => e.status === "reachable") ? "reachable" :
      endpoints.every(e => e.status === "unreachable") ? "unreachable" : "unknown"};
}

export async function runBatch(work, connect, {now = Date.now, attempt = connectOnce, budget = 45000, maxTargets = 40,
  recoveryWait = 5000} = {}) {
  assert(Number.isInteger(maxTargets) && maxTargets >= 1 && maxTargets <= 40, "Invalid target rollout limit");
  const started = now(), executionRound = roundOf(started), chosen = [], jobs = new Map();
  const add = target => {
    const extra = target.endpoints.filter(e => !jobs.has(`${e.ip}:${e.port}`));
    if (jobs.size + extra.length > 40) return false;
    extra.forEach(e => jobs.set(`${e.ip}:${e.port}`, e));
    return true;
  };
  const packed = work.task_slot !== undefined;
  const due = packed ? work.targets.filter(t => stamp(t.next_probe_at) <= now()) :
    work.targets.filter(t => t.last_probe_round === null || t.last_probe_round < executionRound)
      .sort((a,b) => (a.checked_at ?? "").localeCompare(b.checked_at ?? "") || a.id.localeCompare(b.id));
  if (!due.length) return null;
  work.controls.forEach(add);
  for (const target of due) if (chosen.length < maxTargets && add(target)) chosen.push(target);
  const queue = [...jobs.values()], results = new Map();
  let next = 0, unclosedSockets = 0, recoveredSockets = 0;
  await Promise.all(Array.from({length: 4}, async () => {
    let retired = false, released;
    const onUnclosed = confirmation => { if (!retired) { retired = true; released = confirmation; unclosedSockets++; } };
    while (next < queue.length && now() - started <= budget - 4000) {
      if (retired) {
        // Only positive close/closed fulfillment can make this slot reusable.
        // Bounded recovery never consumes the last four seconds of probe budget.
        if (!released || recoveryWait <= 0) break;
        let timer;
        const confirmed = await Promise.race([released,
          new Promise(resolve => { timer = setTimeout(() => resolve(false),
            Math.min(recoveryWait, Math.max(0, budget - 4000 - (now() - started)))); })]);
        clearTimeout(timer);
        if (!confirmed) break;
        retired = false; unclosedSockets--; recoveredSockets++;
        if (next >= queue.length || now() - started > budget - 4000) break;
      }
      const endpoint = queue[next++];
      const result = await attempt(endpoint, connect, {now, onUnclosed});
      results.set(`${endpoint.ip}:${endpoint.port}`, result);
    }
  }));
  const targetResults = chosen.map(t => outcome(t, results));
  const controls = work.controls.map(t => outcome(t, results));
  const complete = targetResults.filter(r => r.status !== "unknown");
  const guarded = !controls.some(r => r.status === "reachable") ||
    (complete.length >= 10 && complete.filter(r => r.status === "unreachable").length / complete.length >= .8);
  const identity = packed ? {task_slot: work.task_slot, task_path: work.task_path,
    task_sha256: work.task_sha256, task_manifest_sha256: work.task_manifest_sha256} :
    {bucket: work.bucket, bucket_sha256: work.bucket_sha256};
  return {schema_version: packed ? 2 : 1, batch_id: crypto.randomUUID(), worker_id: work.worker_id, ...identity, round: executionRound,
    scheduled_at: work.scheduled_at,
    data_commit: work.data_commit, plan_sha256: work.plan_sha256,
    started_at: new Date(started).toISOString(), finished_at: new Date(now()).toISOString(),
    results: targetResults, controls, guarded, completed: complete.length,
    stop_reason: next < queue.length && unclosedSockets === 4 ? "socket_close_unconfirmed" : null,
    budget_exhausted: next < queue.length && unclosedSockets < 4,
    unclosed_sockets: unclosedSockets, recovered_sockets: recoveredSockets, attempted_endpoints: results.size,
    endpoint_error_counts: queue.reduce((counts, e) => {
      const error = results.get(`${e.ip}:${e.port}`)?.error ?? (results.has(`${e.ip}:${e.port}`) ? null : "budget");
      if (error) counts[error] = (counts[error] ?? 0) + 1;
      return counts;
    }, {}),
    deferred: due.length - chosen.length + targetResults.filter(r => r.endpoints.some(e => e.error === "budget")).length};
}

export async function scheduled(controller, env, connect, dependencies = {}) {
  const work = await loadWork(env, controller.scheduledTime, dependencies);
  const batch = await runBatch(work, connect, {...dependencies, maxTargets: Number(env.MAX_TARGETS_PER_RUN ?? 40)});
  if (!batch) return {status: "no_due_targets", bucket: work.bucket, task_slot: work.task_slot};
  const body = JSON.stringify(batch);
  assert(new TextEncoder().encode(body).length <= 65536, "Result batch exceeds limit");
  await env.RESULTS.put(`results/${batch.round}/${batch.batch_id}`, body, {expirationTtl: 259200});
  return {status: "stored", schema_version: batch.schema_version, batch_id: batch.batch_id,
    bucket: batch.bucket, task_slot: batch.task_slot, completed: batch.completed,
    deferred: batch.deferred, guarded: batch.guarded, stop_reason: batch.stop_reason, unclosed_sockets: batch.unclosed_sockets,
    recovered_sockets: batch.recovered_sockets, budget_exhausted: batch.budget_exhausted, attempted_endpoints: batch.attempted_endpoints,
    endpoint_error_counts: batch.endpoint_error_counts};
}

async function authorized(request, secret) {
  if (typeof secret !== "string" || secret.length < 32) return false;
  const expected = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(`Bearer ${secret}`)));
  const supplied = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(request.headers.get("Authorization") ?? "")));
  let difference = 0;
  for (let i = 0; i < expected.length; i++) difference |= expected[i] ^ supplied[i];
  return difference === 0;
}

export async function serve(request, env, now = Date.now) {
  const response = (value, status = 200) => Response.json(value, {status, headers: {"Cache-Control": "no-store"}});
  let worker;
  try { worker = parseWorkerId(env.WORKER_ID); }
  catch { return response({error: "invalid_worker_id"}, 500); }
  if (worker !== 0) return response({error: "not_found"}, 404);
  if (!await authorized(request, env.PROBE_READ_TOKEN)) return response({error: "unauthorized"}, 401);
  if (request.method !== "GET") return response({error: "method_not_allowed"}, 405);
  const url = new URL(request.url);
  if (url.pathname === "/v1/batches") {
    const value = url.searchParams.get("round"), cursor = url.searchParams.get("cursor") || undefined;
    if (!/^\d{1,10}$/.test(value ?? "") || ![roundOf(now()), roundOf(now()) - 1].includes(Number(value)) || (cursor?.length ?? 0) > 2048)
      return response({error: "invalid_round_or_cursor"}, 400);
    const listing = await env.RESULTS.list({prefix: `results/${value}/`, limit: 200, cursor});
    return response({batches: listing.keys.map(k => ({key: k.name})), list_complete: listing.list_complete, cursor: listing.cursor ?? ""});
  }
  const match = /^\/v1\/batch\/results\/(\d{1,10})\/([^/]+)$/.exec(url.pathname);
  if (match && UUID.test(match[2]) && [roundOf(now()), roundOf(now()) - 1].includes(Number(match[1]))) {
    const value = await env.RESULTS.get(`results/${match[1]}/${match[2]}`);
    return value === null ? response({error: "not_visible"}, 404) :
      new Response(value, {headers: {"Content-Type": "application/json", "Cache-Control": "no-store"}});
  }
  return response({error: "not_found"}, 404);
}
