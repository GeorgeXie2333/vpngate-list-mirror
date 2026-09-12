# 自行部署 TCP 探测 Workers

[English](workers.md) · [节点池协议](pool.zh-CN.md)

仓库提供源码、配置模板和固定版本部署工具。由维护者自行创建 KV、部署 Workers 并完成
真实环境验收；Actions 不自动部署 Cloudflare，也不自动升级付费计划。
现有 `vgate-list-update` 和 `29,59 * * * *` 保持不变。

## 设置清单

| 设置 | 保存位置 | 值 |
| --- | --- | --- |
| `RESULTS` | 两个探测 Worker 的 KV binding | 同一个新建 KV namespace |
| `WORKER_ID` | Worker 变量 | `0` 或 `1`，文本或 JSON 数字均可，其他值会被拒绝 |
| `REPOSITORY` | 两个 Worker 变量 | `GeorgeXie2333/vpngate-list-mirror` |
| `MAX_TARGETS_PER_RUN` | 两个 Worker 变量 | 首次小批验收用 `2`，完整上线用 `40`，允许 1–40 |
| `PROBE_READ_TOKEN` | Worker 0 Secret 与 GitHub Actions 仓库 Secret | 同一个至少 32 字符的随机秘密，与现有触发令牌分开 |
| `PROBE_RESULTS_URL` | GitHub Actions 仓库 Variable | Worker 0 的 HTTPS origin，例如 `https://vgate-tcp-probe-0.你的子域.workers.dev`，不带路径或查询参数 |
| `POOL_ENABLED` | GitHub 仓库 Variable | 未设置／`true` 开启池；`false` 冻结池，原始镜像继续 |
| `TCP_PRUNE_ENABLED` | GitHub 仓库 Variable | 首先设 `false`；真实验收后设 `true`，未设置默认 true |

Worker 1 无需秘密。两个探测 Worker 都不持有 GitHub 写入或触发令牌。公开节点池和配置
无需 `PROBE_READ_TOKEN`，它仅用于后台批次读取。未配置接口时正常累积，TCP 清理暂停；
上游失败仍保留两套旧索引。

## Dashboard 部署（无需 Wrangler）

打开 [worker-dashboard.js](../workers/worker-dashboard.js)，通过 GitHub 的 **Raw** 按钮下载，
将完整文件粘贴到两个探测 Worker，并分别部署生产版本。保留各自变量、共享的 `RESULTS`
绑定及上述 Cron 设置。首次验证继续用 `MAX_TARGETS_PER_RUN=2`、`TCP_PRUNE_ENABLED=false`。

此文件由 `node workers/build-dashboard.mjs` 从 `workers/src` 生成，仅使用 Node.js 标准库。
命令也会刷新内容完全相同的本地 `build/worker-dashboard.js`。CI 校验发布文件与源码一致；
修改源码后重新生成，不要手动维护两份 JS。

每次定时调用先记录 `started`，随后记录 `stored`、`no_due_targets` 或 `failed`。
如果无法确认 socket 已关闭，本批停止开启新连接，受影响端点记为 `unknown`，剩余任务延期，
并继续尝试一次 KV 写入。日志包含 `stop_reason: socket_close_unconfirmed` 与
`unclosed_sockets`。最多保留四个未关闭连接，为 KV 请求留出余量。

调度延迟最多允许三小时，同时仍要求源数据不超过三小时。原计划时间决定桶号，实际探测时间
决定结果轮次；延迟执行不会补算过去轮次的失败。

## 使用 Wrangler 部署（可选）

1. 新代码推送后在 **Actions → Sync VPN Gate → Run workflow → main** 发布首次
   `pool/latest.json`，确认获取时间未超过三小时。仓库 Variable 先设
   `TCP_PRUNE_ENABLED=false`。
2. 检查账号还有两个 Cron 空位及足够 KV 额度。加上现有触发 Worker，本项目共三个 Cron。
   在 Cloudflare 控制台创建一个 KV namespace，例如 `vpngate-probe-results`，记下 ID。
3. 安装固定依赖，复制不会提交的本地配置：

   ```bash
   cd workers
   npm ci
   cp wrangler.probe-0.jsonc wrangler.local.probe-0.json
   cp wrangler.probe-1.jsonc wrangler.local.probe-1.json
   ```

   PowerShell 可用 `Copy-Item`；若执行策略阻止脚本包装器，用 `npm.cmd`、`npx.cmd`。
   在**两份本地配置**中把 `REPLACE_WITH_SHARED_KV_NAMESPACE_ID` 替换为同一 ID。
   Worker 0 的 `MAX_TARGETS_PER_RUN` 改为 `"2"`，用于首次小批验证。仓库模板保留通用设置。

4. 用自己的账号登录 Wrangler，先只部署 Worker 0。lockfile 固定 Wrangler 4.131.1，
   `--no-install` 防止意外下载其他版本。

   ```bash
   npx --no-install wrangler login
   npx --no-install wrangler deploy --dry-run --config wrangler.local.probe-0.json
   npx --no-install wrangler deploy --config wrangler.local.probe-0.json
   npx --no-install wrangler secret put PROBE_READ_TOKEN --config wrangler.local.probe-0.json
   ```

   在交互提示中输入生成的随机秘密。同值保存到 GitHub **Settings → Secrets and variables
   → Actions → Secrets**，Worker 0 部署后实际 HTTPS origin 保存到 **Variables →
   PROBE_RESULTS_URL**。不要提交秘密，也不要把秘密放在消费端 URL 中；KV ID 本身不是秘密。

5. 完成下方小批验收后，把 Worker 0 限制改回 `"40"`，再次部署并上线 Worker 1：

   ```bash
   npx --no-install wrangler deploy --config wrangler.local.probe-0.json
   npx --no-install wrangler deploy --dry-run --config wrangler.local.probe-1.json
   npx --no-install wrangler deploy --config wrangler.local.probe-1.json
   ```

   确认 Worker 0 的 Cron 为 `2-57/5 * * * *`，Worker 1 为 `4-59/5 * * * *`，均使用 UTC。
   [Cron 变更传播](https://developers.cloudflare.com/workers/configuration/cron-triggers/)
   最多可能需 15 分钟。检查正常批次 CPU 和一个完整六小时轮次，再设
   `TCP_PRUNE_ENABLED=true`。六小时是目标间隔，平台调度和超预算仍可能延期。

## 必须完成的真实验收

- 查看 Cloudflare 的真正 **scheduled** 事件，不是对 `/__scheduled` 的 HTTP GET。
  生产 HTTP 接口不会触发探测。等待一个非空桶；空桶或已探测桶记录 `no_due_targets` 正常。
- 确认 `socket.opened` 成功、连接立即关闭、平台异常归类 unknown。首次最多两个待测节点
  加最多五个对照，不发送应用数据，不运行 OpenVPN。
- 查看实际 CPU、墙钟时长、资源异常和子请求计数。网络等待不等于 CPU。小批成功不证明
  40 端点批次符合免费 CPU 上限，扩容后仍需检查。
- KV 中应出现一个唯一 `results/<round>/<uuid>`，72 小时过期，内含结果、对照、保护标记
  和延期数。空桶可不写。跨位置列举／读取暂不可见允许重试。
- 下一次既有半小时 Actions 后，摘要 `pool.reader.status` 应为 `read`、
  `batches_applied` 增加、UUID 出现在已提交 `pool/state.json`，对应节点更新探测状态。
  不需要新增 Actions 次数；首次验收也可手动刷新缩短等待。
- 用新消费端下载池和配置，核对同 SHA 哈希、时间和移除原因。平台异常或 CPU 超限时保留
  TCP 清理关闭、降低批次限制，不自动开启付费计划。

离线测试使用假 socket 和 KV，不能代替真实 TCP、平台错误分类和 CPU 验收。
完成后记录部署版本、资源测量和对应 Actions URL。

## KV 为空时排查

先区分定时探测、KV 写入和 Actions 读取这三个阶段：

| 现象 | 检查位置与含义 |
| --- | --- |
| 日志只有 `fetch` / `GET /v1/batches` | 这是 Actions 读取，不能证明定时探测执行过 |
| Cron 已配置，但没有 `scheduled` 日志 | 查看 Worker **Settings → Trigger Events → View events** 的 Cron 执行历史，确认正在查看已部署的生产 Worker |
| Cron 执行历史也为空 | 核对生产部署确实含 `scheduled` 处理器、Cron 保存后仍在列表中，以及账号 Cron 配额；不能仅据此认定为 KV 故障 |
| `status: no_due_targets` | 当前桶为空或本轮已完成，按设计不写 KV；看后续不同桶 |
| `status: stored`，所查看的 KV 仍为空 | 比较两个 Worker 的 `RESULTS` 绑定与当前打开的 namespace ID |
| Actions `pool.reader.errors` 包含 `http_404` | 列表接口地址应是 Worker 0 的 origin；Worker 1 固定返回 404。单条批次 404 也可能是暂不可见 |
| Actions 报 `http_401` | 核对 Worker 0 与 Actions 的 `PROBE_READ_TOKEN`，不要把秘密贴进日志 |
| Actions `reader.status: read` 且 `batches_read: 0` | 读取成功但没有新的可见批次，不能将其计为节点失败 |

通过 Dashboard 部署时，JS、变量、KV binding 和 Cron 都需要分别保存到对应 Worker。
Cron 位置是 **Settings → Triggers → Cron Triggers**；Worker 0 用 `2-57/5 * * * *`，
Worker 1 用 `4-59/5 * * * *`。无需 Wrangler。
[官方文档](https://developers.cloudflare.com/workers/configuration/cron-triggers/)说明 Cron
变更传播可能需 15 分钟，新建或重命名 Worker 的历史记录首次显示可能需 30 分钟。
超过这些时间仍无记录时，先核对生产部署和触发器状态，再检查平台故障。

## 读取接口和额度

Worker 0 仅接受携带 `Authorization: Bearer <PROBE_READ_TOKEN>` 的 GET：

- `/v1/batches?round=<整数>&cursor=<可选>`：列举当前／上一轮键，返回 `list_complete`
  和 `cursor`，每页最多 200 个。
- `/v1/batch/results/<round>/<uuid>`：读取列出的批次。暂时 404 可能只是尚不可见，
  读取失败不能确认消费。

Worker 1 HTTP 返回 404。接口不能提交目标、启动探测或写入结果。未认证返回 401，
写请求返回 405，响应带 `Cache-Control: no-store`。轮换秘密时同步更新 Worker 0 与
GitHub；短暂不匹配只暂停 TCP 处理，采集继续。

[TCP sockets 文档](https://developers.cloudflare.com/workers/runtime-apis/tcp-sockets/)
提供 `opened` 和 `close()`，并禁止 Cloudflare 目标、私网／localhost 和端口 25。
代码额外验证公网数字地址，未明确的平台错误均为未知。多个 Worker 不表示不同地区独立观测。

2026-09-12 核对的 [Workers Free 限制](https://developers.cloudflare.com/workers/platform/limits/)
包括每调用 10 ms CPU、50 次子请求、六个并发出站连接、每账号五个 Cron Trigger。
本实现三次小型 Raw 读取、最多 40 个端点、最多四个并发 socket、非空批次一次 KV 写入；
必须以平台实际统计验收。两个探测 Worker 合计每天计划最多 576 次写入，低于
[KV 免费每天 1,000 写](https://developers.cloudflare.com/kv/platform/limits/)，
但还要计算账号其他项目、列表／读取及存储额度。

[KV 最终一致性](https://developers.cloudflare.com/kv/concepts/how-kv-works/) 包括负缓存，
跨位置可见可能需 60 秒或更久。没有共享 latest 键，不依赖 KV 原子更新。暂停两个探测
Cron 或清空 `PROBE_RESULTS_URL` 可停止处理，不影响既有镜像触发器。配置失去引用时不重写 Git 历史。
