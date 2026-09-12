# VPN Gate 服务器目录镜像

[English](README.md) · [数据协议](docs/protocol.zh-CN.md) · [运维说明](docs/operations.zh-CN.md) · [首次验收](docs/verification.md)

公开镜像[官方 CSV API](https://www.vpngate.net/api/iphone/) 实际返回的服务器目录。
任何用户、开发者或自动化工具都可以通过 HTTPS 下载清单及完整公开 OpenVPN 配置，
无需登录、注册、API Key 或消费端 GitHub Token。

Cloudflare Worker `vgate-list-update` 按 **每小时第 29、59 分钟（UTC）**
（`29,59 * * * *`，每天计划 48 次）触发 GitHub Actions，由其获取完整响应、校验并提交。
GitHub Raw 提供小型版本索引，jsDelivr 按完整数据提交 SHA 分发文件。
本项目不声称涵盖全球全部 VPN Gate 节点；国家、评分、Ping、速度和会话数均来自上游。
上游指标不是本项目实测值。独立节点池提供“**Cloudflare Workers 观测到的 TCP 可达性**”，
不代表 VPN 连接成功或用户所在地可达。本项目不连接 VPN、不执行配置、不创建 TUN，也不修改系统网络。
清单更新不保证连接成功。

## 公开下载

默认下载地址使用 `@latest`，可直接复制，无需任何凭据：

```text
https://cdn.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/data/vpngate.csv
https://cdn.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/data/servers.json
https://cdn.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/data/countries.json
```

可按网络情况选择以下 jsDelivr 入口，表内链接均使用 `@latest`：

| 入口 | 节点 JSON | 原始 CSV | 国家 JSON |
| --- | --- | --- | --- |
| jsDelivr 默认（`cdn.jsdelivr.net`） | [JSON](https://cdn.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/data/servers.json) | [CSV](https://cdn.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/data/vpngate.csv) | [国家](https://cdn.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/data/countries.json) |
| jsDelivr Fastly（`fastly.jsdelivr.net`） | [JSON](https://fastly.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/data/servers.json) | [CSV](https://fastly.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/data/vpngate.csv) | [国家](https://fastly.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/data/countries.json) |
| jsDelivr Gcore（`gcore.jsdelivr.net`） | [JSON](https://gcore.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/data/servers.json) | [CSV](https://gcore.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/data/vpngate.csv) | [国家](https://gcore.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/data/countries.json) |
| `testingcf.jsdelivr.net` | [JSON](https://testingcf.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/data/servers.json) | [CSV](https://testingcf.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/data/vpngate.csv) | [国家](https://testingcf.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/data/countries.json) |
| `quantil.jsdelivr.net` | [JSON](https://quantil.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/data/servers.json) | [CSV](https://quantil.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/data/vpngate.csv) | [国家](https://quantil.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/data/countries.json) |

这些链接方便直接下载，但可能返回缓存数据。`@latest` 指向最新语义化版本发布；
没有标签发布时回退到默认分支，详见 [jsDelivr 解析规则](https://github.com/jsdelivr/jsdelivr#github)。
它不保证按此计划刷新，也不保证不同文件或入口返回同一快照。
需要判断新鲜度并校验完整性时，使用下方的索引流程。

| 文件 | 内容 |
| --- | --- |
| `data/vpngate.csv` | 原始响应字节，含标记、全部原始字段和完整 Base64 配置 |
| `data/servers.json` | 去重后的节点、上游指标、国家信息及完整 Base64 配置 |
| `data/countries.json` | 国家／地区代码、源站名称和唯一节点数量 |
| `latest.json` | 协议版本、成功获取时间、生成时间、数据提交 SHA、文件哈希、字节数及计数 |

[固定分支 JSON 链接](https://cdn.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@main/data/servers.json)
仅方便人工查看，可能有缓存延迟，不用于发现最新版本。

## 累积节点池

官方 API 会轮换返回的节点。独立节点池累积历史观察，上面的 CSV/JSON 仍严格对应单次
响应。缺席节点最多保留七天；可探测的 TCP 节点在缺席满 24 小时且连续三个有效六小时
轮次失败时可提前移除。未知、UDP、缺失和延期结果不累计失败。完整配置按内容哈希复用，
消费端按需下载。

```text
https://cdn.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/pool/servers.json
https://cdn.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/pool/countries.json
https://raw.githubusercontent.com/GeorgeXie2333/vpngate-list-mirror/main/pool/latest.json
```

`@latest` 便捷地址受上述缓存限制。程序应从节点池**自己的** Raw 索引发现完整 commit SHA，
校验目录和配置文件描述；不要混用根目录 `latest.json`。两个新示例均校验下载、筛选国家、
原样保存配置，不执行指令：

```bash
python examples/consume_pool.py --country JP --output selected.ovpn
node examples/consume_pool.mjs JP selected.ovpn
```

两个探测 Worker 负责全部 TCP 连接；Actions 只读取 KV 中已完成的批次、合并和发布。
读取接口未配置时，节点累积和七天过期仍正常运行，TCP 清理暂停。公开节点池和配置无需
任何凭据。见[节点池协议与 API 示例](docs/pool.zh-CN.md)、[自行部署 Workers](docs/workers.zh-CN.md)。
现有触发 Worker 及 `29,59 * * * *` 调度保持不变。

## 新鲜度与一致性

读取[最新成功快照索引](https://raw.githubusercontent.com/GeorgeXie2333/vpngate-list-mirror/main/latest.json)，
用其中 `data_commit` 的 **完整 40 位 SHA** 替换 `COMMIT`：

```text
https://cdn.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@COMMIT/data/servers.json
https://raw.githubusercontent.com/GeorgeXie2333/vpngate-list-mirror/COMMIT/data/servers.json
```

第二个地址是同一提交的 GitHub Raw 回退入口。下载 CSV 或国家列表时替换文件名；
CDN 域名也可以换成上表中的其他入口。

1. 一次读取索引，检查支持的 `schema_version`。
2. 固定该索引的 `data_commit`，下载所需文件。
3. 先对响应字节验证长度和 SHA-256，再解析数据；不能对重新序列化的 JSON 求哈希。
4. CDN 失败时尝试同 SHA 的 Raw 地址，不退回分支文件。
5. 校验跨文件关系与计数后，整体替换本地快照。任何必要文件失败，都保留旧成功缓存。

`fetched_at` 是本项目最近一次完整获取且校验通过的时间，不是源站目录更新时间。
上游没有可靠更新时间时，`source_updated_at` 为 `null`。
数据字节未变但再次获取成功时，只更新小型索引的获取时间，复用数据提交和大文件。

[jsDelivr 官方文档](https://github.com/jsdelivr/jsdelivr#caching)说明分支缓存为 12 小时，
包括 `latest` 在内的版本别名缓存为 7 天，完整 commit 内容长期保存。
`@main`、`@latest`、时间戳查询参数或 purge 都不是本项目的一致性机制；项目无需 purge。

GitHub Raw 同样有缓存。2026-09-11 匿名请求观测到 `max-age=300`，这不是时限承诺；
浏览器 `cache: "no-store"` 也不能保证所有上游缓存立即更新。
调度由 [Cloudflare Cron Triggers](https://developers.cloudflare.com/workers/configuration/cron-triggers/)
管理，GitHub Actions 不再配置 `schedule`。Worker 触发、runner 启动、成功发布和 CDN
可见是不同事件；请求被接受不等于发布完成，不承诺严格准点或全网同时刷新。

建议消费端每 10–15 分钟加随机偏移检查索引；3 小时可提示陈旧，24 小时可提示严重陈旧，
阈值由消费端自行决定。已缓存更新索引时拒绝回退；刷新失败或协议不支持时保留旧缓存并显示其时间。
新快照中消失的节点从当前目录移除，但不据此断言节点已离线。
SHA-256 用于完整性与跨文件一致性，**不是独立来源认证**。

## 使用示例

默认 `@latest` 快速下载示例，缓存行为见上文：

```bash
curl --fail --silent --show-error --location --max-time 30 \
  --max-filesize 16777216 --proto '=https' --proto-redir '=https' \
  https://cdn.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/data/servers.json \
  --output servers.download.json
```

以下完整校验示例使用 Raw 索引和完整提交 SHA。公开克隆本仓库后执行；
Python 使用 Python 3.13+ 标准库，不需要 pip 包或 `jq`。
Windows 使用 `curl.exe` 和已安装的 Python 命令。

```bash
curl --fail --silent --show-error --location --max-time 30 \
  --max-filesize 65536 --proto '=https' --proto-redir '=https' \
  https://raw.githubusercontent.com/GeorgeXie2333/vpngate-list-mirror/main/latest.json \
  --output latest.download.json

python3 examples/consume.py --index-file latest.download.json \
  --country JP --output selected.ovpn

# 或使用合并的 curl/Python 示例：
bash examples/consume.sh JP selected.ovpn

# JavaScript CLI，Node.js 22+：
node examples/consume.mjs JP selected.ovpn
```

示例会固定索引版本、校验三个文件、按国家筛选并原样解码配置，不执行配置。
Python 在 `.cache/vpngate` 下使用版本目录和原子替换的 `current.json` 指针；每个缓存目录
使用一个写入进程。可加 `--max-age-hours 24` 在安装缓存前拒绝过期数据。
刷新失败返回非零状态并保留旧成功缓存；找不到国家匹配节点时不覆盖输出配置。

Python API（在仓库根目录运行）：

```python
from pathlib import Path
from mirror.consumer import load_snapshot
from mirror.snapshot import parse_json
from mirror.validate import decode_config

index, files = load_snapshot("GeorgeXie2333/vpngate-list-mirror")
servers = parse_json(files["data/servers.json"])["servers"]
japan = [server for server in servers if server["country_code"] == "JP"]
if japan:
    Path("selected.ovpn").write_bytes(decode_config(japan[0]["openvpn_config_base64"]))
print(index["fetched_at"], index["data_commit"])
```

浏览器 HTTPS 页面可以放置一份 `examples/consume.mjs` 后导入：

```javascript
import { loadSnapshot, decodeConfig } from "./examples/consume.mjs";
let current = null;
async function refresh() {
  const next = await loadSnapshot({ previous: current });
  current = next; // 全部校验后才替换内存缓存；失败时 current 不变
  const japan = current.servers.filter(server => server.country_code === "JP");
  if (japan.length) {
    const config = new Blob([decodeConfig(japan[0])], {
      type: "application/octet-stream"
    });
    console.log(current.index.fetched_at, japan.length, config.size);
  }
}
await refresh(); // 在界面中处理异常并展示旧缓存时间
```

默认 CDN 和 GitHub Raw 的匿名请求观测到 `Access-Control-Allow-Origin: *`。
2026-09-11 对上表五个 CDN 入口的 `countries.json` 匿名检查也均返回 HTTP 200 和该跨域响应头；
实际可用性仍随网络和时间变化。
浏览器使用 `credentials: "omit"`，不携带认证或自定义条件请求头。
Raw 可能以 `text/plain` 返回 JSON，字节校验后正常解析即可；无需读取未暴露的 ETag。
JavaScript 会验证三个文件的哈希、节点 ID、配置和国家统计；Python 还会从原始 CSV
重新推导规范化字段。JavaScript 模块返回整体快照，调用者只在成功后替换缓存引用。

## 开发与运维

```bash
python -m unittest discover -s tests -v
node --test tests/test_consumer.mjs tests/test_pool_consumer.mjs workers/test/core.test.mjs
python -m mirror build --source-file tests/fixtures/normal.csv --output build/offline
python -m mirror check-live  # 可选实时检查，不发布
python -m mirror verify     # 校验含 latest.json 的已发布检出目录
```

默认测试全部离线，覆盖正常与引号 CSV、空值、重复及冲突节点、损坏 Base64、截断、空列表、
超时、大小限制、旧索引、哈希错误、版本混用、缓存切换失败、发布竞争和推送确认丢失。

在 [Sync VPN Gate](https://github.com/GeorgeXie2333/vpngate-list-mirror/actions/workflows/sync.yml)
选择 `main` 并点击 **Run workflow** 可手动刷新。只有发布 job 有 `contents: write`，
PR 检查只读；发布使用内置 `GITHUB_TOKEN`。外部调度端需要维护者的 Cloudflare 账号，
以及保存在 Worker Secret `GH_ACTIONS_TOKEN` 中的 GitHub 触发令牌，消费端无需这些凭据。
官方 Actions 固定完整 SHA，Dependabot 提交更新 PR。无需数据库、自建服务器或 Pages。
权限、令牌轮换和触发排查见[调度配置](docs/operations.zh-CN.md#刷新与调度)。

故障排查、权限、时间含义和 Git 历史维护见[运维说明](docs/operations.zh-CN.md)。
贡献见 [CONTRIBUTING](CONTRIBUTING.md)。项目自有代码使用 MIT，上游数据及配置保留原有
权利和声明，代码许可不覆盖上游内容，详见 [NOTICE](NOTICE)。
