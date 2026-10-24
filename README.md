# Loon AI Rules

自动把 [ddgksf2013 的 AI 规则](https://ddgksf2013.top/filter/Ai.yaml)转换为 Loon 远程规则文件 `Ai.lsr`，保留规则顺序、匹配范围和原作者注释。

## 推荐：AI 域名与 ChatGPT 语音合并订阅

将原来的本仓库 AI Fix 订阅地址替换为以下地址，策略继续选择自己的 **AI 代理链**：

```text
https://raw.githubusercontent.com/Purrs-Meow/loon-ai-rules/main/Ai-Combined.lsr
```

保留你另外使用的 Kelee AI 订阅；本合并版只对本仓库来源去重，没有读取或修改 Kelee 规则。
使用合并版后，不必再同时订阅本仓库的 Ai.lsr 和 ChatGPT-Voice.lsr。
放在通用 IP 分流和最终兜底之前，所有条目统一绑定同一个 AI 策略。

- 当前合并结果：**99 条域名规则 + 23 条语音 IP，共 122 条**。
- 原 Ai.lsr 有 101 条；合并时移除已被 googleapis.com 覆盖的 proactivebackend-pa.googleapis.com，
  及已被 azureedge.net 覆盖的 production-openaicom-storage.azureedge.net，覆盖范围不减少。
- LiveKit 子域名已有 livekit.cloud 后缀覆盖，不重复添加。
- 保留源文件和原作者注释；Ai.lsr 的原样转换流程不变。去重只在合并产物中进行，
  不因 CDN/IP 解析关系删除域名，也不扩展为更大的 IP 网段。
- 两个同步工作流在更新各自来源后都重新生成合并版，并将相关文件放入同一提交。
  校验或合并失败时不会发布半成品；域名仍每 15 天更新，语音 IP 每日更新。
- 合并命令：`python3 -m scripts.merge_rules`。静态覆盖检查不代替 Loon 实机通话测试。

## 在 Loon 中使用原始域名版

在 Loon 的「远程规则」中添加此地址，并选择你已有的代理策略或策略组：

```text
https://raw.githubusercontent.com/Purrs-Meow/loon-ai-rules/main/Ai.lsr
```

本仓库使用 `.lsr` 扩展名，内容是纯文本规则，每行形如 `DOMAIN,example.com` 或 `DOMAIN-SUFFIX,example.com`，不包含 YAML 的 `payload:` 和列表缩进，也不写死策略名称。文件扩展名本身不会转换规则格式。

完整保留上游的规则，包括 `cloudflare.com`、`amazonaws.com` 等较宽泛的后缀；请确认这些匹配范围符合自己的分流需求。脚本校验不代替 Loon 实机验证。


## ChatGPT 语音补充（Loon）

现有 `Ai.lsr` 的 `DOMAIN-SUFFIX,livekit.cloud` 已覆盖
`host.livekit.cloud`、`chatgpt.livekit.cloud`、`turn.livekit.cloud` 及其子域名。
语音还可能直接连接服务器 IP，因此另提供 **[ChatGPT-Voice.lsr](ChatGPT-Voice.lsr)**，
从 [OpenAI 官方语音 IP 列表](https://openai.com/chatgpt-voice.json)生成。

### 添加到 Loon

保留现有 AI 域名订阅，再添加以下远程规则地址，选择同一个 AI 代理链策略：
```text
https://raw.githubusercontent.com/Purrs-Meow/loon-ai-rules/main/ChatGPT-Voice.lsr
```

本文件沿用仓库的 **Loon 纯文本远程规则格式**：不含 YAML 的 `payload:`，
不写死策略名称。IPv4 行形如：
```text
IP-CIDR,4.151.200.38/32,no-resolve
```
若官方未来增加 IPv6，则使用 `IP-CIDR6,IPv6前缀,no-resolve`。
在 Loon 中将该远程规则启用并指向自己的 AI 代理链，排在通用 IP 分流和最终兜底之前。
`no-resolve` 表示这份 IP 补充不为域名主动触发 DNS 解析，原 OpenAI/LiveKit 域名规则仍需保留。
仅刷新原来的 `Ai.lsr` 不会自动添加这个独立订阅。

[OpenAI 网络说明](https://help.openai.com/en/articles/9247338-network-recommendations-for-chatgpt-errors-on-web-and-apps)
说明语音优先使用 UDP 3478，也可使用 TCP 443。本补充匹配官方目标 IP 的所有端口，
不会把其他应用的全部 UDP 3478 流量一起接管；链式代理各跳仍需具备相应传输能力。

### 更新与保护

- **Sync ChatGPT Voice IPs** 每日 21:47 UTC（北京时间次日 05:47）更新语音列表，
  也支持手动运行；相关代码推送到 main 后会验证并更新。GitHub 排程可能延迟或被停用。
- 原 `Ai.lsr` 仍保持成功后满 15 天的同步周期；两个工作流共用并发锁。
- 只在语音规则内容变化时提交，不写每日时间戳，不改原来的成功计时记录。
- 复用现有 HTTPS 下载、大小限制、重试和原子写入；空列表、重复字段/前缀、
  地址族错误、非公网地址、IPv4 比 /16 更宽、IPv6 比 /32 更宽、
  超过 1,024 个前缀或条目数减少超过 25% 时中止，保留旧文件。官方确有这类变化时先人工核对。
- 初始官方快照于 **2026-10-24** 获取，包含 **23 条 IPv4 /32**，没有 IPv6。
  每次成功同步以 Actions 记录为准；测试不等于 Loon 实机语音连通性验证。
- 离线测试：`python3 -m unittest discover -s tests -v`；
  手动生成：`python3 -m scripts.sync_voice`。


## 自动同步

- 距离**上次成功同步满 15 天（360 小时）**后，才会再次自动下载上游并转换规则，跨月、跨年也按实际间隔计算
- GitHub cron 不能直接表示连续每隔 15 天，因此保留每天 **21:37 UTC**（UTC+8 为次日 05:37）的轻量检查；未到期会跳过下载，不改规则或成功时间
- 满 15 天后的首次定时检查会同步，通常有不到一天的检查间隔，GitHub 排程本身也可能延迟；这不是固定每月 1 日、16 日、31 日运行
- 可在 **Actions → Sync AI rules → Run workflow** 随时强制同步；成功后从这次成功时间重新计满 15 天
- 修改 README、转换脚本、测试或工作流只触发测试，不会提前下载上游
- 成功时间保存在 [`.sync-state.json`](.sync-state.json)。每次成功都会提交该记录，即使规则没有变化；`Ai.lsr` 仍只在内容变化时更新，不创建每日时间戳或空提交
- 下载或校验失败不更新成功时间；规则和成功时间放在同一提交中，推送失败也不会更新远端记录。到期失败后，下一次每日检查会再尝试；记录缺失时首次定时检查会同步，记录损坏时则报错，人工核对或手动成功同步后修复
- 使用 GitHub 自带的 `GITHUB_TOKEN`，写权限仅授予同步任务，无需 PAT 或额外密钥
- 转换器只用 Python 标准库；`actions/checkout` 固定到官方 v7.0.1 的完整提交 SHA

### 失败时会怎样

HTTP 403、HTML 错误页、空文件、无效 UTF-8、未知规则类型或格式变化都会让任务失败，**不会覆盖上次有效的 `Ai.lsr`**。临时网络故障、429 和部分 5xx 最多尝试 3 次，403 不重试。

目前只接受上游使用的严格 YAML 子集：一个 `payload:` 字段、每项两空格缩进的标量列表，以及注释/空行；支持普通字符串、单引号字符串和 JSON 兼容的双引号字符串。规则仅支持 `DOMAIN` 和 `DOMAIN-SUFFIX`。不静默丢弃未知条目、不去重、不筛除宽泛域名。

少于 50 条规则，或比仓库现有规则减少超过 25%，也会中止。若上游确实大幅精简或新增类型，请先人工核对差异，再修改脚本中的阈值或实现并补充测试。不要为消除报错直接删除校验。

首次文件来自已经校验的上游快照：源标注更新日期 **2026-09-30**，共 **101 条**（27 条 DOMAIN、74 条 DOMAIN-SUFFIX）。后续是否成功联网更新，以 [Actions 运行记录](https://github.com/Purrs-Meow/loon-ai-rules/actions/workflows/sync.yml)和文件提交记录为准。

## 保持仓库活跃

GitHub 官方说明：[公开仓库连续 60 天没有仓库活动时，计划工作流会自动停用](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows)。每天的轻量检查不应被当作永久保活的保证。每次成功同步会提交实际成功时间，但仍建议定期检查任务是否正常。

建议每 30–45 天检查一次运行记录，并在这里记录真实的维护结果、提交 README：

- 最近维护：2026-10-02，改为按上次成功时间间隔 15 天同步，保留手动强制同步，补充跨月、失败与计时记录测试
- 下次维护时：检查最近成功运行时间、上游是否可访问、Loon 是否能正常更新，再补充日期和结果

如果已停用，进入 **Actions → Sync AI rules** 重新启用工作流，再执行一次 **Run workflow**。README 提交用于留下维护记录，不替代重新启用操作。

## 本地检查

Python 3.10+，无需安装依赖：

```sh
python3 -m unittest discover -s tests -v
python3 scripts/sync_rules.py
# 仅检查当前是否到期（不下载、不写成功记录）：
python3 -m scripts.sync_interval check --event schedule
# 离线验证已保存的上游文件：
python3 scripts/sync_rules.py --input tests/fixtures/Ai.yaml --output /tmp/Ai-test.lsr
```

测试覆盖真实初始快照的逐条一致性、格式/类型错误、HTML/空响应、HTTP 和网络失败、规则骤减、失败后保留旧文件、原子替换失败与内容不变时不写入；另覆盖 15 天边界、跨月/跨年/闰年、未到期跳过、手动强制、无变化成功记录、损坏/未来时间与同步失败不更新远端记录。

## 来源与权利

- 原规则：[ddgksf2013.top/filter/Ai.yaml](https://ddgksf2013.top/filter/Ai.yaml)
- 上游文件署名：[ddgksf2021](https://t.me/ddgksf2021)
- 本仓库做格式适配并另提供去重合并版，不声称拥有上游规则；原作者署名与注释均予保留
- 尚未确认适用于该规则文件的明确上游许可证，不为原规则授予新的许可；相关权利归各自权利人。使用、转载或分发前请自行核实授权范围
