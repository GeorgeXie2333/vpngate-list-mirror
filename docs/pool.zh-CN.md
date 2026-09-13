# 累积节点池协议 v1

[English](pool.md) · [自行部署 Workers](workers.zh-CN.md) · [原镜像 v1 协议](protocol.zh-CN.md)

节点池累积官方 API 成功响应中的观察记录，不宣称全球全部节点或 VPN 已验证可用。
Git 保存正式状态；KV 只是会过期的探测结果收件箱。原镜像 CSV/JSON 仍对应单次响应。

## 文件与兼容性

| 路径 | 内容 |
| --- | --- |
| `pool/latest.json` | `kind: "vpngate-pool"`、`schema_version: 1`、成功获取时间、生成时间、完整数据 SHA 和文件描述 |
| `pool/servers.json` | 按 ID 排序的去重节点、上游指标、观察时间、配置引用与 TCP 状态 |
| `pool/countries.json` | 国家代码、源站名称及节点数，包含未知国家 |
| `pool/configs/<解码内容SHA-256>.json` | 完整原始 Base64 及解码字节的 SHA-256、长度 |
| `pool/probe-plan.json` | 144 桶的路径／哈希／大小，以及最多五个本轮源站单端点对照节点 |
| `pool/probes/000.json` … `143.json` | 节点 ID、配置哈希、IP／端口、最近观察及探测轮次；无证书和 Base64 |
| `pool/state.json` | 已消费批次 UUID 及轮次，与池共同提交的维护状态 |

[JSON Schema](../schemas/pool/v1/) 覆盖公开文件和 Worker 批次。添加可选字段兼容，消费端
可以忽略；改变字段类型、单位、ID、必需路径或语义必须提升版本，协同升级发布器、Worker
和消费端，过渡期保留受支持的 v1 接口。未知版本保留旧缓存并报告不兼容。

全部时间使用 UTC 毫秒精度，如 `2026-09-12T12:00:00.000Z`。`source_fetched_at` 是
本项目成功获取源站的时间，**不是源站目录更新时间**；`generated_at` 是所引用数据的
生成时间，`index_generated_at` 是索引生成时间。成功推送和实际 CDN 可见时间在 Actions
摘要中分别报告，不用推送前生成的索引时间冒充。

索引包含 `source_url`、`server_count`、`workflow_run_url`；`files` 列出两份目录、
探测清单和维护状态的准确 `bytes`、`sha256`。目录携带相同获取时间和总数。探测清单描述
各桶，节点描述配置文件。路径相对仓库根目录，全部固定到本索引的完整 40 位 `data_commit`，
不要混用根目录镜像索引。原始文件字节通过校验后再解析，不对重新序列化的 JSON 求哈希。

## 节点字段

| 字段 | 含义 |
| --- | --- |
| `id` | 沿用 `v1:` + UTF-8 `vpngate-node-v1\0<规范主机名>\0<规范IP>` 的 SHA-256；配置或端口变化不改变 ID |
| `hostname`、`ip` | 原始源站标识（可含下划线，见 [v1 名称规则](protocol.zh-CN.md#节点字段)）、规范化 CSV IP |
| `country_code`、`country_name` | 大写双字母代码／上游原名称，未知为 `null`，不丢弃未知国家节点 |
| `score`、`ping_ms`、`speed_bps`、`num_vpn_sessions` | 非负安全整数或 `null`；上游评分、毫秒、bit/s、会话数，不是本项目实测 |
| `first_seen_at`、`last_seen_at` | 池保留的首次／最近成功源站观察时间，不回填整个 Git 历史 |
| `present_in_latest_source` | 最新响应是否含此 ID，不是在线标记 |
| `openvpn_config_sha256`、`openvpn_config_bytes` | 解码配置字节的哈希／长度 |
| `config` | 配置 JSON 文件的 `{path, sha256, bytes}`，与解码内容的哈希／大小区分 |
| `probe_targets`、`probe_reason` | 明确端点或空列表；原因为 `tcp`、`udp`、`non_public_ip`、`complex_configuration`、`ambiguous_endpoint`、`ambiguous_protocol` |
| `tcp_probe` | Cloudflare Workers 观测到的 TCP 可达性 |

`tcp_probe` 包含 `status`、固定 `probe_source: "cloudflare_workers"`、可空的
`checked_at`、`last_success_at`、`round`、`worker_id`、`connect_ms`、
`connected_endpoint`、`last_failure_round`，以及整数 `consecutive_failures`。
状态为 `reachable`、`unreachable`、`unknown`、`not_applicable`。`connect_ms` 只记录
成功建立连接的耗时，与上游 `ping_ms` 分开，不代表 ICMP 延迟或吞吐。成功端点由
`connected_endpoint` 的 IP／端口说明。未检测的时间字段为 `null`；UDP 为不适用，
复杂配置为未知。再次被源站返回清零失败数；配置变化重置旧配置的全部探测状态。
可达记录仍是历史观察，必须同时看检查时间。

配置 JSON 的 `kind` 为 `vpngate-pool-config`，版本 1，保存三个字段
`openvpn_config_base64`、`openvpn_config_sha256`、`openvpn_config_bytes`。
严格 Base64 解码，校验解码哈希与长度，不归一化换行或文本。所有示例均不执行其中指令。

## 探测与合并

轮次为 `floor(unix_seconds / 21600)`，UTC 00/06/12/18 点对齐。桶号为
`int(去掉v1:后的ID前8位, 16) % 144`。Worker 0 负责 0–71，Worker 1 负责 72–143；
六小时内每个五分钟槽为 `floor((scheduled_milliseconds % 21600000) / 300000)`，
桶号为 `worker_id * 72 + slot`，桶内优先最早检查的节点。六小时是目标间隔，错过调度或
超预算会延期；两个 Worker 不代表不同地区的独立观测。

Worker 读取 Raw 索引，再读取完整 SHA 下经哈希校验的清单和当前桶。源站获取时间超过
三小时则暂停。仅处理所有远端均与 CSV 公网 IP 一致、端口明确的纯 TCP 配置；支持多个
顶层 `remote` 和显式协议覆盖，最多八个去重端点。域名、代理、外部引用、连接块和混合／
不确定协议保留记录，不猜测端点。

每次最多建立 40 个不同端点的连接，含最多五个本轮源站对照；并发四个、每连接三秒、
探测预算 45 秒。任一端点成功即 TCP 可达，全部完成且失败才不可达。平台限制、端口 25、
资源异常和不明确错误均未知。只等待 `socket.opened` 然后关闭，不发送应用数据；超时也主动关闭。
如果关闭无法确认，本批停止开启新连接，受影响端点记为未知，剩余任务延期；最多保留四个未关闭
连接，为批次 KV 写入留出余量。

调用延迟最多允许三小时，与源数据三小时新鲜度检查分别执行。桶号按原计划时间选择，结果轮次
按实际执行时间记录。批次新增兼容 v1 的可选字段 `scheduled_at`、`stop_reason` 和
`unclosed_sockets`，用于记录原计划时间与关闭异常，不用于补算历史失败轮次。

Actions 只读取当前及上一轮已完成的批次，不等待探测、不连接节点 IP。批次含唯一 UUID、
Worker／桶／轮次、数据完整 SHA、清单和桶哈希、开始结束时间、逐端点结果、对照、完成／
延期数及异常保护标记。合并器重新验证并计算结果和保护条件。批次哈希不是独立来源认证。

- 节点已删、配置已变、结果过期超过 12 小时、旧轮次或时间异常则不更新；时钟容忍五分钟，
  单批次最多两分钟。乱序旧结果不能覆盖新状态。
- 同轮失败最多累计一次，同轮成功优先。轮次断档重新累计。探测后又被源站观察到的节点，
  旧失败不用于清理。
- 没有匹配的成功对照，或至少十个完成节点的失败率达到 80%，整个批次不累计失败或执行 TCP 清理。
- 缺席本身不删除。仅在合并**新的有效失败**时，缺席满 24 小时且连续三轮失败才移除。
  没有新结果时旧失败记录不会自行触发清理；未知、UDP 和延期不增加失败数。
- 七天未被源站返回则按记录过期移除，即使 TCP 可达。容量不足优先淘汰最早观察的缺席节点，
  保留当前响应，原因单独报告。
- 上限：5,000 节点、64 MiB 被引用配置 JSON、每份目录 16 MiB、索引／清单 64 KiB、
  每桶 256 KiB、单条解码配置 128 KiB。旧池损坏、源站异常或当前数据本身超容量则停止发布并保留上次成功。

KV 键为 `results/<round>/<uuid>`，TTL 72 小时，非空调用最多写一次，不写每节点键或共享
latest 键。最终一致性可能导致列表或对象暂不可见，这不是节点失败。已处理 ID 随 Git
成功快进推送才确认消费，重放幂等。KV 读取失败仅暂停 TCP 更新和清理，仍发布源站观察。

## 消费端

发现地址：[pool/latest.json](https://raw.githubusercontent.com/GeorgeXie2333/vpngate-list-mirror/main/pool/latest.json)。
文件地址为 `https://cdn.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@COMMIT/PATH`；
回退为 `https://raw.githubusercontent.com/GeorgeXie2333/vpngate-list-mirror/COMMIT/PATH`。
COMMIT 使用池索引完整 SHA，PATH 使用已验证的文件描述。两份目录全部通过后整体切换缓存，
配置按需读取并校验 JSON 及解码内容两层哈希。失败保留旧缓存，公共消费端不使用秘密。

```bash
curl --fail --silent --show-error --max-time 30 --max-filesize 65536 \
  https://raw.githubusercontent.com/GeorgeXie2333/vpngate-list-mirror/main/pool/latest.json
python examples/consume_pool.py --country JP --output selected.ovpn --max-age-hours 24
node examples/consume_pool.mjs JP selected.ovpn
```

```python
from mirror.pool_consumer import load_pool, load_pool_config
repo = "GeorgeXie2333/vpngate-list-mirror"
index, servers = load_pool(repo)
japan = [node for node in servers if node["country_code"] == "JP"]
if japan:
    config_bytes = load_pool_config(repo, index, japan[0])
print(index["source_fetched_at"], len(japan))
```

```javascript
import {loadPool, loadPoolConfig} from "./examples/consume_pool.mjs";
let current = null;
async function refresh() {
  const next = await loadPool({previous: current});
  current = next; // 校验全部目录成功后切换；异常时仍保留旧引用。
  const japan = current.servers.filter(n => n.country_code === "JP");
  if (japan.length) {
    const bytes = await loadPoolConfig(current, japan[0]);
    console.log(current.index.source_fetched_at, japan[0].tcp_probe, bytes.length);
  }
}
await refresh(); // 页面应捕获失败并展示旧缓存年龄。
```

浏览器从 HTTPS 页面提供两个 JS 模块，用匿名 Fetch 和 Web Crypto，沿用 Raw/jsDelivr
跨域行为，不携带凭据、不依赖 ETag。Python 缓存位于 `.cache/vpngate-pool`，使用原子目录
指针和内容哈希配置缓存，每个缓存目录只用一个写入者。客户端自行决定过期阈值和本地旧配置
回收。建议源数据超过三小时提示过旧，24 小时提示严重过旧，TCP 观察超过 12 小时提示过旧。
Python `--tcp-reachable` 只选择最近 12 小时内可达的观察。池节点消失也可能因七天过期或
容量淘汰，不能描述为已证实离线。
