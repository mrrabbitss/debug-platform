# GW/AP Debug Skill-only MVP 部署指南

本文适用于 `gw-ap-debug` v0.3.6。该分支是独立的 Skill-only MVP，
不是 Debug Platform `main` 分支的后续版本。它不需要 Vue 前端、Docker、
PostgreSQL 或 Qdrant；诊断推理默认使用当前 OpenCode、Claude Code 或 Codex
CLI 会话已经选择的模型。

## 1. 运行要求

- Windows 11、Linux 或 macOS；
- Python 3.11、3.12、3.13 或 3.14；
- OpenCode、Claude Code 或 Codex 中至少安装一个；
- 首次 `bootstrap` 时可访问 Python 依赖源；
- 至少一份 GW/AP 日志、无后缀 `collectDebuginfo`、日志目录或安全归档。

Skill 不需要也不会索取当前 CLI 模型的 API Key。模型提供商、模型名称和
凭据继续由宿主 CLI 自己管理。

## 2. 新电脑推荐安装

推荐只保留一份 Git 检出，再用仓库自带脚本把它注册到三个 CLI 的用户级 Skill
目录。以后更新、校验和回滚都针对这一份副本，不会出现三个复制版本不一致。

### 2.1 Windows 11（PowerShell）

```powershell
$skillDir = Join-Path $env:LOCALAPPDATA "gw-ap-debug-skill"
git clone --branch skillonly --single-branch https://github.com/mrrabbitss/debug-platform.git $skillDir
powershell -NoProfile -ExecutionPolicy Bypass -File "$skillDir\scripts\setup_user_skill.ps1" -Clients All -RunBootstrap -RunValidation
```

脚本会注册以下三个目录，并全部指向 `$skillDir`：

- Codex：`~/.agents/skills/gw-ap-debug`；
- Claude Code：`~/.claude/skills/gw-ap-debug`；
- OpenCode：`~/.config/opencode/skills/gw-ap-debug`。

它不会覆盖已存在的普通目录，也不会改写指向其他位置的链接。先预览而不修改时，
加上 `-PlanOnly`。只安装某一个 CLI 时使用 `-Clients Codex`、
`-Clients Claude` 或 `-Clients OpenCode`。

### 2.2 Linux / macOS

```bash
skill_dir="${XDG_DATA_HOME:-$HOME/.local/share}/gw-ap-debug-skill"
git clone --branch skillonly --single-branch https://github.com/mrrabbitss/debug-platform.git "$skill_dir"
bash "$skill_dir/scripts/setup_user_skill.sh" --clients all --bootstrap --validate
```

先预览时加 `--plan`；只注册部分 CLI 时，例如使用
`--clients codex,claude`。

## 3. 手动从 `skillonly` 分支安装

仓库地址：`https://github.com/mrrabbitss/debug-platform.git`

### 3.1 Codex 与 OpenCode 共用安装

两者都能发现 `.agents/skills`。进入需要使用该 Skill 的项目根目录后执行：

```powershell
git clone --branch skillonly --single-branch https://github.com/mrrabbitss/debug-platform.git .agents/skills/gw-ap-debug
```

如果 OpenCode 设置了 `OPENCODE_DISABLE_EXTERNAL_SKILLS=1`，改为安装到其
专用目录：

```powershell
git clone --branch skillonly --single-branch https://github.com/mrrabbitss/debug-platform.git .opencode/skills/gw-ap-debug
```

### 3.2 Claude Code 安装

进入项目根目录后执行：

```powershell
git clone --branch skillonly --single-branch https://github.com/mrrabbitss/debug-platform.git .claude/skills/gw-ap-debug
```

### 3.3 用户级安装

需要让多个项目共用时，可以安装到用户目录：

| CLI | 用户级目录 |
| --- | --- |
| Codex | `~/.agents/skills/gw-ap-debug` |
| Claude Code | `~/.claude/skills/gw-ap-debug` |
| OpenCode | `~/.config/opencode/skills/gw-ap-debug` |

例如在 PowerShell 中为 Codex 安装：

```powershell
git clone --branch skillonly --single-branch https://github.com/mrrabbitss/debug-platform.git "$HOME/.agents/skills/gw-ap-debug"
```

目标目录已存在时不要覆盖或嵌套克隆；应进入已有目录执行本文的更新流程。

## 4. 初始化运行环境

将 `<SKILL_DIR>` 替换为包含 `SKILL.md` 的绝对目录。先检查软件包和 Python：

```powershell
python "<SKILL_DIR>/scripts/debug_platform_skill.py" doctor --check package
```

首次安装或锁文件发生变化时初始化独立后端环境：

```powershell
python "<SKILL_DIR>/scripts/debug_platform_skill.py" bootstrap
python "<SKILL_DIR>/scripts/debug_platform_skill.py" doctor --check host-agent
```

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

## 5. 使用当前 CLI 内置模型诊断

在 CLI 中明确要求使用 `gw-ap-debug` Skill，并授权当前模型读取内置诊断方法和
经过本地限界、脱敏后的证据。原始日志不会发送给宿主模型。

也可以由 CLI 按 Skill 指令执行以下准备命令：

```powershell
python "<SKILL_DIR>/scripts/debug_platform_skill.py" run --mode host-agent --approve-host-model-egress --title "问题标题" --gw-log "D:\logs\gw" --ap-log "D:\logs\ap"
```

至少提供 `--gw-log`、`--ap-log` 或 `--log` 之一。输入可以是单个日志、
无后缀 `collectDebuginfo`、目录或受支持的归档。

`run` 只完成本地准备，不是最终诊断。CLI 必须继续读取输出目录中的
`host-agent-instructions.md`，完成只读工具循环，并且仅在以下两个条件同时成立
后展示结果：

- `host-validation.json` 中 `accepted` 为 `true`；
- `manifest.json` 中 `host_agent.status` 为 `VALIDATED`。

最终报告为输出目录中的 `host-diagnosis.md`。

## 6. 无模型的确定性模式

只验证上传、解析、规则诊断和报告链路时，可以完全不调用模型：

```powershell
python "<SKILL_DIR>/scripts/debug_platform_skill.py" run --mode deterministic --title "本地冒烟" --log "D:\logs\collectDebuginfo.txt"
```

确定性模式适合安装验收，但不等同于宿主模型完成的故障树综合诊断。

## 7. 安装后验收

在 `<SKILL_DIR>` 中执行：

```powershell
python -B -m unittest discover -s tests -v
python -B scripts/check_provenance.py
python -B scripts/validate_release.py
python scripts/debug_platform_skill.py doctor --check host-agent
```

预期结果：

- 44 个单元测试全部通过；
- provenance 输出 `"ok": true`；
- release validation 输出 `"ok": true`；
- host-agent doctor 没有缺失依赖或版本错误。

随后使用一份允许测试的日志完成一次真实宿主 CLI 诊断，并确认最终验证状态。

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
python "<SKILL_DIR>/scripts/debug_platform_skill.py" bootstrap
python "<SKILL_DIR>/scripts/debug_platform_skill.py" doctor --check host-agent
```

运行状态位于 Skill 外部，正常更新不会删除已有案例。更新前仍建议备份自定义状态
目录。需要回滚时，先查看分支提交记录，再检出明确的已知提交；不要删除状态目录
来代替代码回滚。

## 9. 常见问题

### CLI 没有发现 Skill

- 确认路径末端是 `gw-ap-debug/SKILL.md`，而不是多嵌套了一层仓库目录；
- 重启 CLI 或重新打开项目；
- OpenCode 禁用外部 Skill 时使用 `.opencode/skills`；
- Claude Code 使用 `.claude/skills`，Codex 使用 `.agents/skills`。

### `doctor` 报 Python 版本不支持

安装 Python 3.11 至 3.14，并确保执行命令的 `python` 指向该版本。

### 更新后提示环境指纹不匹配

重新执行 `bootstrap`。该检查用于防止旧虚拟环境与新锁文件混用。

### OpenCode 返回 429 或余额不足

这是宿主模型提供商的账户或资源包问题，不是 Skill 安装失败。充值或切换宿主
CLI 已配置的可用模型后重新执行；不要把模型 API Key 传给 Skill 脚本。

### 未生成最终报告

确认 CLI 没有停在 `run` 之后，并继续完成 `host-agent-instructions.md` 中的工具
循环。未通过 finalizer 时，Skill 会拒绝把草稿当作最终报告。

## 10. 能力边界

该 MVP 保留日志安全接入、GW/AP 来源、解析、方法必查、故障树覆盖、限界检索、
证据校验和报告生成能力。它不包含主平台的 Vue 管理界面、RBAC、多租户、代码和
Commit 图谱、完整知识治理、全局轨迹回放、PostgreSQL/Qdrant 或 Docker 部署。

更细的安装路径、状态目录和凭据隔离说明见
[`references/installation.md`](references/installation.md)，执行约束见
[`SKILL.md`](SKILL.md)。
