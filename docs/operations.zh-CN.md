# 运维说明

[English](operations.md) · [数据协议](protocol.zh-CN.md)

## 最少 GitHub 设置

仓库保持公开，默认分支设为 `main`，启用 Actions 并允许固定 SHA 的官方 checkout 和
setup-python Action，使用标准托管 Ubuntu runner。全局默认令牌可继续只读，只有
`sync.yml` 的发布 job 请求 `contents: write`。不需要更大付费 runner 或每次运行都上传数据 artifact。

目标分支必须允许内置 `GITHUB_TOKEN` 正常快进推送。无法满足的强制 PR、签名或状态检查
会阻止发布，不要通过允许 force-push 绕过。若组织要求代码分支仅可通过 PR 修改，应评审后
改用独立数据发布分支并更新索引 URL。默认单仓库方案无需 PAT、额外 GitHub App、Pages、
数据库、自建服务器、秘密、CDN 账号或 purge 权限。消费端不用 Token。
公开仓库标准 runner 用量适用 [GitHub 免费规则](https://docs.github.com/en/billing/concepts/product-billing/github-actions)。

## 刷新与调度

在 **Actions → Sync VPN Gate → Run workflow** 选择 `main`。已认证的维护者 CLI 可运行：

```bash
gh workflow run sync.yml --repo GeorgeXie2333/vpngate-list-mirror --ref main
gh run list --repo GeorgeXie2333/vpngate-list-mirror --workflow sync.yml --limit 5
```

调度表达式 `17 */4 * * *` 使用 UTC，每 4 小时执行一次，即每天 00:17、04:17、08:17、
12:17、16:17、20:17，共 6 次。文件必须位于默认分支，计划任务只在默认分支运行。
非 `main` 的手动发布被跳过。固定 concurrency group 配合 `cancel-in-progress: false`
避免活动中的定时与手动同步互相覆盖，但 GitHub 可能替换旧的待运行任务。
繁忙时调度可能延迟或丢弃；公开仓库连续 60 天无活动时可自动停用，需要到 Actions 重新启用。
这些限制见[官方文档](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)。

**Optional live source check** 工作流或 `python -m mirror check-live` 只获取并校验真实
HTTPS 响应，不发布。普通 PR 与代码 push 检查只使用离线样例。

## 发布事务

同步命令并行运行完整的 Python、JavaScript 离线测试和一次源站获取。只有两个测试套件均
通过、获取也成功后，才在内存生成并校验数据；测试子进程不接收发布令牌。浅克隆发布分支头到
自己拥有的临时检出目录。若远程相对已测试代码提交发生代码、协议或生成数据变更，则停止；
纯文档变更可以保留。只暂存三个允许的数据路径及 `latest.json`。

内容变化时，本地提交图为 `远程 H → 数据 D → 索引 I`。读取 D 的实际完整 SHA 写入 I，
完成全部校验后一次正常快进推送 I，使两个提交一起可用，避免提交自引用。
不能用工作流的 `GITHUB_SHA` 代替 D。内容相同时复用 D 和数据生成时间，仅提交更新获取
时间的小型索引，不给大文件加入时间戳。

推送被拒后读取远程，不 force-push。若只是并发文档提交，则在新头上重新生成 D 和 I，
最多三次推送；不能 rebase D 后继续使用 I 中旧的 SHA。并发代码或数据变更明确失败；
已经发布的较新获取结果会取代本次旧结果。推送确认丢失时先检查远程头／索引，避免重复发布。
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
| 源站超时、429、5xx | 查看有限重试结果，保留旧快照，稍后手动重试 |
| 空列表、截断、格式或配置异常 | 查看错误及源站变化，增加离线回归样例后再改校验规则 |
| 远程未变化但推送被拒 | 检查 job 权限、仓库和分支规则；不 force-push |
| 并发代码／数据变更 | 评审变更后从最新 main 重新运行 |
| 索引看起来较旧 | 比较 fetched_at、Actions 最近结果及 Raw 缓存；查询参数不能保证即时刷新 |
| 新 SHA 的 CDN 地址失败 | 尝试同 SHA Raw；二者均无法校验则保留旧缓存 |
| 哈希或跨文件不匹配 | 拒绝候选，检查索引对应运行和固定提交文件 |
| 协议不支持 | 保留旧数据、展示其年龄，升级消费端 |
| 国家没有节点 | 不断言离线；选择本次实际返回的国家或等待下次目录 |

浏览器应从 HTTPS 来源进行匿名 `fetch`，设置 `credentials: "omit"`，读取字节并通过
Web Crypto 校验。确认响应可读取而非 opaque，三个数据文件和配置哈希匹配。不使用
`mode: "no-cors"`、凭据、API Token 或手工 `If-None-Match`。跨域和缓存响应头由服务方
控制，浏览器访问行为变化时需重新检查，不将一次观测视为永久保证。

## 历史与依赖维护

2026-09-11 实现检查返回 100 个节点：CSV 1,347,159 字节，节点 JSON 1,384,056 字节，
国家 JSON 1,297 字节，合计约 2.73 MB。若每天 6 次都成功且数据有变化，按一年 365 天计算，
约 2,190 次快照、4,380 个提交，未压缩文件版本逻辑累计约 6 GB。这不是实际 Git pack 大小；压缩和差分
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
