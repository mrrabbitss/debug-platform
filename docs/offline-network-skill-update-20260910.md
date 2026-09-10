# 组网 Skill 独立更新工具

适用：已安装 0.5.1–0.5.5 的 Windows 服务器，尤其是旧库显示“已保留现有知识和 Skill”的情况。
本工具直接更新现有数据库里的组网包，无需卸载或再次覆盖安装服务器。

## 在公司服务器上使用

1. 正常停止服务器，等待原服务器窗口结束。
2. 将更新 ZIP 完整解压到一个新文件夹，用原来安装服务器的 Windows 账号双击 `Update-Network-Skill.bat`。
3. 等待显示“100% 六份组网 Skill 与索引已一起生效”，启动原服务器，刷新网页，在组网类别查看 Skill。

不需要管理员网页密钥、不需要联网下载依赖，不更改用户账号或密钥。
双击运行即执行这份明确范围的本机维护操作，并以系统维护来源留存审批清单、差异、备份和发布记录。

## 更新范围

随附原始 hilink-diag.zip：一个 SKILL.md 和五个 references 文件，共六份：
diagnostic-methodology.md、fault-tree.md、log-analysis.md、hilink-architecture.md、report-format.md。
只更新完整来源路径匹配、归类为组网 Skill 的现有文件；缺少文件会补齐，旧内容会保留历史版本。
其他目录的同名文件、其他类别、普通知识、草稿、案例、报告、自定义报告模板均保留。
若原组网报告模板指向本次更新的旧包，则自动关联新包模板；独立自定义模板保留。
总领 Skill 增加平台模型选择与人工审批适配说明，原文与差异保存在更新备份内。

完整构建含其他现有知识的向量和图索引后，一次提交六文件、索引指针、模板及完成记录。
中途失败不会发布半个包。意外退出后请保持服务器停止，重新运行同一工具；会沿用原审批重试。
如果中断后已对知识或模型配置作了其他修改，工具会拒绝过时审批，需核对日志，不会覆盖这些修改。
重复运行且六份内容已完整生效时直接提示无需更新，不重复建库。

## 文件和配置

备份：原业务数据目录下 `knowledge-update-backups`，每次更新独立子目录，包含 SQLite 一致性备份和原始 ZIP。
操作日志：解压目录下 `knowledge-update-日期时间.log`。
程序目录仅供读取现有 Python、应用依赖与模型，工具不重命名或替换它。

默认定位当前 Windows 账号下的服务器。自定义目录可在命令提示符中设置：

```bat
set "GWAP_SERVER_PACKAGE_ROOT=D:\YourServer\app"
set "GWAP_SERVER_DATA_ROOT=D:\YourServerData"
Update-Network-Skill.bat
```

默认仅使用现有内置 Embedding（或已有离线内建索引配置）。不调用诊断 Chat、CLI 或 Reranker。
若系统选用了外部 Embedding API，需明确同意将本包与现有可索引知识发往该已配置服务后运行：

```bat
Update-Network-Skill.bat --allow-configured-embedding-api
```

工具不会改变全局模型选择，也不会回退为另一个模型。进度百分比是阶段估算，模型较慢时允许等待。
目前公司服务器上的实际运行结果尚未验证；本工具绕开安装程序目录切换，不能代表整包安装故障已修复。
