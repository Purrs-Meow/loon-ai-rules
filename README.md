# Loon AI Rules

自动把 [ddgksf2013 的 AI 规则](https://ddgksf2013.top/filter/Ai.yaml)转换为 Loon 远程规则文件 `Ai.lsr`，保留规则顺序、匹配范围和原作者注释。

## 在 Loon 中使用

在 Loon 的「远程规则」中添加此地址，并选择你已有的代理策略或策略组：

```text
https://raw.githubusercontent.com/Purrs-Meow/loon-ai-rules/main/Ai.lsr
```

本仓库使用 `.lsr` 扩展名，内容是纯文本规则，每行形如 `DOMAIN,example.com` 或 `DOMAIN-SUFFIX,example.com`，不包含 YAML 的 `payload:` 和列表缩进，也不写死策略名称。文件扩展名本身不会转换规则格式。

完整保留上游的规则，包括 `cloudflare.com`、`amazonaws.com` 等较宽泛的后缀；请确认这些匹配范围符合自己的分流需求。脚本校验不代替 Loon 实机验证。

## 自动同步

- 每天 **21:37 UTC**（UTC+8 为次日 05:37）运行，GitHub 排程可能延迟
- 可在 **Actions → Sync AI rules → Run workflow** 手动运行
- 修改 README、转换脚本、测试或工作流也会触发检查与同步
- 仅在文件内容变化时提交 `Ai.lsr`，不添加每日时间戳或空提交
- 使用 GitHub 自带的 `GITHUB_TOKEN`，写权限仅授予同步任务，无需 PAT 或额外密钥
- 转换器只用 Python 标准库；`actions/checkout` 固定到官方 v7.0.1 的完整提交 SHA

### 失败时会怎样

HTTP 403、HTML 错误页、空文件、无效 UTF-8、未知规则类型或格式变化都会让任务失败，**不会覆盖上次有效的 `Ai.lsr`**。临时网络故障、429 和部分 5xx 最多尝试 3 次，403 不重试。

目前只接受上游使用的严格 YAML 子集：一个 `payload:` 字段、每项两空格缩进的标量列表，以及注释/空行；支持普通字符串、单引号字符串和 JSON 兼容的双引号字符串。规则仅支持 `DOMAIN` 和 `DOMAIN-SUFFIX`。不静默丢弃未知条目、不去重、不筛除宽泛域名。

少于 50 条规则，或比仓库现有规则减少超过 25%，也会中止。若上游确实大幅精简或新增类型，请先人工核对差异，再修改脚本中的阈值或实现并补充测试。不要为消除报错直接删除校验。

首次文件来自已经校验的上游快照：源标注更新日期 **2026-09-30**，共 **101 条**（27 条 DOMAIN、74 条 DOMAIN-SUFFIX）。后续是否成功联网更新，以 [Actions 运行记录](https://github.com/Purrs-Meow/loon-ai-rules/actions/workflows/sync.yml)和文件提交记录为准。

## 保持仓库活跃

GitHub 官方说明：[公开仓库连续 60 天没有仓库活动时，计划工作流会自动停用](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows)。每天运行任务不应被当作永久保活的保证，尤其是在上游长期没有变化时。

建议每 30–45 天检查一次运行记录，并在这里记录真实的维护结果、提交 README：

- 最近维护：2026-10-02，初始化转换脚本、校验与维护说明
- 下次维护时：检查最近成功运行时间、上游是否可访问、Loon 是否能正常更新，再补充日期和结果

如果已停用，进入 **Actions → Sync AI rules** 重新启用工作流，再执行一次 **Run workflow**。README 提交用于留下维护记录，不替代重新启用操作。

## 本地检查

Python 3.10+，无需安装依赖：

```sh
python3 -m unittest discover -s tests -v
python3 scripts/sync_rules.py
# 离线验证已保存的上游文件：
python3 scripts/sync_rules.py --input tests/fixtures/Ai.yaml --output /tmp/Ai-test.lsr
```

测试覆盖真实初始快照的逐条一致性、格式/类型错误、HTML/空响应、HTTP 和网络失败、规则骤减、失败后保留旧文件、原子替换失败与内容不变时不写入。

## 来源与权利

- 原规则：[ddgksf2013.top/filter/Ai.yaml](https://ddgksf2013.top/filter/Ai.yaml)
- 上游文件署名：[ddgksf2021](https://t.me/ddgksf2021)
- 本仓库只做格式适配，不声称拥有上游规则；原作者署名与注释均予保留
- 尚未确认适用于该规则文件的明确上游许可证，不为原规则授予新的许可；相关权利归各自权利人。使用、转载或分发前请自行核实授权范围
