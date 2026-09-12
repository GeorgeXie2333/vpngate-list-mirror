# Contributing

Use a branch and pull request for code, schema, tests or documentation changes.
Do not hand-edit `data/*`, `latest.json` or `pool/*`; the publisher owns those paths.
Keep English and Chinese documentation consistent.

Run from the repository root with Python 3.13+ and Node.js 22+:

```bash
python -m unittest discover -s tests -v
node --test tests/test_consumer.mjs tests/test_pool_consumer.mjs workers/test/core.test.mjs
```

Tests must stay offline by default. Use small synthetic fixtures and temporary
local Git repositories. Do not add VPN connection tests, execute configuration
directives, create network interfaces, or alter system networking. A live source
check is optional: `python -m mirror check-live` or the manually dispatched
**Optional live source check** workflow. It does not publish.

Runtime Python code uses the standard library. Avoid adding dependencies unless
they solve a concrete requirement. Official Actions must remain pinned to
verified complete commit SHAs, with their release versions in comments. Review
Dependabot updates and their checks before merging them.

All node TCP connections belong in `workers/src/`, never in Python or Actions.
Worker tests inject fake sockets, clocks, HTTPS responses and KV; CI must never
connect to real node addresses. Wrangler is a pinned deployment-only development
dependency. After `cd workers && npm ci`, `npm run check` bundles locally without
deployment. Follow [the manual deployment and acceptance guide](docs/workers.md)
for real socket, CPU and KV checks; a passing offline test is not that live check.

For parser changes, include a fixture reproducing the changed upstream format
and verify that malformed responses still retain the last successful snapshot.
For publication changes, cover races, rejected pushes, uncertain acknowledgments,
the two-commit reference and unchanged-data freshness. For consumer changes,
cover stale indexes, fallback, byte integrity and all-or-nothing cache updates.

Protocol changes follow [the upgrade procedure](docs/protocol.md#compatibility-and-upgrades).
Update the affected schema documents, consumer examples, tests and both
language versions of the documentation together. Tests do not implement a
general JSON Schema engine: runtime checks explicitly enforce the required
fields and semantic cross-file constraints.
The independently versioned pool protocol is documented in [pool.md](docs/pool.md).

贡献请通过分支和 PR，保持双语文档同步。不要手工编辑生成数据或索引；默认测试必须离线。
采集器和示例只能读取、校验和保存公开配置，不能连接 VPN 或执行配置中的命令。
协议变更须同时更新 Schema、消费端示例、测试及双语文档。
