# 运维说明

[English](operations.md) · [数据协议](protocol.zh-CN.md)

## 最少 GitHub 设置

仓库保持公开，默认分支设为 `main`，启用 Actions 并允许固定 SHA 的官方 checkout 和
setup-python Action，使用标准托管 Ubuntu runner。全局默认令牌可继续只读，只有
`sync.yml` 的发布 job 请求 `contents: write`。不需要更大付费 runner 或每次运行都上传数据 artifact。

目标分支必须允许内置 `GITHUB_TOKEN` 正常快进推送。无法满足的强制 PR、签名或状态检查
会阻止发布，不要通过允许 force-push 绕过。若组织要求代码分支仅可通过 PR 修改，应评审后
改用独立数据发布分支并更新索引 URL。发布任务本身无需 PAT；外部 Worker 使用单独的触发令牌。
无需 Pages、数据库、自建服务器、CDN 账号或 purge 权限。调度端需要 Cloudflare 账号及
Worker Secret，消费端不用 Token。
公开仓库标准 runner 用量适用 [GitHub 免费规则](https://docs.github.com/en/billing/concepts/product-billing/github-actions)。

## 刷新与调度

在 **Actions → Sync VPN Gate → Run workflow** 选择 `main`。已认证的维护者 CLI 可运行：

```bash
gh workflow run sync.yml --repo GeorgeXie2333/vpngate-list-mirror --ref main
gh run list --repo GeorgeXie2333/vpngate-list-mirror --workflow sync.yml --limit 5
```

当前调度器为 Cloudflare Worker `vgate-list-update`，Cron Trigger 配置为
`29,59 * * * *`，即 UTC 每小时第 29、59 分钟，每天计划触发 48 次。配置在 Cloudflare
**Workers & Pages → vgate-list-update → Settings → Triggers → Cron Triggers**
维护；UTC+8 同样是每小时第 29、59 分钟。[Cloudflare 文档](https://developers.cloudflare.com/workers/configuration/cron-triggers/)
说明 Cron 使用 UTC，触发配置变更可能需要最多 15 分钟传播。

已部署 Worker 的 `scheduled()` 向
`https://api.github.com/repos/GeorgeXie2333/vpngate-list-mirror/actions/workflows/sync.yml/dispatches`
发送 POST，JSON 请求体为 `{"ref":"main"}`。`sync.yml` 只保留 `workflow_dispatch`，
同时支持 Worker API 调用和手动按钮。文件保留在默认分支，工作流保持启用，不再添加第二套
GitHub `schedule`。非 `main` 的发布被跳过；GitHub 原生定时任务的 60 天无活动停用规则
不是当前方案使用的调度机制。

触发令牌在 Worker 中以 **Secret** 类型保存为 `GH_ACTIONS_TOKEN`，不能放进仓库或消费端
示例。使用 fine-grained PAT 时，仅选择本仓库，授予 **Actions: Read and write**，
触发令牌无需 Contents 写权限，见 [GitHub dispatch API](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event)。
到期前轮换令牌并更新 Worker Secret；Actions 发布 job 继续使用自己的短期 `GITHUB_TOKEN`。

Worker 请求保留有限超时和 `redirect: "manual"`，只接受 HTTP 200 或 204，重定向和其他
状态均报错。`redirect: "error"` 曾在此部署中导致运行时异常；不要携带认证头跟随重定向，
见 [Cloudflare 请求行为](https://developers.cloudflare.com/workers/runtime-apis/request/)。
验证时先看 Worker 的 `scheduled` 事件和 `workflow_dispatched` 日志，再核对对应 Actions
运行及发布摘要。访问已部署 Worker 的 `/__scheduled` 只是普通 GET，不会调用该 Worker
的定时处理函数，HTTP handler 返回 404；不要暴露无需认证的 HTTP 触发入口。

固定 `concurrency: vpngate-sync` 配合 `cancel-in-progress: false`，使 Worker 和手动触发
不会取消正在运行的同步，但 GitHub 可能替换旧的待运行任务。Cron 时间、触发请求被接受、
runner 启动、成功发布及 CDN 可见是不同时间；仍可能排队或遇到服务故障，不保证严格每半小时
发布。请求超时且无法确定是否被接受时，先查看最近运行，再决定是否重试，避免重复触发。

**Optional live source check** 工作流或 `python -m mirror check-live` 只获取并校验真实
HTTPS 响应，不发布。普通 PR 与代码 push 检查只使用离线样例。

## 发布事务

同步命令并行运行完整的 Python、JavaScript 离线测试和一次源站获取。只有两个测试套件均
通过、获取也成功后，才在内存生成并校验数据；测试子进程不接收发布令牌。浅克隆发布分支头到
自己拥有的临时检出目录。若远程相对已测试代码提交发生代码、协议或生成数据变更，则停止；
纯文档变更可以保留。只暂存三个镜像数据路径、`latest.json` 和生成的 `pool/` 文件。

内容变化时，本地提交图为 `远程 H → 数据 D → 索引 I`。读取 D 的实际完整 SHA 写入 I，
完成全部校验后一次正常快进推送 I，使两个提交一起可用，避免提交自引用。
不能用工作流的 `GITHUB_SHA` 代替 D。内容相同时复用 D 和数据生成时间，仅提交更新获取
时间的小型索引，不给原始镜像数据文件加入时间戳。池目录包含观察时间，每次成功获取会更新；
配置内容不变则复用原有 blob。两套索引可以引用不同的数据 commit，但各套内部固定自己的 commit。

推送被拒后读取远程，不 force-push。若只是并发文档提交，则在新头上重新生成 D 和 I，
最多三次推送；不能 rebase D 后继续使用 I 中旧的 SHA。并发代码或数据变更明确失败；
已经发布的较新获取结果会取代本次旧结果。推送确认丢失时先检查远程头／两套索引，避免重复发布。
已处理 Worker 批次 ID 与池状态共同提交，推送失败不会提前确认消费。
检出或重置操作只发生在发布器自己的临时目录，不重置维护者的工作区。

Git 认证在数据校验后加入进程环境，不写入 URL、命令参数或持久凭据文件，不发送给源站、
CDN 或消费端。checkout 使用 `persist-credentials: false`。同步不监听 push，内置 Token
普通推送也不会继续触发 push 工作流，详见[触发规则](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)。
PR 检查明确为只读。

索引始终表示**最新成功**，不是失败日志。获取或关键校验失败返回失败运行和摘要，并保留
旧数据与索引。首次成功前不生成这些文件，不发布空白或伪造成功快照。

## 可见性与故障排查

| 摘要字段 | 实际含义 |
| --- | --- |
| `attempt_started_at` | runner 本次开始尝试时间，不是计划 cron 时间 |
| `fetched_at` | 完整响应获取并通过校验的时间 |
| `data_changed`、文件字节数和计数 | 本次响应范围及是否变化 |
| `push_confirmed_at` | 分支更新被接受或随后确认成功的时间 |
| `durations_seconds` | 测试／获取、校验、发布、下载探测和命令总耗时；不含 runner 排队和环境准备 |
| `visibility_probes[path].cdn.verified_at` | 当前 runner 下载并校验对应 SHA 的 CDN 文件的时间 |
| `visibility_probes[path].raw.verified_at` | 同版本 Raw 回退校验时间 |

新 SHA URL 只在推送确认后探测，避免提前制造 404。六个 CDN／Raw 下载同时进行，每个请求
仍有大小、超时限制和完整 SHA-256 校验，分别报告失败。CDN 探测失败不撤销已校验的 Git 数据，
摘要会报告警告及 Raw 结果。这些只是一个网络位置的观测，不代表全球边缘节点同时可见；
客户端暂时无法获取时继续保留旧缓存。

| 问题 | 处理 |
| --- | --- |
| 没有 Worker scheduled 事件 | 检查已部署 Worker 及 Cron Trigger，配置变更后等待传播 |
| Worker 触发失败 | 检查 GH_ACTIONS_TOKEN、有效期、仓库范围、Actions 写权限、API 状态及 redirect: "manual" |
| 触发被接受但数据未发布 | 检查 Actions 运行、排队、工作流启用状态及发布摘要；接受请求不等于发布完成 |
| 源站超时、429、5xx | 查看有限重试结果，保留旧快照，稍后手动重试 |
| 空列表、截断、格式或配置异常 | 查看错误及源站变化，增加离线回归样例后再改校验规则 |
| 远程未变化但推送被拒 | 检查 job 权限、仓库和分支规则；不 force-push |
| 并发代码／数据变更 | 评审变更后从最新 main 重新运行 |
| 索引看起来较旧 | 比较 fetched_at、Actions 最近结果及 Raw 缓存；查询参数不能保证即时刷新 |
| 新 SHA 的 CDN 地址失败 | 尝试同 SHA Raw；二者均无法校验则保留旧缓存 |
| 哈希或跨文件不匹配 | 拒绝候选，检查索引对应运行和固定提交文件 |
| 协议不支持 | 保留旧数据、展示其年龄，升级消费端 |
| 国家没有节点 | 不断言离线；选择本次实际返回的国家或等待下次目录 |

CSV 校验失败时，任务日志和摘要中的 `validation_error` 给出从 1 开始的
`csv_record` 及记录结束的物理行号 `csv_line_end`。名称错误还包含 `field`：
`HostName` 表示 CSV 字段，`OpenVPN_ConfigData_Base64.remote.host` 表示配置中的
远端主机名（包括 `<connection>` 内的 `remote`）。`value_preview` 是前 96 个字符的
ASCII JSON 转义预览，配合原值字符数、截断标志和 UTF-8 SHA-256 定位问题。
`rejected_source` 给出整份失败响应的字节数及 SHA-256；这是响应指纹，不是响应备份。
日志不输出完整 CSV、Base64 配置或证书；校验失败仍拒绝发布，并保留旧的成功快照。

浏览器应从 HTTPS 来源进行匿名 `fetch`，设置 `credentials: "omit"`，读取字节并通过
Web Crypto 校验。确认响应可读取而非 opaque，三个数据文件和配置哈希匹配。不使用
`mode: "no-cors"`、凭据、API Token 或手工 `If-None-Match`。跨域和缓存响应头由服务方
控制，浏览器访问行为变化时需重新检查，不将一次观测视为永久保证。

## 节点池运维

参见[自行部署 Worker/KV](workers.zh-CN.md)、[节点池协议](pool.zh-CN.md)。仓库变量
`POOL_ENABLED=false` 暂停池更新，保留旧文件且其时间继续变旧；`TCP_PRUNE_ENABLED=false`
只暂停 TCP 清理，仍更新观察和探测状态、执行七天过期及容量淘汰。两项未设置均默认 true。
没有配置读取接口时，不更新 TCP 结果、不执行 TCP 清理。读取失败为软错误，原始镜像和
源站观察仍可发布。池只从已有最新快照开始，不导入全部 Git 历史。

摘要 `pool` 报告读取状态／异常／待重试量、新增和总数、配置变化及复用字节数、已处理及
受保护批次、延期节点数、最旧探测年龄、未探测数。移除分为 `tcp_removed`、`expired`、
`capacity_evicted` 三种原因。KV 不可见或延迟绝不算失败。

发布后 `pool_visibility` 额外检查池目录、探测清单和一份配置的同 SHA CDN／Raw 文件。
这只是 HTTPS 下载验证，不是 Actions 对节点发起 TCP 探测。

池额外保留最多 64 MiB 当前引用的配置文件和最多 5,000 个节点的目录。按每天 48 次计算，
仅每次变化的 1 MiB 目录就会产生约 17.1 GiB/年的逻辑版本量，尚未计算 Git 压缩。
配置不变时复用 blob；新配置仍持续增加历史。失去引用后只删除工作树文件，不清除历史。

## 历史与依赖维护

2026-09-11 实现检查返回 100 个节点：CSV 1,347,159 字节，节点 JSON 1,384,056 字节，
国家 JSON 1,297 字节，合计约 2.73 MB。若每天 48 次都成功且数据有变化，按一年 365 天计算，
约 17,520 次快照、35,040 个提交，未压缩文件版本逻辑累计约 48 GB。这不是实际 Git pack 大小；压缩和差分
收益取决于真实内容与排列，不能预先保证比例。

运行后第 7、30 天记录体积，之后每月检查。GitHub 仓库 API 的 size 为近似 KiB；
`git count-objects -vH` 只有在完整历史检出后才可衡量本地历史体积，浅克隆无法代表远程历史。
建议 500 MiB 时评估、接近 1 GiB 时规划迁移。GitHub
[建议仓库理想情况下小于 1 GB，并强烈建议小于 5 GB](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)。

工作树只存当前文件，但删除工作树文件或旧目录**不会清除历史 Git 对象**。
不自动重写历史、force-push 或重置为孤立分支。确需迁移时，可在同账号建立后继公开仓库，
公告新的索引入口，保留旧仓库维持历史 SHA 链接。固定仓库、无限不可变历史和有界存储不能
永久同时保证。jsDelivr 可能保留已缓存内容，删除仓库也不是 CDN 撤销手段。

每周评审 Dependabot 的 Actions 更新，核实发行版本与 SHA 对应，保持完整 SHA 和版本注释。
runner 和 Python 实际补丁版本可从运行记录查看。协议升级遵循[数据协议](protocol.zh-CN.md#兼容性和升级)。
