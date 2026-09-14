import test from "node:test";
import assert from "node:assert/strict";
import {publicIP, validateTarget, connectOnce, classify, runBatch, loadWork, scheduled,
  serve, hash, roundOf, readBytes} from "../src/core.mjs";

const now = Date.parse("2026-09-01T00:02:00.000Z");
const iso = n => new Date(n).toISOString();
const bytes = o => new TextEncoder().encode(JSON.stringify(o));
const id = n => `v1:${n.toString(16).padStart(8,"0")}${"0".repeat(56)}`;
function target(n=0, ports=[443]) {
  return {id:id(n), config_sha256:"b".repeat(64), ip:"8.8.8.8", endpoints:ports.map(port=>({ip:"8.8.8.8",port})),
    last_seen_at:iso(now), last_probe_round:null, checked_at:null};
}
function work(targets=[target()], controls=[target(1,[444])]) {
  return {worker_id:0,bucket:0,round:roundOf(now),data_commit:"a".repeat(40),plan_sha256:"b".repeat(64),
    bucket_sha256:"c".repeat(64),targets,controls};
}
const success = async e => ({...e,status:"reachable",error:null,connect_ms:5});
const failure = async e => ({...e,status:"unreachable",error:"timeout",connect_ms:null});
async function inputs(targets=[target()], {bucketNumber=0}={}) {
  const bucket=bytes({schema_version:1,bucket:bucketNumber,targets});
  const plan=bytes({schema_version:1,bucket_count:144,source_fetched_at:iso(now),controls:[target(1,[444])],
    buckets:{[bucketNumber]:{path:`pool/probes/${String(bucketNumber).padStart(3,"0")}.json`,bytes:bucket.length,sha256:await hash(bucket)}}});
  const index=bytes({kind:"vpngate-pool",schema_version:1,data_commit:"a".repeat(40),source_fetched_at:iso(now),
    files:{"pool/probe-plan.json":{bytes:plan.length,sha256:await hash(plan)}}});
  const calls=[];
  const read=async url=>{calls.push(url); return url.endsWith("latest.json")?index:url.endsWith("probe-plan.json")?plan:bucket;};
  return {read,calls,index,plan,bucket};
}

test("numeric public addresses only, no transition or private ranges",()=>{
  for(const ip of ["8.8.8.8","1.1.1.1","2606:4700:4700::1111"]) assert.equal(publicIP(ip),true,ip);
  for(const ip of ["127.0.0.1","10.0.0.1","172.31.2.1","192.168.1.1","169.254.169.254","100.64.0.1",
    "192.0.2.1","224.0.0.1","255.255.255.255","1.01.1.1","::1","::ffff:8.8.8.8","2001:db8::1",
    "2002:808:808::1","fe80::1%eth0","example.org","1:2:3:4:5:6:7:8:9",null]) assert.equal(publicIP(ip),false,String(ip));
  assert.throws(()=>validateTarget({...target(),endpoints:[{ip:"1.1.1.1",port:443}]}));
});

test("socket opened is awaited and every socket is closed, including timeout rejections",async()=>{
  let closes=0;
  const socket={opened:Promise.resolve(),closed:Promise.resolve(),close:async()=>{closes++;}};
  assert.equal((await connectOnce(target().endpoints[0],()=>socket)).status,"reachable");
  const result=await connectOnce(target().endpoints[0],()=>({opened:new Promise(()=>{}),closed:Promise.reject(new Error("closed")),
    close:async()=>{closes++;throw new Error("close failed");}}),{timeout:5,closeTimeout:5});
  assert.equal(result.error,"platform");
  assert.equal(result.status,"unknown");
  assert.equal(result.cleanup_error,"close_rejected");
  assert.equal(closes,2);
  const timeout=await connectOnce(target().endpoints[0],()=>({opened:new Promise(()=>{}),closed:Promise.resolve(),close:async()=>{}}),{timeout:5});
  assert.equal(timeout.error,"timeout");
  assert.equal(timeout.status,"unreachable");
  const refused=await connectOnce(target().endpoints[0],()=>{throw new Error("ECONNREFUSED");});
  assert.equal(refused.status,"unreachable");
  const blocked=await connectOnce(target().endpoints[0],()=>{throw new Error("proxy request failed, cannot connect");});
  assert.equal(blocked.status,"unknown");
  assert.equal(classify(new Error("unknown internal exception")),"platform");
  assert.equal(classify(new Error("resource limit: connection refused")),"platform");
});

test("a confirmed handshake survives slow or rejected cleanup",async()=>{
  for(const reject of [false,true]) {
    let retired=0;
    const socket={opened:Promise.resolve(),closed:new Promise(()=>{}),
      close:()=>reject?Promise.reject(new Error("close failed")):new Promise(()=>{})};
    const result=await connectOnce(target().endpoints[0],()=>socket,{closeTimeout:5,onUnclosed:()=>retired++});
    assert.equal(result.status,"reachable");
    assert.equal(result.error,null);
    assert.equal(result.close_confirmed,false);
    assert.equal(result.cleanup_error,reject?"close_rejected":"close_timeout");
    assert.equal(retired,1);
  }
});

test("closure after the former 100 ms deadline is accepted within the new grace period",async()=>{
  let retired=0,closes=0;
  const result=await connectOnce(target().endpoints[0],()=>({opened:Promise.resolve(),closed:new Promise(()=>{}),
    close:()=>new Promise(resolve=>setTimeout(()=>{closes++;resolve();},150))}),{onUnclosed:()=>retired++});
  assert.equal(result.status,"reachable");
  assert.equal(result.close_confirmed,true);
  assert.equal(result.cleanup_error,null);
  assert.equal(retired,0);
  assert.equal(closes,1);
});

test("a fulfilled closed promise confirms release despite rejected close; late rejections are handled",async()=>{
  let closeCalls=0,resolveClosed,rejectClose;
  const closed=new Promise(resolve=>{resolveClosed=resolve;});
  const closing=new Promise((resolve,reject)=>{rejectClose=reject;});
  const socket={opened:Promise.resolve(),closed,close:()=>{closeCalls++;resolveClosed();return closing;}};
  const result=await connectOnce(target().endpoints[0],()=>socket,{onUnclosed:()=>assert.fail("closed slot must be reusable")});
  assert.equal(result.close_confirmed,true);
  assert.equal(closeCalls,1);
  rejectClose(new Error("late close rejection"));
  await new Promise(resolve=>setImmediate(resolve));
  const rejected=await connectOnce(target().endpoints[0],()=>({opened:Promise.resolve(),closed:Promise.resolve(),
    close:()=>Promise.reject(new Error("already closed"))}),{onUnclosed:()=>assert.fail()});
  assert.equal(rejected.status,"reachable");
  assert.equal(rejected.close_confirmed,true);
});

test("unknown/private targets and port 25 create zero sockets",async()=>{
  const connect=()=>assert.fail("must not connect");
  for(const e of [{ip:"127.0.0.1",port:443},{ip:"example.com",port:443},{ip:"8.8.8.8",port:25}])
    assert.equal((await connectOnce(e,connect)).status,"unknown");
});

test("40 endpoint calls include controls, at most four concurrent, oldest targets first",async()=>{
  let active=0,peak=0,calls=[];
  const targets=Array.from({length:60},(_,n)=>target(n,[1000+n]));
  targets[0].checked_at=iso(now-1000);
  const attempt=async e=>{active++;peak=Math.max(peak,active);calls.push(e.port);
    await new Promise(resolve=>setTimeout(resolve,1));active--;return success(e);};
  const batch=await runBatch(work(targets,Array.from({length:5},(_,n)=>target(100+n,[2000+n]))),null,{now:()=>now,attempt});
  assert.equal(calls.length,40);assert.equal(peak,4);assert.equal(batch.results.length,35);
  assert.equal(batch.deferred,25);assert.equal(batch.results.some(r=>r.id===id(0)),false);
  assert.equal(batch.guarded,false);
});

test("four pending socket closures exhaust lanes and leave room for the KV write",async t=>{
  for(const settle of ["resolve","reject"]) await t.test(settle,async()=>{
    let active=0,peak=0,kvWrites=0;
    const release=[];
    const connect=()=>{
      active++;peak=Math.max(peak,active);
      let openedResolve,openedReject,closedResolve,closedReject;
      const opened=new Promise((resolve,reject)=>{openedResolve=resolve;openedReject=reject;});
      const closed=new Promise((resolve,reject)=>{closedResolve=resolve;closedReject=reject;});
      release.push(()=>settle==="resolve"?openedResolve():openedReject(new Error("late connect rejection")));
      // Match workerd: close waits for the pending connection attempt.
      return {opened,closed,close:()=>opened.then(()=>{active--;closedResolve();},error=>{
        active--;closedReject(error);throw error;
      })};
    };
    const data=await inputs(Array.from({length:12},(_,n)=>target(n*144,[1000+n])));
    const env={WORKER_ID:"0",REPOSITORY:"owner/repo",RESULTS:{put:async(key,body)=>{
      assert.ok(active<=4,"KV needs a free connection slot");
      kvWrites++;
      const batch=JSON.parse(body);
      assert.equal(batch.results.every(r=>r.status==="unknown"),true);
      assert.equal(batch.deferred,9); // one control plus three targets used the four lanes
      assert.equal(batch.stop_reason,"socket_close_unconfirmed");
      assert.equal(batch.attempted_endpoints,4);
      assert.deepEqual(batch.endpoint_error_counts,{platform:4,budget:9});
    }}};
    try {
      const result=await scheduled({scheduledTime:now},env,connect,{now:()=>now,read:data.read,
        recoveryWait:0,
        attempt:(endpoint,socketConnect,options)=>connectOnce(endpoint,socketConnect,{...options,timeout:5,closeTimeout:5})});
      assert.equal(result.status,"stored");
      assert.equal(result.unclosed_sockets,4);
      assert.equal(peak,4);
      assert.equal(kvWrites,1);
    } finally {
      release.forEach(finish=>finish());
      await new Promise(resolve=>setImmediate(resolve));
    }
    assert.equal(active,0);
  });
});

test("rejected close retires its lane without erasing a confirmed handshake",async()=>{
  let connections=0;
  const batch=await runBatch(work(Array.from({length:12},(_,n)=>target(n,[1000+n])),[]),()=>{
    connections++;
    return {opened:Promise.resolve(),closed:new Promise(()=>{}),close:async()=>{throw new Error("close failed");}};
  },{recoveryWait:0,attempt:(e,connect,options)=>connectOnce(e,connect,{...options,closeTimeout:5})});
  assert.equal(connections,4);
  assert.equal(batch.unclosed_sockets,4);
  assert.equal(batch.results.filter(r=>r.status==="reachable").length,4);
  assert.equal(batch.results.filter(r=>r.status==="unknown").length,8);
  assert.equal(batch.deferred,8);
  assert.equal(batch.stop_reason,"socket_close_unconfirmed");
});

test("one stuck socket reserves only its own lane while other lanes finish the bucket",async()=>{
  let active=0,peak=0,release,calls=0,callsAtRetirement=0;
  let retireLane;
  const laneRetired=new Promise(resolve=>{retireLane=resolve;});
  const connect=({port})=>{
    active++;calls++;peak=Math.max(peak,active);
    let resolveClosed;
    const closed=new Promise(resolve=>{resolveClosed=resolve;});
    if(port===1000) {
      const opened=new Promise(resolve=>{release=resolve;});
      return {opened,closed,close:()=>opened.then(()=>{active--;resolveClosed();})};
    }
    return {opened:Promise.resolve(),closed,close:()=>{
      active--;resolveClosed();return Promise.resolve();
    }};
  };
  try {
    const batch=await runBatch(work(Array.from({length:35},(_,n)=>target(n,[1000+n]))),connect,{
      now:()=>now,recoveryWait:0,
      attempt:async(endpoint,connect,options)=>{
        // Gate healthy lanes on retirement, not on competing wall-clock timers.
        if(endpoint.port!==1000)await laneRetired;
        return connectOnce(endpoint,connect,{...options,timeout:5,closeTimeout:5,
          onUnclosed:confirmation=>{
            callsAtRetirement=calls;options.onUnclosed(confirmation);retireLane();
          }});
      }});
    assert.equal(peak,4);
    assert.equal(active,1);
    assert.ok(calls>callsAtRetirement,"other lanes must continue after the stuck lane retires");
    assert.equal(batch.unclosed_sockets,1);
    assert.equal(batch.attempted_endpoints,36);
    assert.equal(batch.results.filter(r=>r.status==="reachable").length,34);
    assert.equal(batch.results.filter(r=>r.status==="unknown").length,1);
    assert.equal(batch.deferred,0);
    assert.equal(batch.stop_reason,null);
    assert.deepEqual(batch.endpoint_error_counts,{platform:1});
  } finally {release();await new Promise(resolve=>setImmediate(resolve));}
  assert.equal(active,0);
});

test("shared endpoints do not spend additional calls; results remain bounded",async()=>{
  let calls=0;
  const attempt=async e=>{calls++;return success(e);};
  const batch=await runBatch(work(Array.from({length:100},(_,n)=>target(n)),[target()]),null,{now:()=>now,attempt});
  assert.equal(calls,1);assert.equal(batch.results.length,40);assert.equal(batch.deferred,60);
  const small=await runBatch(work(Array.from({length:5},(_,n)=>target(n))),null,{now:()=>now,attempt,maxTargets:2});
  assert.equal(small.results.length,2);assert.equal(small.deferred,3);
});

test("multi endpoint aggregation and network guards",async()=>{
  const multi=work([target(0,[443,445])]);
  const one=await runBatch(multi,null,{now:()=>now,attempt:e=>e.port===443?failure(e):success(e)});
  assert.equal(one.results[0].status,"reachable");
  const guarded=await runBatch(work(Array.from({length:10},(_,n)=>target(n,[100+n]))),null,
    {now:()=>now,attempt:e=>e.port===444?success(e):failure(e)});
  assert.equal(guarded.guarded,true);
  const controlFail=await runBatch(work(),null,{now:()=>now,attempt:failure});
  assert.equal(controlFail.guarded,true);
});

test("budget deferral is unknown, due round suppresses repeated work",async()=>{
  let time=now;
  const batch=await runBatch(work([target(0,[443,445])]),null,{now:()=>time,budget:4000,
    attempt:async e=>{time+=4000;return failure(e);}});
  assert.equal(batch.results[0].status,"unknown");assert.equal(batch.deferred,1);
  assert.equal(batch.stop_reason,null);
  assert.equal(batch.budget_exhausted,true);
  assert.equal(batch.attempted_endpoints,1);
  assert.equal(await runBatch(work([{...target(),last_probe_round:roundOf(now)}]),null,{now:()=>now,attempt:success}),null);
});

test("probe budget reserves time for both connect and cleanup before starting a new endpoint",async()=>{
  let calls=0;
  const batch=await runBatch(work(),null,{now:()=>now,budget:3999,attempt:()=>{calls++;return success(target().endpoints[0]);}});
  assert.equal(calls,0);
  assert.equal(batch.attempted_endpoints,0);
  assert.equal(batch.stop_reason,null);
  assert.equal(batch.budget_exhausted,true);
  assert.equal(batch.deferred,1);
});

test("loads only the selected hash-checked bucket, rejects stale/mixed data",async()=>{
  const data=await inputs();
  const env={WORKER_ID:"0",REPOSITORY:"owner/repo"};
  const loaded=await loadWork(env,now,{now:()=>now,read:data.read});
  assert.equal(loaded.bucket,0);assert.equal(data.calls.length,3);
  assert.match(data.calls[1],new RegExp(`/a{40}/pool/`));
  assert.equal(data.calls.some(u=>u.endsWith("servers.json")),false);
  await assert.rejects(loadWork(env,now+4*3600000,{now:()=>now+4*3600000,read:data.read}),/Stale source/);
  await assert.rejects(loadWork(env,now,{now:()=>now,read:async url=>url.endsWith("000.json")?bytes({}):data.read(url)}),/hash mismatch/);
  const wrong=await inputs([target(1)]);
  await assert.rejects(loadWork(env,now,{now:()=>now,read:wrong.read}),/Wrong target bucket/);
  const privateTarget=await inputs([{...target(),ip:"10.0.0.1"}]);
  await assert.rejects(loadWork(env,now,{now:()=>now,read:privateTarget.read}),/Invalid target/);
});

test("delayed invocations keep their bucket and use the actual observation round",async()=>{
  const env={WORKER_ID:"0",REPOSITORY:"owner/repo"};
  const data=await inputs();
  for(const delay of [120000,10800000]) {
    const loaded=await loadWork(env,now,{now:()=>now+delay,read:data.read});
    assert.equal(loaded.bucket,0);
    assert.equal(loaded.round,roundOf(now+delay));
    assert.equal(loaded.scheduled_at,iso(now));
  }
  const previousRound=await inputs([target(71)],{bucketNumber:71});
  const loaded=await loadWork(env,now-300000,{now:()=>now,read:previousRound.read});
  assert.equal(loaded.bucket,71);
  assert.equal(loaded.round,roundOf(now));
  assert.notEqual(loaded.round,roundOf(now-300000));
  for(const invalid of [now-10800001,now+300001,-1,now+0.5,undefined]) {
    await assert.rejects(loadWork(env,invalid,{now:()=>now,read:()=>assert.fail("invalid invocation must not fetch")}),/Stale scheduled invocation/);
  }
});

test("worker ID types are consistent between scheduled and HTTP handlers",async()=>{
  const data=await inputs(),secret="x".repeat(32);
  const request=new Request(`https://probe.example/v1/batches?round=${roundOf(now)}`,{headers:{Authorization:`Bearer ${secret}`}});
  const env={REPOSITORY:"owner/repo",PROBE_READ_TOKEN:secret,RESULTS:{list:async()=>({keys:[],list_complete:true})}};
  for(const worker of ["0",0]) {
    assert.equal((await loadWork({...env,WORKER_ID:worker},now,{now:()=>now,read:data.read})).worker_id,0);
    assert.equal((await serve(request,{...env,WORKER_ID:worker},()=>now)).status,200);
  }
  for(const worker of ["1",1]) assert.equal((await serve(request,{...env,WORKER_ID:worker},()=>now)).status,404);
  for(const worker of ["",null,undefined,false,true,"00"," 0 ",2]) {
    await assert.rejects(loadWork({...env,WORKER_ID:worker},now,{now:()=>now,read:()=>assert.fail("invalid ID must not fetch")}),/Invalid WORKER_ID/);
    const response=await serve(request,{...env,WORKER_ID:worker},()=>now);
    assert.equal(response.status,500);
    assert.equal((await response.json()).error,"invalid_worker_id");
  }
});

test("scheduled writes one immutable TTL batch; empty bucket writes nothing",async()=>{
  const writes=[];const env={WORKER_ID:"0",REPOSITORY:"owner/repo",RESULTS:{put:async(...args)=>writes.push(args)}};
  const data=await inputs();
  const stored=await scheduled({scheduledTime:now},env,null,{now:()=>now,read:data.read,attempt:success});
  assert.equal(stored.status,"stored");assert.equal(writes.length,1);assert.equal(writes[0][2].expirationTtl,72*3600);
  assert.match(writes[0][0],/^results\/\d+\/[0-9a-f-]{36}$/);
  const empty=await inputs([]);
  assert.equal((await scheduled({scheduledTime:now},env,null,{now:()=>now,read:empty.read,attempt:success})).status,"no_due_targets");
  assert.equal(writes.length,1);
});

test("read API is authenticated and cannot submit targets or write KV",async()=>{
  const secret="x".repeat(48), round=roundOf(now), key=`results/${round}/00000000-0000-4000-8000-000000000000`;
  let reads=0;
  const env={WORKER_ID:"0",PROBE_READ_TOKEN:secret,RESULTS:{
    list:async()=>{reads++;return {keys:[{name:key}],list_complete:true};},
    get:async()=>{reads++;return null;},put:()=>assert.fail("HTTP must never write")}};
  const request=(path,method="GET",auth=true)=>new Request(`https://probe.example${path}`,{method,
    headers:auth?{Authorization:`Bearer ${secret}`}:{}});
  assert.equal((await serve(request(`/v1/batches?round=${round}`,"GET",false),env,()=>now)).status,401);
  assert.equal((await serve(request(`/v1/batches?round=${round}`,"POST"),env,()=>now)).status,405);
  assert.equal((await serve(request(`/v1/batches?round=${round-2}`),env,()=>now)).status,400);
  assert.equal((await serve(request(`/v1/batches?round=${round}`),{...env,WORKER_ID:"1"},()=>now)).status,404);
  const listing=await serve(request(`/v1/batches?round=${round}`),env,()=>now);
  assert.deepEqual((await listing.json()).batches,[{key}]);
  assert.equal((await serve(request(`/v1/batch/${key}`),env,()=>now)).status,404); // KV not yet visible.
  assert.equal((await serve(request("/v1/probe?ip=8.8.8.8"),env,()=>now)).status,404);
  assert.equal(reads,2);
});

test("fetch explicitly uses manual redirects and bounded response reads",async()=>{
  const result=await readBytes("https://example.test/data",2,{fetcher:async(url,options)=>{
    assert.equal(options.redirect,"manual");return new Response("{}");}});
  assert.equal(result.length,2);
  await assert.rejects(readBytes("https://example.test/data",2,{fetcher:async()=>new Response("123")}),/limit/);
  await assert.rejects(readBytes("https://example.test/data",2,{fetcher:async()=>new Response(null,{status:302})}),/HTTP 302/);
});

async function taskInputs(targets, {worker=0, wrongWorker=false, missing=false, unknownProtocol=false}={}) {
  const slot=Math.floor(now/300000), path=`pool/tasks/${worker}-${slot}.json`;
  const scheduledAt=slot*300000+(worker===0?120000:240000);
  const task=bytes({schema_version:2,worker_id:wrongWorker?1-worker:worker,slot,scheduled_at:iso(scheduledAt),targets});
  const manifest=bytes({schema_version:2,normal_interval_seconds:14400,retry_interval_seconds:3600,
    tasks:missing?{}:{[`${worker}:${slot}`]:{path,bytes:task.length,sha256:await hash(task)}}});
  const plan=bytes({schema_version:1,bucket_count:144,source_fetched_at:iso(now),controls:[target(1,[444])],
    buckets:{},task_protocol_version:unknownProtocol?3:2,
    task_manifest:{path:"pool/task-plan.json",bytes:manifest.length,sha256:await hash(manifest)}});
  const index=bytes({kind:"vpngate-pool",schema_version:1,data_commit:"a".repeat(40),source_fetched_at:iso(now),
    files:{"pool/probe-plan.json":{bytes:plan.length,sha256:await hash(plan)}}});
  const calls=[];
  const read=async url=>{calls.push(url);return url.endsWith("latest.json")?index:
    url.endsWith("probe-plan.json")?plan:url.endsWith("task-plan.json")?manifest:task;};
  return {read,calls,task,scheduledAt,path};
}

test("v2 task packs cross buckets within one partition and write one KV key",async()=>{
  const targets=Array.from({length:35},(_,n)=>({...target(n,[1000+n]),next_probe_at:iso(now),last_probe_round:roundOf(now)}));
  const data=await taskInputs(targets), writes=[];
  const env={WORKER_ID:"0",REPOSITORY:"owner/repo",RESULTS:{put:async(...args)=>writes.push(args)}};
  const stored=await scheduled({scheduledTime:now},env,null,{now:()=>now,read:data.read,attempt:success});
  assert.equal(stored.schema_version,2);
  assert.equal(data.calls.length,4);
  assert.equal(writes.length,1);
  assert.equal(writes[0][2].expirationTtl,259200);
  const batch=JSON.parse(writes[0][1]);
  assert.equal(batch.results.length,35);
  assert.equal(batch.bucket,undefined);
  assert.equal(batch.task_path,data.path);
  assert.equal(batch.attempted_endpoints,36);
  assert.equal(batch.results.every(r=>r.status==="reachable"),true);
});

test("missing tasks and targets not yet due do not probe or write; no same-round suppression in v2",async()=>{
  const writes=[],env={WORKER_ID:0,REPOSITORY:"owner/repo",RESULTS:{put:async(...args)=>writes.push(args)}};
  for(const data of [await taskInputs([],{missing:true}),await taskInputs([{...target(),next_probe_at:iso(now+3600000)}])]) {
    const result=await scheduled({scheduledTime:now},env,()=>assert.fail(),
      {now:()=>now,read:data.read,attempt:()=>assert.fail("future target must be skipped")});
    assert.equal(result.status,"no_due_targets");
  }
  assert.equal(writes.length,0);
});

test("task hash, protocol, assignment and public address checks precede any socket",async()=>{
  const good={...target(),next_probe_at:iso(now)},env={WORKER_ID:0,REPOSITORY:"owner/repo"};
  for(const [data,pattern] of [
    [await taskInputs([good],{wrongWorker:true}),/Mixed task/],
    [await taskInputs([good],{unknownProtocol:true}),/Unsupported task/],
    [await taskInputs([{...good,id:id(72)}]),/Wrong task partition/],
    [await taskInputs([{...good,endpoints:[{ip:"10.0.0.1",port:443}]}]),/Invalid endpoint/],
    [await taskInputs([good,good]),/duplicate task/]
  ]) await assert.rejects(loadWork(env,now,{now:()=>now,read:data.read}),pattern);
  const data=await taskInputs([good]);
  await assert.rejects(loadWork(env,now,{now:()=>now,
    read:url=>url.endsWith(data.path)?bytes({}):data.read(url)}),/hash mismatch/);
  const delayed=await loadWork(env,now,{now:()=>now+10800000,read:data.read});
  assert.equal(delayed.task_slot,Math.floor(now/300000));
  const one=await taskInputs([{...good,id:id(72)}],{worker:1});
  assert.equal((await loadWork({...env,WORKER_ID:1},one.scheduledAt,{now:()=>one.scheduledAt,read:one.read})).worker_id,1);
});

test("confirmed late closure recovers lanes without exceeding four outstanding sockets",async()=>{
  let active=0,peak=0,calls=0;
  const connect=()=>{
    active++;calls++;peak=Math.max(peak,active);
    let resolveClosed;
    const closed=new Promise(resolve=>{resolveClosed=resolve;});
    return {opened:Promise.resolve(),closed,close:()=>new Promise(resolve=>setTimeout(()=>{
      active--;resolveClosed();resolve();
    },35))};
  };
  const batch=await runBatch(work(Array.from({length:12},(_,n)=>target(n,[1000+n])),[]),connect,
    {recoveryWait:200,attempt:(e,c,options)=>connectOnce(e,c,{...options,closeTimeout:5})});
  assert.equal(calls,12);
  assert.equal(peak,4);
  assert.ok(batch.recovered_sockets>=8);
  assert.equal(batch.deferred,0);
  assert.equal(batch.results.every(r=>r.status==="reachable"),true);
  await new Promise(resolve=>setTimeout(resolve,50));
  assert.equal(active,0);
});

test("rejected closure never frees a lane and recovery waiting is bounded",async()=>{
  let calls=0;
  const batch=await runBatch(work(Array.from({length:8},(_,n)=>target(n,[1000+n])),[]),()=>{
    calls++;return {opened:Promise.resolve(),closed:Promise.reject(new Error("closed rejected")),
      close:()=>Promise.reject(new Error("close rejected"))};
  },{recoveryWait:10,attempt:(e,c,options)=>connectOnce(e,c,{...options,closeTimeout:5})});
  assert.equal(calls,4);
  assert.equal(batch.recovered_sockets,0);
  assert.equal(batch.unclosed_sockets,4);
  assert.equal(batch.stop_reason,"socket_close_unconfirmed");
});

test("a six-hour boundary during input loading cannot backdate the execution round",async()=>{
  const nextRound=(roundOf(now)+1)*21600000;
  const before=work([{...target(),last_probe_round:roundOf(now)}]);
  const batch=await runBatch(before,null,{now:()=>nextRound,attempt:success});
  assert.equal(batch.round,roundOf(nextRound));
  assert.equal(batch.results.length,1);
  const packed={...before,task_slot:Math.floor(now/300000),task_path:"pool/tasks/0-1.json",
    task_sha256:"a".repeat(64),task_manifest_sha256:"b".repeat(64),
    targets:[{...target(),next_probe_at:iso(now)}]};
  assert.equal((await runBatch(packed,null,{now:()=>nextRound,attempt:success})).round,roundOf(nextRound));
});
