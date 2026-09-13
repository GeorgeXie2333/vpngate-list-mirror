# 数据协议 v1

[English](protocol.md) · [中文 README](../README.zh-CN.md)

## 文件和版本发现

可变版本入口是
`https://raw.githubusercontent.com/GeorgeXie2333/vpngate-list-mirror/main/latest.json`。
每次刷新只读取一次索引。三个数据文件均固定到其中同一个 `data_commit`：

```text
https://cdn.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@DATA_COMMIT/PATH
https://raw.githubusercontent.com/GeorgeXie2333/vpngate-list-mirror/DATA_COMMIT/PATH
```

`data_commit` 为完整 40 位小写 Git SHA，表示数据版本；`schema_version` 表示协议版本。
索引不包含自身哈希或所属提交 SHA。在数据提交 D 下读到的 `latest.json`（若存在）属于
更早一次发布，不能递归跟随；当前发现入口是默认分支的索引。

| 索引必需字段 | 含义 |
| --- | --- |
| `schema_version` | 整数 `1` |
| `source_url` | 固定为 `https://www.vpngate.net/api/iphone/` |
| `source_updated_at` | 可靠源站目录更新时间或 `null`；目前为 `null` |
| `fetched_at` | 最近一次完整获取并通过校验、与当前数据匹配的时间 |
| `generated_at` | 当前数据文件生成时间；内容未变时保持原值 |
| `index_generated_at` | 索引在提交和推送前的生成时间 |
| `data_commit` | 完整数据提交 SHA |
| `source_record_count` | CSV 原始记录数，包含完全重复记录 |
| `server_count` | 规范化后的唯一节点数 |
| `country_count` | 国家代码分组数；出现未知分组时也计入 |
| `files` | 三个固定数据路径的完整性描述 |
| `workflow_run_url` | 含推送结果和可见性探测的公开 Actions 运行记录 |

每个文件描述包含 64 位小写 `sha256`、正整数 `bytes` 和相同的唯一 `server_count`。
CSV 实际行记录数由 `source_record_count` 表达，国家分组数由 `country_count` 表达。
哈希覆盖存储的精确字节，包括空白与末尾换行。消费端应对 HTTP 解压后的文件字节求哈希，
不能对压缩传输字节或重新序列化 JSON 求哈希。

所有时间采用 UTC RFC 3339，固定三位毫秒，例如 `2026-09-11T12:17:42.123Z`。
索引生成时间不早于获取时间和数据生成时间。相同内容再次获取时，数据生成时间可以早于
最新获取时间。HTTP `Date`、节点 uptime 和本地文件时间都不是源站目录更新时间。
实际 `push_confirmed_at` 及各端点 `verified_at` 只记入 Actions 摘要，避免新增自引用提交。

## 原始 CSV 与校验

`data/vpngate.csv` 原样保存成功响应的 UTF-8 字节，包括可选 BOM、换行、全部列、引号、
空值和标记；`.gitattributes` 对它禁用文本换行转换。
首记录必须为 `*vpn_servers`，表头首项以 `#` 标记，结束记录只含 `*`。
数据间及结束后可有空记录，结束后非空内容失败。表头名称不得重复，必需列必须存在，
每条记录列数必须与表头一致。允许列重新排序和新增列，新增原值保留在 CSV。
解析使用 Python 严格 CSV reader，正确处理引号内逗号、转义引号和跨行字段。

必需列为 `HostName`、`IP`、`Score`、`Ping`、`Speed`、`CountryLong`、`CountryShort`、
`NumVpnSessions`、`OpenVPN_ConfigData_Base64`。Uptime、累计计数、Operator、Message
等其他源站列完整保留在 CSV，不为它们臆造额外规范化语义。

## 节点字段

`data/servers.json` 包含 `schema_version`、`source_csv_sha256`、`server_count`、`servers`。
CSV 哈希必须等于索引对应值；节点按 `id` 升序排列。

| 节点字段 | 类型和约定 |
| --- | --- |
| `id` | `v1:` 加 64 位小写 SHA-256 |
| `hostname` | 原始 CSV `HostName` 标识，可含下划线，不保证是 DNS 域名 |
| `ip` | 规范化 IPv4/IPv6；拒绝带 scope、未指定、多播和回环地址 |
| `country_code` | 源站两字母代码转大写；非两字母值、`XX`、`ZZ` 转为 `null` |
| `country_name` | 源站非空名称原文或 `null`，不额外定位或改名 |
| `score` | 上游评分，无单位，非负安全整数或 `null` |
| `ping_ms` | 上游 Ping，毫秒，非负安全整数或 `null` |
| `speed_bps` | 上游速度，bit/s，非负安全整数或 `null` |
| `num_vpn_sessions` | 上游会话数，非负安全整数或 `null` |
| `openvpn_config_base64` | 完整原始 Base64 字段，不截断、不重编码后保存 |
| `openvpn_config_sha256` | 解码后的原始配置字节 SHA-256 |
| `openvpn_config_bytes` | 解码后的原始配置字节数 |

数值空字段转 `null`，只有 Ping 额外接受 `-` 表示未知；0 保留为0。畸形数字、负数、
小数和超过 `9007199254740991` 的数值失败，不能悄悄当作未知。
未知国家代码的原值仍在 CSV 中。这些指标均来自上游，没有 `online` 或实测在线字段。

`hostname` 原样保存；校验和 ID 生成使用去两端空白、转小写、去一个末尾点后的形式。
每个点分隔段长 1–63 个字符，允许 ASCII 字母、数字、下划线和连字符，段首尾不能是连字符；
规范化后总长不超过 253，原字段不超过 254。例如上游会返回 `_unregistered_vpn335506854`。
不要从这个元数据推导连接地址或追加 DNS 后缀。OpenVPN `remote` 仍单独校验 IP／DNS 地址，
TCP 探测仍只使用通过校验的数字 IP 和端口。此前复制过消费端示例的使用者需更新名称校验器；
本次修正不改变 v1 JSON 结构和已有 ID 的规范化规则。

ID 的输入为精确 UTF-8 字节串：

```text
vpngate-node-v1<NUL>normalized_hostname<NUL>canonical_ip
```

输出为 `v1:` 加该字节串 SHA-256。国家、指标、端口和配置不参与；IP 或主机名变化产生新 ID。
这是目录记录身份，不是永久设备身份。相同 ID 的完整 CSV 行完全一致时去重；任何字段
不同（包括额外列）都视为冲突，整次失败，不任意选择一条。

Base64 严格检查规范编码及补位；解码内容须为 UTF-8 文本，控制字符只允许 CR/LF/tab。
基本结构检查要求 `client`、支持的 `dev`、合法 `remote` 地址和端口、有效的显式传输协议、
成对内联块和含证书界定符的 CA 块。客户端 cert/key 块若出现必须配对。
这不是完整 OpenVPN 解析器、证书认证、安全执行许可或连通性检查。
其他指令原样保留且从不执行；解码保存时不得改换行或重新编码文本。

## 国家／地区统计

`data/countries.json` 同样包含协议版本、CSV 哈希、唯一节点数，另有 `countries` 数组。
每组含 `code`、`names` 和 `server_count`；`names` 为源站非空原始名称去重后按 Unicode
码点排序的数组，同一代码下不同名称全部保留。未知桶为 `code: null`，名称可为空数组，
界面可显示“未知”。已知代码按字母排序，未知桶最后。各组数量总和必须等于唯一节点数。

## 限制与失败保护

源响应最多 8 MiB，解码配置最多 128 KiB（编码字段最多 174,764 字符），包含重复的原始
记录最多 5,000 条、表头最多 64 列，每个发布文件最多 16 MiB，消费端索引最多 64 KiB。
网络操作超时 20 秒，每个响应在读取间检查 60 秒总时限；已开始的读取仍受 socket 超时控制。
暂时连接错误、429 和 5xx 最多请求三次；源请求要求 identity 编码，不支持的压缩明确失败。
HTTPS 重定向只允许原主机和标准端口，最多两次。

必须校验完整响应。空列表、缺结束标记、关键字段错误、坏配置或冲突重复都会保留之前成功
发布的数据和索引。收到 HTTP 200 并不等于允许发布；三个文件校验完后才更新远程分支引用。

完整 Schema 位于 [`schemas/v1`](../schemas/v1/)。运行代码通过标准库显式检查相应约束及
跨文件语义，没有自行实现通用 JSON Schema 引擎。使用外部 Schema 校验器仍须额外验证哈希、
数量和版本关联。

## 兼容性和升级

同一 `schema_version: 1` 可增加可选字段，消费端忽略未知字段；既有必需字段、类型、单位、
ID 规则和含义保持兼容。上游 CSV 仍是上游协议，字段变化不能被悄悄解释成新语义。
破坏性变更使用新版本目录和索引，例如 `v2/latest.json` 与 `data/v2/`，同步升级 Schema、
两种消费端和测试，并公告至少 90 天并行迁移期。冻结旧版本后保留真实的最后成功时间。
不支持的协议明确失败并保留旧成功缓存；不重写不可变提交，也不把旧字节改称新协议。
