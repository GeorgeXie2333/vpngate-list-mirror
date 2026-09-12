import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {spawnSync} from "node:child_process";
import {fileURLToPath} from "node:url";
import {hash} from "../src/core.mjs";

test("the published dashboard file matches its source modules",()=>{
  const result=spawnSync(process.execPath,[fileURLToPath(new URL("../build-dashboard.mjs",import.meta.url)),"--check"],{encoding:"utf8"});
  assert.equal(result.status,0,result.stderr);
});

async function dashboard() {
  const source=readFileSync(new URL("../worker-dashboard.js",import.meta.url),"utf8");
  const adapted=source.replace('import {connect} from "cloudflare:sockets";',
    'let socketCalls=0; const connect=()=>{socketCalls++; return {opened:Promise.resolve(),closed:Promise.resolve(),close:async()=>{}};};');
  return import(`data:text/javascript;base64,${Buffer.from(adapted+'\nexport {socketCalls};').toString("base64")}`);
}

test("standalone scheduled entry logs progress and writes KV with numeric worker ID",async t=>{
  const instant=Date.now(), fetchedAt=new Date(instant).toISOString();
  const bucketNumber=Math.floor((instant%21600000)/300000);
  const bytes=value=>new TextEncoder().encode(JSON.stringify(value));
  const target={id:`v1:${bucketNumber.toString(16).padStart(8,"0")}${"0".repeat(56)}`,
    config_sha256:"b".repeat(64),ip:"8.8.8.8",endpoints:[{ip:"8.8.8.8",port:443}],
    last_seen_at:fetchedAt,last_probe_round:null,checked_at:null};
  const bucket=bytes({schema_version:1,bucket:bucketNumber,targets:[target]});
  const path=`pool/probes/${String(bucketNumber).padStart(3,"0")}.json`;
  const plan=bytes({schema_version:1,bucket_count:144,source_fetched_at:fetchedAt,controls:[target],
    buckets:{[bucketNumber]:{path,bytes:bucket.length,sha256:await hash(bucket)}}});
  const index=bytes({kind:"vpngate-pool",schema_version:1,data_commit:"a".repeat(40),source_fetched_at:fetchedAt,
    files:{"pool/probe-plan.json":{bytes:plan.length,sha256:await hash(plan)}}});
  const files=new Map([
    ["https://raw.githubusercontent.com/owner/repo/main/pool/latest.json",index],
    [`https://raw.githubusercontent.com/owner/repo/${"a".repeat(40)}/pool/probe-plan.json`,plan],
    [`https://raw.githubusercontent.com/owner/repo/${"a".repeat(40)}/${path}`,bucket]
  ]);
  t.mock.method(globalThis,"fetch",async url=>{assert.ok(files.has(url),url);return new Response(files.get(url));});
  const logs=[],writes=[];
  t.mock.method(console,"log",text=>logs.push(JSON.parse(text)));
  const worker=await dashboard();
  const before=worker.socketCalls;
  await worker.default.scheduled({scheduledTime:instant,cron:"2-57/5 * * * *"},
    {WORKER_ID:0,REPOSITORY:"owner/repo",MAX_TARGETS_PER_RUN:"2",RESULTS:{put:async(...args)=>writes.push(args)}});
  assert.equal(worker.socketCalls-before,1);
  assert.deepEqual(logs.map(row=>row.status),["started","stored"]);
  assert.equal(writes.length,1);
  assert.equal(writes[0][2].expirationTtl,72*3600);
  const batch=JSON.parse(writes[0][1]);
  assert.equal(batch.worker_id,0);
  assert.equal(batch.results[0].status,"reachable");
  assert.equal(batch.stop_reason,null);
});

test("standalone entry reports failure before any socket or KV call",async t=>{
  const logs=[],errors=[];
  t.mock.method(console,"log",text=>logs.push(JSON.parse(text)));
  t.mock.method(console,"error",text=>errors.push(JSON.parse(text)));
  t.mock.method(globalThis,"fetch",async()=>new Response(null,{status:503}));
  const worker=await dashboard(),before=worker.socketCalls;
  await assert.rejects(worker.default.scheduled({scheduledTime:Date.now()},
    {WORKER_ID:"0",REPOSITORY:"owner/repo",RESULTS:{put:()=>assert.fail("failed input must not write")}}),/Input HTTP 503/);
  assert.equal(logs[0].status,"started");
  assert.deepEqual(errors,[{status:"failed",error:"Input HTTP 503"}]);
  assert.equal(worker.socketCalls,before);
});
