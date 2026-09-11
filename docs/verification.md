# Initial publication verification / 首次发布验收

This is a historical verification record, not a live availability dashboard.
这是一次验收记录，不是实时可用性看板；最新数据时间以公开索引为准。

| Item / 项目 | Observed result / 结果 |
| --- | --- |
| Code revision / 代码提交 | `cdd2423bf6c1778ad59ef9c13aba2bfe6df1a9d5` |
| Offline checks / 离线检查 | 43 Python tests and 8 JavaScript tests passed locally and on GitHub |
| First publication / 首次同步 | [Successful Actions run 34606416524](https://github.com/GeorgeXie2333/vpngate-list-mirror/actions/runs/34606416524) |
| Data commit / 数据提交 | `e0ceca7dc8099c6bd1782596de41cb2e1a5b1530` |
| Successful fetch / 成功获取 | `2026-09-11T13:48:32.899Z` |
| Scope / 返回范围 | 97 unique nodes, 97 source records, 11 country/region buckets |
| Browser check / 浏览器检查 | Headless Chrome, HTTPS origin `https://example.com`, `2026-09-11T13:53:51.478Z` |
| Cross-origin read / 跨域读取 | Index, all three CDN files and all three same-commit Raw fallback files returned HTTP 200, readable `cors` responses |
| Configuration / 配置字节 | Python CLI, Node.js CLI and browser decoded the same selected JP configuration: 10,006 bytes, SHA-256 `a43046b89a3e449fe7d485b04ac791bb9e44485ecf325919772abe85f24862f5` |

Verified response bytes / 已验证的响应字节：

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `data/vpngate.csv` | 1,306,828 | `28b5bf7627b94011ce9f5c96343aefe815660f1adea18ca114cac899b19e19e1` |
| `data/servers.json` | 1,342,703 | `cb4c4fb2dfd2ca2c6f0337df001a70d0438faf4411eeecca013cb5758ac18e40` |
| `data/countries.json` | 1,320 | `948b5294e456340f2d51faa48fd453d3c25ca5cfcaeab7d183778efc57778ca6` |

The browser used `credentials: "omit"`, no authorization headers, and Web
Crypto hashing. The CDN failure was deliberately injected into the consumer to
exercise fallback; its subsequent same-SHA Raw downloads were real anonymous
HTTPS requests. No VPN connection was made and no configuration was executed.

Observed CDN cache header: `public, max-age=31536000, s-maxage=31536000, immutable`.
Observed Raw cache header: `max-age=300`. CDN served JSON as `application/json`
and CSV as `text/csv`; Raw served these as `text/plain`. All included UTF-8.
These observations do not promise future headers, a fixed refresh deadline,
worldwide simultaneous visibility or node connection availability.

浏览器不携带凭据或认证头，使用 Web Crypto 检查哈希。为验证回退，在消费层模拟 CDN
错误，随后对同一数据 SHA 的 Raw 地址进行了真实匿名 HTTPS 下载。没有连接 VPN 或执行
配置。上面的缓存和 MIME 类型是本次实测，不承诺未来响应头、固定刷新时限、全网同时
可见或节点可连接性。
