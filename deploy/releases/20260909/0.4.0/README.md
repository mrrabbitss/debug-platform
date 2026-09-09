# 0.4.0 三栏工作台交付（2026-09-09）

本次已完成已确认的界面精简、问题类别、知识助手、角色权限、案例报告共享与四章报告改版。
服务器完整离线EXE与配套分机ZIP已生成，本地定向检查及隔离包内运行通过。

原始交付目录：`artifacts/lan/workbench-0.4.0/delivery`。
GitHub分发目录：`artifacts/lan/workbench-0.4.0/github-release`。
下载入口：[v0.4.0 Release](https://github.com/mrrabbitss/debug-platform/releases/tag/v0.4.0)。

| 文件 | 大小 | SHA-256 |
| --- | --- | --- |
| `GWAP-Debug-Server-Setup-0.4.0-x64.exe` | 920949842 字节 | `47b437e9ee743dd379ef0f780f759b77f05008853fa6bdb7b92aba809a143452` |
| `GWAP-Client-b1d91dda4f932e2f.zip` | 44858 字节 | `a569540f4ba4c1288cb063ac889d5f47473cad3b24c2204cf4d3965661971ab1` |

GitHub附件将同一分机ZIP命名为 `GWAP-Client-0.4.0.zip`，内容与上表哈希相同。
附件包括 `Server-Guide.md`、`Client-Guide.md`、`Release-Notes.md`、`SHA256.txt` 和 `delivery-manifest.json`。
独立服务器指南补充覆盖升级步骤；EXE、分机ZIP与原始验证产物保持字节一致。
服务器含Python、前端、GGUF Embedding/Reranker、HTTPS网关及迁移；分机仅含连接器与Skill。
内部服务器地址仅存在于忽略的专用产物内，源码和本说明不保存该地址。

0.3.3无需先卸载。使用原Windows账号，先停止并备份旧服务器，再覆盖安装0.4.0。
升级沿用原数据目录、账号、证书、端口和模型配置；首次启动迁移到0022。安装器不会自动停止运行中的服务器。
回退须同时恢复升级前业务备份及旧程序。详见[服务器指南](../../../../docs/服务器使用指南.md)与[分机指南](../../../../docs/分机使用指南.md)。

安装包构建时源码来自 `release` 分支基线 `46c3b1f47529ab17095d5e4e01ef8bb6ea59b671` 加本次工作台改动。
本版源码使用 `v0.4.0` 标签关联对应的 `release` 分支提交，不将标签指向旧0.3.3源码。
10,181项包清单校验通过；257个后端/前端文件与最终源码逐项一致；24项分机文件与ZIP、指南和Skill一致。
包内验证使用全新合成数据库及包内Python，覆盖迁移、三栏页面、默认授权、模拟诊断、三种报告、人工共享审核、暂停助手及重启保留。

验证清单见[VALIDATION.md](../../../../VALIDATION.md)，需求与接续见[实施记录](../../../../docs/workbench-iteration-20260908.md)。
本轮按用户要求分发已有本地验证产物，提交对应源码；未重复历史回归或Full、未运行External或手动CI。
真实公司Chat模型、公司网络/实机安装、Word实际分页与代码签名未在本轮验收；本地检查通过不等于正式发布/合并门禁全部完成。

## GitHub发布结果

`v0.4.0` 已于2026-09-09发布并标为Latest，对应源码提交 `bd9c9dab340d3c982971a72294d3bab913fc7745`。
服务器、分机、两份指南、版本说明、SHA256及交付清单共7个附件，GitHub返回的大小和SHA-256均与本地一致。
记录位于忽略的 `artifacts/lan/workbench-0.4.0/github-release/published-verification.json`。

发布事件自动触发的[完整GGUF重打包](https://github.com/mrrabbitss/debug-platform/actions/runs/34299969071)在Python环境准备阶段失败：
Windows 2022 runner找不到指定的Python 3.12.12 x64，应用构建/验证尚未执行。
本次附件来自已核验的本地构建包，没有手动重跑CI，不宣称新CI通过。
