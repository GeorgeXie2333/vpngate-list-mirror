import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {digest} from "../examples/consume.mjs";
import {loadPool,loadPoolConfig,verifyPoolCatalog,validatePoolIndex} from "../examples/consume_pool.mjs";

const golden=JSON.parse(await readFile(new URL("./fixtures/pool.json",import.meta.url),"utf8"));
const fresh=()=>({index:structuredClone(golden.index),
  files:Object.fromEntries(Object.entries(golden.files).map(([p,b])=>[p,new Uint8Array(Buffer.from(b,"base64"))]))});
const json=v=>new TextEncoder().encode(JSON.stringify(v));
const reader=(input,calls=[],badCDN=false)=>async url=>{
  calls.push(url);
  if(url.endsWith("latest.json"))return json(input.index);
  if(badCDN&&url.includes("cdn.jsdelivr.net"))return json({corrupt:true});
  const path=Object.keys(input.files).find(p=>url.endsWith("/"+p));
  assert.ok(path,"unexpected request");return input.files[path];
};
test("catalog first; country filter; lazily verify and decode exactly one config",async()=>{
  const input=fresh(),calls=[],pool=await loadPool({read:reader(input,calls)});
  assert.equal(calls.length,3);assert.equal(calls.some(u=>u.includes("configs/")),false);
  const node=pool.servers.find(r=>r.country_code==="JP");
  const body=await loadPoolConfig(pool,node,{read:reader(input,calls)});
  assert.equal(await digest(body),node.openvpn_config_sha256);
  assert.match(new TextDecoder().decode(body),/up \/this-command-must-never-run/);
  assert.equal(calls.length,4);assert.ok(calls.slice(1).every(u=>u.includes("@"+pool.index.data_commit)));
});
test("unregistered source identifier remains downloadable through the pool consumer",async()=>{
  const input=fresh(),path="pool/servers.json",catalog=JSON.parse(new TextDecoder().decode(input.files[path]));
  const row=catalog.servers.find(r=>r.hostname==="vpn-example");
  row.hostname="_unregistered_vpn335506854";
  row.id="v1:"+await digest(new TextEncoder().encode(`vpngate-node-v1\0${row.hostname}\0${row.ip}`));
  catalog.servers.sort((a,b)=>a.id.localeCompare(b.id));
  input.files[path]=json(catalog);
  input.index.files[path]={bytes:input.files[path].length,sha256:await digest(input.files[path])};
  const read=reader(input),pool=await loadPool({read}),node=pool.servers.find(r=>r.id===row.id);
  assert.equal(node.hostname,row.hostname);
  assert.equal(await digest(await loadPoolConfig(pool,node,{read})),row.openvpn_config_sha256);
});
test("CDN corruption uses Raw with identical commit for catalog and config",async()=>{
  const input=fresh(),calls=[],read=reader(input,calls,true),pool=await loadPool({read});
  await loadPoolConfig(pool,pool.servers[0],{read});
  assert.equal(calls.length,7);
  assert.ok(calls.filter(u=>u.includes("raw.githubusercontent.com")&&!u.endsWith("latest.json"))
    .every(u=>u.includes(`/${pool.index.data_commit}/pool/`)));
});
test("old/unsupported indexes and hash mismatch retain the previous object",async()=>{
  const input=fresh(),pool=await loadPool({read:reader(input)}),old=JSON.stringify(pool.index);
  input.index.source_fetched_at="2026-08-01T00:00:00.000Z";
  await assert.rejects(loadPool({previous:pool,read:reader(input)}),/older/);
  assert.equal(JSON.stringify(pool.index),old);
  for(const changes of [{schema_version:2},{data_commit:"main"},{server_count:true}])
    assert.throws(()=>validatePoolIndex({...pool.index,...changes}));
  const corrupt=fresh();corrupt.files["pool/servers.json"][0]^=1;
  await assert.rejects(loadPool({read:reader(corrupt)}),/CDN and same-commit/);
});
test("metadata hash alone cannot conceal mixed file versions or incorrect country totals",async()=>{
  for(const mixed of [true,false]){
    const input=fresh(),path="pool/countries.json",groups=JSON.parse(new TextDecoder().decode(input.files[path]));
    if(mixed)groups.source_fetched_at="2026-08-01T00:00:00.000Z";
    else groups.countries[0].server_count++;
    input.files[path]=json(groups);input.index.files[path]={bytes:input.files[path].length,sha256:await digest(input.files[path])};
    await assert.rejects(verifyPoolCatalog(input.index,input.files),mixed?/Mixed pool/:/totals mismatch/);
  }
});
test("configuration hash failure cannot return bytes and forged config paths cannot download",async()=>{
  const input=fresh(),pool=await loadPool({read:reader(input)}),row=pool.servers[0];
  input.files[row.config.path][0]^=1;
  await assert.rejects(loadPoolConfig(pool,row,{read:reader(input)}),/same-commit/);
  await assert.rejects(loadPoolConfig(pool,{...row,config:{...row.config,path:"../secret"}},{read:()=>assert.fail()}),/verified pool/);
});
