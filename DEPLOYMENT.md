# GW/AP Debug Skill-only MVP 部署指南

本文适用于 `gw-ap-debug` v0.6.0。该分支是独立的 Skill-only MVP，
不是 Debug Platform `main` 分支的后续版本。它不需要 Vue 前端、Docker、
PostgreSQL 或 Qdrant。首选运行环境是 Windows 11 上的 Claude Code CLI；
Codex CLI 是首选备用入口，OpenCode CLI 是另一兼容入口。诊断推理始终使用
当前宿主 CLI 会话已经选择的模型。

## 1. 运行要求

- Windows 11、Linux 或 macOS；
- Git；Windows 使用系统自带 Windows PowerShell 5.1 或更新版本；
- Python 3.11、3.12、3.13 或 3.14；
- 首选已安装并登录 [Claude Code CLI](https://code.claude.com/docs/en/setup)，
  也可以只安装 Codex CLI 或 OpenCode CLI；
- 首次 `bootstrap` 时可访问 Python 依赖源；
- 至少一份 GW/AP 日志、无后缀 `collectDebuginfo`、日志目录或安全归档。

Skill 不需要也不会索取当前 CLI 模型的 API Key。模型提供商、模型名称和
凭据继续由宿主 CLI 自己管理。

## 2. 新电脑推荐安装

推荐把唯一的 Git 检出直接放在 Claude Code 的用户级 Skill 目录，再用仓库自带
脚本为 Codex 和 OpenCode 创建指向它的目录联接。这样 Claude 的主路径是真实目录，
三个 CLI 仍共享同一份可更新、可校验、可回滚的副本。安装器会自动查找
Python 3.14、3.13、3.12 或 3.11，并优先注册 Claude Code。

### 2.1 Windows 11（PowerShell）

```powershell
$skillDir = Join-Path $HOME ".claude\skills\gw-ap-debug"
git clone --branch skillonly --single-branch https://github.com/mrrabbitss/debug-platform.git $skillDir
powershell -NoProfile -ExecutionPolicy Bypass -File "$skillDir\scripts\setup_user_skill.ps1" -Clients All -RunBootstrap -RunValidation
```

脚本会识别 Claude 的真实检出目录，并注册以下三个入口：

- Codex：`~/.agents/skills/gw-ap-debug`；
- Claude Code：`~/.claude/skills/gw-ap-debug`（真实 Git 检出）；
- OpenCode：`~/.config/opencode/skills/gw-ap-debug`。

Codex 和 OpenCode 入口是指向 `$skillDir` 的目录联接；所有更新仍只发生在这一份
Git 检出中。

它不会覆盖已存在的普通目录，也不会改写指向其他位置的链接。先预览而不修改时，
加上 `-PlanOnly`。只安装某一个 CLI 时使用 `-Clients Codex`、
`-Clients Claude` 或 `-Clients OpenCode`。未安装的辅助 CLI 只会产生提示，不会
导致 Skill 安装失败。

### 2.2 Linux / macOS

```bash
skill_dir="${XDG_DATA_HOME:-$HOME/.local/share}/gw-ap-debug-skill"
git clone --branch skillonly --single-branch https://github.com/mrrabbitss/debug-platform.git "$skill_dir"
bash "$skill_dir/scripts/setup_user_skill.sh" --clients all --bootstrap --validate
```

先预览时加 `--plan`；只注册部分 CLI 时，例如使用
`--clients codex,claude`。

### 2.3 Claude Code 首次使用

安装完成后，在任意工作目录启动 `claude`，输入 `/skills` 确认
`gw-ap-debug` 已出现，然后一条消息启动诊断：

```text
/gw-ap-debug D:\logs\gw D:\logs\ap 诊断 AP 频繁离线；使用当前 Claude 模型完成诊断，并允许读取内置方法和本地限界脱敏证据
```

路径、问题描述和宿主模型授权已经完整时，Skill 会直接开始，不再逐项询问。
它会在准备、证据检索、最终校验三个阶段给出简短进度，并在校验通过后返回最终
报告。如果安装前 Claude Code 已经处于运行状态且 `/skills` 没有刷新，重启一次
Claude Code。如果 `/skills` 已列出 `gw-ap-debug`，但首次调用仍提示 Unknown，先让
Claude 执行一次无害的只读工具调用再重试；仍失败时确认该路径是真实检出目录而非
目录联接。

## 3. 手动从 `skillonly` 分支安装

仓库地址：`https://github.com/mrrabbitss/debug-platform.git`

### 3.1 Claude Code 安装

进入项目根目录后执行：

```powershell
git clone --branch skillonly --single-branch https://github.com/mrrabbitss/debug-platform.git .claude/skills/gw-ap-debug
```

### 3.2 Codex 与 OpenCode 共用安装

两者都能发现 `.agents/skills`。进入需要使用该 Skill 的项目根目录后执行：

```powershell
git clone --branch skillonly --single-branch https://github.com/mrrabbitss/debug-platform.git .agents/skills/gw-ap-debug
```

如果 OpenCode 设置了 `OPENCODE_DISABLE_EXTERNAL_SKILLS=1`，改为安装到其
专用目录：

```powershell
git clone --branch skillonly --single-branch https://github.com/mrrabbitss/debug-platform.git .opencode/skills/gw-ap-debug
```

### 3.3 用户级安装

需要让多个项目共用时，可以安装到用户目录：

| CLI | 用户级目录 |
| --- | --- |
| Codex | `~/.agents/skills/gw-ap-debug` |
| Claude Code | `~/.claude/skills/gw-ap-debug` |
| OpenCode | `~/.config/opencode/skills/gw-ap-debug` |

例如在 PowerShell 中为 Claude Code 安装：

```powershell
git clone --branch skillonly --single-branch https://github.com/mrrabbitss/debug-platform.git "$HOME/.claude/skills/gw-ap-debug"
```

目标目录已存在时不要覆盖或嵌套克隆；应进入已有目录执行本文的更新流程。

## 4. 初始化运行环境

将 `<SKILL_DIR>` 替换为包含 `SKILL.md` 的绝对目录。Windows 11 使用自带的
PowerShell 包装器，它会自动找到兼容 Python：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<SKILL_DIR>\scripts\gw_ap_debug.ps1" doctor --check package
```

首次安装或锁文件发生变化时初始化独立后端环境：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<SKILL_DIR>\scripts\gw_ap_debug.ps1" bootstrap
powershell -NoProfile -ExecutionPolicy Bypass -File "<SKILL_DIR>\scripts\gw_ap_debug.ps1" doctor --check host-agent
```

Linux/macOS 对应入口为 `bash "<SKILL_DIR>/scripts/gw_ap_debug.sh"`。

虚拟环境、SQLite 数据库、上传日志、运行日志、验证密钥和诊断输出不会写入
Skill 安装目录。默认状态目录为：

- Windows：`%LOCALAPPDATA%\gw-ap-debug`
- Linux/macOS：`$XDG_STATE_HOME/gw-ap-debug`，未设置时为
  `~/.local/state/gw-ap-debug`

需要自定义时，在启动 CLI 前设置 `GW_AP_DEBUG_STATE_DIR`：

```powershell
$env:GW_AP_DEBUG_STATE_DIR = "D:\gw-ap-debug-state"
```

不要把状态目录放进 Git 仓库或 Skill 安装目录。

### 4.1 诊断前加入另一个完整 Skill 的知识

如已有一个日志分析/综合诊断 Skill，可先预览它会加入哪些 Markdown：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<SKILL_DIR>\scripts\gw_ap_debug.ps1" import-skill-methods --skill "D:\skills\complete-network-diagnosis" --dry-run
```

默认不需要把它装成长期知识。第 5 节的 `run --diagnostic-skill` 会为本次案例生成
一份不可变方法 generation，通过案例绑定仅供本次后端分析读取，并保持当前
persistent active generation 不变。后端作业已确认终止时会释放活动绑定；若等待超时或
网络结果不确定，则保留并续租到完整但有限的 24 小时边界，避免仍在排队的作业错误
回退到 persistent 方法。每个 `--job-timeout`、两个 HTTP 超时窗口和 60 秒调度余量
必须共同落在该边界内；超过 24 小时的后端排队只属于尽力保留范围，租约到期后会
自动失效。

即使本次没有外部 Skill，`run`/`diagnose` 也会把当时的 persistent generation
内容寻址为一个 case snapshot 并绑定，防止并发 persistent 导入令同一案例的分诊与
综合分析读取到两套方法。

只有明确希望后续诊断继续使用时，才去掉 `--dry-run` 完成 persistent 导入。
导入内容会在外部状态目录中变成有来源路径和 SHA-256 的方法包，并与当前基础
故障树、日志分析方法合并；不会执行被导入 Skill 中的脚本或命令。查看和撤销
persistent 方法包：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<SKILL_DIR>\scripts\gw_ap_debug.ps1" list-method-packs
powershell -NoProfile -ExecutionPolicy Bypass -File "<SKILL_DIR>\scripts\gw_ap_debug.ps1" remove-method-pack --name "complete-network-diagnosis"
```

Skill 格式、自动分类标题、显式 metadata 映射和限制详见
[`references/composable-knowledge.md`](references/composable-knowledge.md)。

持久化组合使用 `<state>\method-packs\active.json` 原子切换到新的不可变
generation，不会原地改写正在使用的方法文件。案例绑定位于
`<state>\method-packs\bindings`；generation 位于
`<state>\method-packs\generations`。

## 5. 使用当前 CLI 内置模型诊断

在 Claude Code 中优先直接使用 `/gw-ap-debug`。在其他 CLI 中明确要求使用
`gw-ap-debug` Skill，并授权当前模型读取内置诊断方法和
经过本地限界、脱敏后的证据。原始日志不会发送给宿主模型。

也可以由 CLI 按 Skill 指令执行以下准备命令：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<SKILL_DIR>\scripts\gw_ap_debug.ps1" run --mode host-agent --approve-host-model-egress --title "问题标题" --diagnostic-skill "D:\skills\complete-network-diagnosis" --gw-log "D:\logs\gw" --ap-log "D:\logs\ap"
```

至少提供 `--gw-log`、`--ap-log` 或 `--log` 之一。输入可以是单个日志、
无后缀 `collectDebuginfo`、目录或受支持的归档。没有外部诊断 Skill 时省略
`--diagnostic-skill`；多个 Skill 可重复填写该参数。该参数会先解析/合并知识，再
开始分诊和综合诊断。默认 `--diagnostic-skill-scope run`，不会把外部 Skill
加入长期 registry。只有用户明确要求安装为后续默认知识时，才添加：

```text
--diagnostic-skill-scope persistent
```

如果单个外部 Skill 的标题无法被稳定自动分类，又没有配置 role metadata，可在
`run` 或 `diagnose` 中显式指定相对于该 Skill 根目录的 Markdown（参数均可重复）：

```text
--diagnostic-skill-fault-tree "references\tree.md" --diagnostic-skill-log-analysis "references\patterns.md"
```

显式 role 路径只允许和恰好一个 `--diagnostic-skill` 一起使用；多个 Skill 应分别
在各自 `SKILL.md` 的 metadata 中声明映射。多 Skill 与显式路径混用会直接拒绝，
避免把相对路径解析到错误的 Skill 根目录。该约束对 run 和 persistent scope 一致。

host-agent 模式会在创建案例前估算基础方法、persistent 方法包和本次外部 Skill
合并后的总方法体积。默认上限为 120,000 个保守估算 token；超过时命令会停止，
不会悄悄截断知识。优先通过外部 Skill metadata 或显式 role 路径缩小输入。只有
确认当前宿主模型上下文足够容纳方法、证据和输出后，才显式提高，例如：

```text
--max-host-method-tokens 160000
```

该数值采用 UTF-8 字节数作为跨 CLI、无 tokenizer 的保守上界代理；它不是
Claude Code、Codex 或 OpenCode 提供的可信实时 token 计数。

对已有案例重新分析并直接生成宿主模型工作包：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<SKILL_DIR>\scripts\gw_ap_debug.ps1" diagnose --case-id "CASE-..." --mode host-agent --approve-host-model-egress --diagnostic-skill "D:\skills\complete-network-diagnosis"
```

若案例已有完成的 analysis、只需重新导出工作包，使用 `result --mode host-agent`。
导出器会按 analysis 中的内容哈希搜索已保留且校验通过的不可变 generation，而不是
误用当前 active 方法。

`run` 只完成本地准备，不是最终诊断。CLI 必须继续读取输出目录中的
`host-agent-instructions.md`，完成只读工具循环，并且仅在以下两个条件同时成立
后展示结果：

- `host-validation.json` 中 `accepted` 为 `true`；
- `manifest.json` 中 `host_agent.status` 为 `VALIDATED`。

最终报告为输出目录中的 `host-diagnosis.md`。

## 6. 无模型的确定性模式

只验证上传、解析、规则诊断和报告链路时，可以完全不调用模型：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<SKILL_DIR>\scripts\gw_ap_debug.ps1" run --mode deterministic --title "本地冒烟" --log "D:\logs\collectDebuginfo.txt"
```

确定性模式适合安装验收，但不等同于宿主模型完成的故障树综合诊断。

## 7. 安装后验收

在 `<SKILL_DIR>` 中执行：

```powershell
python -B -m unittest discover -s tests -v
python -B scripts/check_provenance.py
python -B scripts/validate_release.py
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\gw_ap_debug.ps1 doctor --check host-agent
```

预期结果：

- 当前包中的全部单元测试通过；
- provenance 输出 `"ok": true`；
- release validation 输出 `"ok": true`；
- host-agent doctor 没有缺失依赖或版本错误。

随后使用一份允许测试的日志完成一次真实宿主 CLI 诊断，并确认最终验证状态。

来源映射是维护者审计，不是 `--single-branch skillonly` 新机安装的必过步骤；后者
通常不包含记录的 `main` 提交。持有主仓库检出时另行执行：

```powershell
python -B scripts/check_source_mapping.py --source-repo "D:\path\to\debugplatform-main-checkout"
```

预期 `"status": "PASS"`。GitHub Actions 的独立 source-mapping job 会获取完整历史并
执行同一审计；普通使用者不应把因缺少主分支对象产生的 `SKIPPED` 当成安装失败。

## 8. 更新与回滚

推荐用注册脚本更新。它只允许 fast-forward，不会自动合并或覆盖本地修改：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<SKILL_DIR>\scripts\setup_user_skill.ps1" -Clients All -Update -RunBootstrap -RunValidation
```

```bash
bash "<SKILL_DIR>/scripts/setup_user_skill.sh" --clients all --update --bootstrap --validate
```

也可以手动更新现有安装：

```powershell
git -C "<SKILL_DIR>" pull --ff-only origin skillonly
powershell -NoProfile -ExecutionPolicy Bypass -File "<SKILL_DIR>\scripts\gw_ap_debug.ps1" bootstrap
powershell -NoProfile -ExecutionPolicy Bypass -File "<SKILL_DIR>\scripts\gw_ap_debug.ps1" doctor --check host-agent
```

运行状态位于 Skill 外部，正常更新不会删除已有案例。更新前仍建议备份自定义状态
目录。需要回滚时，先查看分支提交记录，再检出明确的已知提交；不要删除状态目录
来代替代码回滚。

## 9. 常见问题

### CLI 没有发现 Skill

- 确认路径末端是 `gw-ap-debug/SKILL.md`，而不是多嵌套了一层仓库目录；
- Claude Code 中先运行 `/skills`；顶层 Skill 目录是会话启动后首次创建时重启一次；
- 若 `/skills` 可见但 `/gw-ap-debug` 首次提示 Unknown，先执行一次无害的只读工具
  调用后重试；持续失败时把仓库直接 clone 到 `~/.claude/skills/gw-ap-debug`，不要用
  目录联接作为 Claude 的主入口；
- OpenCode 禁用外部 Skill 时使用 `.opencode/skills`；
- Claude Code 使用 `.claude/skills`，Codex 使用 `.agents/skills`。

### `doctor` 报 Python 版本不支持

安装 64 位 Python 3.11 至 3.14。Windows 包装器会依次检查 Python Launcher
中的 3.14、3.13、3.12、3.11，再检查 PATH；通常不需要手动调整 `python`。

### 更新后提示环境指纹不匹配

重新执行 `bootstrap`。该检查用于防止旧虚拟环境与新锁文件混用。

### OpenCode 返回 429 或余额不足

这是宿主模型提供商的账户或资源包问题，不是 Skill 安装失败。充值或切换宿主
CLI 已配置的可用模型后重新执行；不要把模型 API Key 传给 Skill 脚本。

### 未生成最终报告

确认 CLI 没有停在 `run` 之后，并继续完成 `host-agent-instructions.md` 中的工具
循环。未通过 finalizer 时，Skill 会拒绝把草稿当作最终报告。

### 外部 Skill 提示方法上下文超限

先在外部 Skill 的 metadata 中只映射真正用于故障树/日志分析的 Markdown，或使用
显式 role 路径。不要仅为了绕过检查而调大上限；确认当前 CLI 所选模型的上下文
窗口后，再使用 `--max-host-method-tokens`。

### run scope 提示后端不支持案例绑定

停止仍在运行的旧版后端，让当前 Skill 启动 bundled runtime；若使用自定义
`--state-dir`，确保客户端和后端使用同一个状态目录。run scope 会拒绝连接不支持
v0.6 案例绑定或 method control root 不匹配的后端。

## 10. 能力边界

该 MVP 保留日志安全接入、GW/AP 来源、解析、内置方法与外部诊断 Skill 知识组合、
方法必查、故障树覆盖、限界检索、
证据校验和报告生成能力。它不包含主平台的 Vue 管理界面、RBAC、多租户、代码和
Commit 图谱、完整知识治理、全局轨迹回放、PostgreSQL/Qdrant 或 Docker 部署。

更细的安装路径、状态目录和凭据隔离说明见
[`references/installation.md`](references/installation.md)，Claude Code 的
交互约定见 [`references/claude-code.md`](references/claude-code.md)，执行约束见
[`SKILL.md`](SKILL.md)。
