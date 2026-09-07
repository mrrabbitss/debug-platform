# GW/AP Win11 局域网服务器：先导运行说明

状态：安装脚本和 HTTPS 通信已分别验证，**Windows 服务实装、异机恢复、最终 GGUF 组合尚未全部验收**。
不要把本说明当作 M0–M5 全部完成的发布通知。

## 部署结构

一台 Win11 x64 服务器托管网页、API/MCP、数据库、文件和可选本地 GGUF E/R。
客户端通过 HTTPS 访问；CodeAgent 使用客户端自己的推理模型，网页使用后台 Chat Profile。
先导默认：2 个重任务、E/R 各 1 个并发、模型各 8 线程、队列 32、至少保留 10 GiB 空闲。
目标最低 i7-14700 / 32 GB，数据库和工作数据必须在本地 SSD，不要放共享盘或同步盘。

## 首次安装

1. 将完整、校验通过的服务器包解压到固定位置；服务会直接引用此目录，安装后不能移动或删除。
   目录须允许 Windows `LocalService` 读取。当前脚本尚不自动复制到 Program Files。
2. 确定固定 IP 或公司 DNS 名和 HTTPS 端口。推荐公司证书；否则使用平台自签 CA。
3. 管理员身份打开 PowerShell，在包目录执行（替换示例地址）：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install_server.ps1 -PublicUrl https://debug.example.test -OpenFirewall
```

公司证书增加 `-Certificate "证书链.pem完整路径" -CertificateKey "私钥.pem完整路径"`，
文件必须长期可读且仅授权服务账户和管理员。默认业务目录为 `C:\ProgramData\GWAPDebugServer`。
防火墙只为 Domain/Private 网络、本地子网开放指定 HTTPS 端口；不开放数据库和模型端口。
已有同名服务或非空数据目录会拒绝覆盖，**当前不要把首次安装脚本当升级器使用**。

4. 管理员从 `config\bootstrap-token.txt` 读取一天有效的初始令牌，在网页登录。
   建立个人账户及访问令牌后撤销初始令牌。令牌不要放入公司群、脚本或版本库。
5. 若使用自签 CA，导出公钥证书并给客户端核对指纹：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\server_maintenance.ps1 -Action ExportCertificate -Output "D:\Transfer\gwap-root.crt"
```

只发这个 `.crt`，不要发 `gateway` 整个目录。客户端按轻客户端说明导入。

## 日常操作

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\server_maintenance.ps1 -Action Status
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\server_maintenance.ps1 -Action Stop
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\server_maintenance.ps1 -Action Start
```

服务名 `GWAPBackend`、`GWAPGateway`；不依赖某个 CodeAgent 窗口保持开启。
服务配置含延迟自动启动和故障重启，但重启恢复仍需在目标 Win11 实机验收。

## 当前备份边界（重要）

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\server_maintenance.ps1 -Action Backup -Archive "D:\GWAPBackups\business-20260907.zip"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\server_maintenance.ps1 -Action Restore -Archive "D:\GWAPBackups\business-20260907.zip" -Confirm RESTORE
```

操作在服务停止后执行，使用独占维护锁。备份失败恢复原运行状态；恢复失败保持停止并保留旧数据。
ZIP 包含数据库、知识/案例附件、模型配置解密材料、完整 `config`、`gateway` 及公司证书材料。
程序和 GGUF 不重复打包；必须保留相同哈希的完整程序包。恢复会先验证所有文件哈希及匹配的程序版本，
成功后保留旧数据和配置的 rollback 目录。备份包含凭据，应限制访问。

首次安装默认注册 SYSTEM 身份每日 02:00 的 `GWAPServerBackup`，备份会短暂停止服务。
不自动删除备份；管理员应检查任务执行记录和磁盘余量。仅本机磁盘时使用默认路径即可；
将来配置异机目录前，确保 SYSTEM/计算机账户具有共享目录权限：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\server_maintenance.ps1 -Action ConfigureBackup -BackupDirectory "D:\GWAPBackups" -OffsiteDirectory "\\backup-host\gwap"
```

副本复制后核对 SHA256，再由临时文件改名；失败保留已验证的本机备份。
不传目录参数会保留已有策略；传 `-OffsiteDirectory ""` 可关闭异机复制。

## 升级与故障回退

新包解压到独立版本目录，保留旧包。在旧包目录、管理员 PowerShell 执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\upgrade_server.ps1 -NewPackageRoot "D:\GWAPServer\next" -DryRun
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\upgrade_server.ps1 -NewPackageRoot "D:\GWAPServer\next"
```

脚本校验新包，在停止服务后建立完整备份，切换服务/防火墙路径并检查 API ready。
失败使用旧包和升级前备份恢复；成功更新自动备份脚本路径并保留策略。
不要使用有损降级迁移；不要让旧程序直接读取新数据库。Windows 服务切换仍需目标实机验收。

## 验收顺序

1. 客户端浏览器打开 HTTPS，使用个人账户查看权限范围内案例。
2. 从轻客户端快捷方式启动 CodeAgent，检查平台标识、MCP 及旧有 MCP/代理仍可使用。
3. 使用合成 GW/AP 日志分别完成网页和 CLI 诊断，检查两端报告可见、证据引用可打开。
4. 建立知识草稿并审核发布，编辑草稿时旧版继续检索；模拟发布失败应保留旧版。
5. 实机测试服务重启、GGUF 进程退出、磁盘不足、撤销令牌、升级和备份恢复。

公司日志和未批准的模型端点不用于默认验收。若缺少受管 GGUF，不能声称已完成全 GGUF E/R 验收。
