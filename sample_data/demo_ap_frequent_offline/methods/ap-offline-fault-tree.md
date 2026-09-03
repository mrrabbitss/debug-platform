# AP 频繁离线演示故障树（公开合成）

> `[SYNTHETIC DEMO METHOD]` 本故障树只适用于仓库内置的纯合成
> `ap-frequent-offline-glm52-v1` 演示数据。它依据公开合成日志和演示验收规则重新编写，不包含
> 根目录私有 `故障树.md` 的正文。

## 联合诊断流程

```text
步骤1: 确认 AP 是否真实、重复离线；检查 `Status=[0]`、`DelAPTopoTree` 与后续 `Status=[1]`。
步骤2: 确认目标 AP 和 Parent 判断；关联 `AddAPTopoTree`、APInst、Parent 与 UDN 身份。
步骤3: 区分物理链路问题还是协议层问题；同时检查正向故障证据和 `REACHABLE`、`link is up` 反证。
3.1: 检查物理链路是否通；搜索 `carrier lost`、`no carrier`、`port negotiation failed` 及可达反证。
3.2: 检查链路检测结果；`TestLinkOK failed` 只能说明检测失败，不能单独确定根因。
步骤4: 区分 GW 的 UDM 协议栈判断还是 AP 自身问题；关联控制点离线、心跳阈值与 AP 异常窗口。
4.1: 检查 UDM 检测离线；搜索 `src=CtrlPointVerify` 和 GW 离线处理链路。
4.2: 检查心跳超时；仅当 `curTime - lastEventTime > iAdvrTimeOut` 才确认。
4.3: 检查 UDN 最后十二位与 AP MAC 是否一致；原值比较只能由服务器本地确定性执行。
步骤5: 定位 AP 侧根因；检查 UDM 进程、监听端口、Advertise 和恢复边界。
5.1: 检查 UDM 进程异常；搜索复位、signal 11 和 watchdog 重启，深层原因需进程转储。
5.2: 检查监听端口异常；区分端口失败与重启后的 1900/37443 ready。
5.3: 检查协议版本；用已加载 SO 库识别协议栈，不由端口或心跳现象臆测版本。
根因分析结论
场景1: 物理链路问题；需要载波丢失、无载波或端口协商失败等直接证据，正常可达可用于排除。
场景2: UDM 协议栈问题；需要 AP 侧进程或端口异常、Advertise 中断以及 GW 侧超时离线链路共同支持。
场景3: 网络传输问题；需要 UDP 丢包、SSDP 多播阻断或抓包证据，没有直接证据时保持证据不足。
```

## 判断表

| 判断点 | 证据与判断方法 |
|---|---|
| AP是否离线 | 用 `Status=[0]`、`DelAPTopoTree`、`Recv Offline Event` 确认离线，并用 `Status=[1]` 划定恢复边界。 |
| 物理链路是否通 | 同时检查 `LAN3 REACHABLE`、`link is up` 与 `carrier lost`、`no carrier`。 |
| 链路检测 | 检查 `TestLinkOK failed`；该信号不可同时证明物理链路和网络传输两个根因。 |
| UDM检测离线 | 检查 `src=CtrlPointVerify`、`COVER_PonApLeaveProc` 以及拓扑删除。 |
| 心跳超时 | 仅接受同一行满足 `curTime - lastEventTime > iAdvrTimeOut` 的算术结果。 |
| UDM进程异常 | 检查 `udm Is Abnormal!Reset Proc!`、`process udm died` 和 `watchdog restarting service udm`。 |
| 监听端口异常 | 检查 `listen port check failed`、`listen port not exist`，并区分 `listen ports 1900 and 37443 ready`。 |
| 协议版本 | 检查进程 maps 中实际加载的 `libudm_rpc_adapt.so` 或 `libhw_smp_udm_api.so`。 |
| SO库 | 用 `libudm_rpc_adapt.so` 支持 SmartLink 2.0 观察，用 `libhw_smp_udm_api.so` 支持另一协议栈观察。 |
| 端口 | 协议代际对应的端口差异待确认；1900/37443 的失败或恢复只能说明本次运行状态。 |
| 心跳 | 协议代际对应的心跳机制差异待确认；发送成功、发送失败和 GW 超时需分别判断。 |

## 结论约束

- 三个根因场景必须分别得到支持、排除或证据不足的终态，不能因其他场景已有结论而省略。
- `SUPPORTED` 和 `EXCLUDED` 必须引用当前案例日志证据；方法文档本身不能充当案例事实。
- 缺少进程转储时不得确认 signal 11 的底层崩溃原因；缺少抓包时不得确认或彻底排除网络传输问题。

