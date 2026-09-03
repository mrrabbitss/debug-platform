# AP 频繁离线演示日志分析（公开合成）

> `[SYNTHETIC DEMO METHOD]` 本方法只适用于仓库内置的纯合成
> `ap-frequent-offline-glm52-v1` 演示数据。它不是私有方法文档的副本，也不得作为真实案例的
> 全局诊断知识自动启用。

## 使用边界

同时检查主 GW 与从 AP，并按时间排序建立因果链。日志中的方法说明只能指导检索，结论必须引用
当前案例工具返回的证据。单纯时间相关只能支持“较可能”，不能代替进程转储、抓包或物理层测量。
恢复日志是故障窗口的边界和反证，不得作为异常支持证据。

## GW 侧日志 Pattern

| 日志 Pattern | 含义 |
|---|---|
| `AddAPTopoTree, APInst: %u, Parent: %u` | 建立 AP 与上级设备的拓扑关系。 |
| `Topo, apInst=[X] Status=[0]` | GW 将目标 AP 标记为离线。 |
| `Topo, apInst=[X] Status=[1]` | GW 将目标 AP 标记为在线，可作为恢复边界。 |
| `LAN3 REACHABLE` | 邻居可达，是物理链路异常的反证之一。 |
| `link is up` | 以太链路协商正常，是物理链路异常的反证之一。 |
| `UDN[uuid:` | 提取 GW 观察到的设备身份；身份比较必须由服务器本地完成。 |
| `curTime[` | 与同一行的 lastEventTime、iAdvrTimeOut 联合计算超时，不得单独判故障。 |
| `lastEventTime[` | 与同一行的 curTime、iAdvrTimeOut 联合计算超时。 |
| `iAdvrTimeOut[` | 只有 curTime - lastEventTime 大于该阈值才确认心跳超时。 |
| `Recv Offline Event, src=CtrlPointVerify` | GW 控制点因验证失败接收离线事件。 |
| `DelAPTopoTree` | GW 删除目标 AP 拓扑节点。 |
| `COVER_PonApLeaveProc ApInst offline` | GW 执行 AP 离线处理。 |
| `Add new AP to list uuid:` | AP 重新被发现，可作为恢复边界。 |

## AP 侧日志 Pattern

| 日志 Pattern | 含义 |
|---|---|
| `deviceApHandle:%u alive` | AP UDM 心跳发送路径处于活动状态。 |
| `UpnpSendAdvertisement success` | Advertise 发送成功，只能作为恢复或正常窗口证据。 |
| `Error sending alive advertisements : -5` | AP Advertise 发送失败。 |
| `listen port check failed` | UDM 监听端口检查失败。 |
| `listen port not exist` | UDM 监听端口不存在。 |
| `udm Is Abnormal!Reset Proc!` | 监控逻辑检测 UDM 异常并触发复位。 |
| `process udm died unexpectedly with signal 11` | UDM 进程异常退出；深层原因仍需进程转储。 |
| `watchdog restarting service udm` | 看门狗在异常退出后重启 UDM。 |
| `listen ports 1900 and 37443 ready` | 重启后监听端口恢复，只能作为恢复边界。 |
| `libudm_rpc_adapt.so` | 当前日志观察到 SmartLink 2.0 适配库。 |

## 替代分支与补采 Pattern

| 日志 Pattern | 含义 |
|---|---|
| `TestLinkOK failed` | 仅说明链路检测失败，不能单独断言物理链路或网络传输根因。 |
| `carrier lost` | 明确的物理载波丢失证据。 |
| `no carrier` | 明确的物理载波缺失证据。 |
| `port negotiation failed` | 明确的物理端口协商失败证据。 |
| `udp packet loss` | 网络传输分支的直接证据之一。 |
| `SSDP multicast blocked` | SSDP 多播被阻断的直接证据之一。 |

## 联合判断顺序

1. 先确认三次离线是否指向同一 AP、同一 Parent 和同一 UDN/MAC 身份关系。
2. 对每个周期分别确定 AP 异常、Advertise 中断、GW 超时判离线、拓扑删除和恢复的时间顺序。
3. 用 `REACHABLE` 与 `link is up` 检查物理链路反证，同时搜索载波或协商失败的正向证据。
4. 将 UDM 进程、端口和 Advertise 证据与 GW 控制点判离线证据交叉验证。
5. 网络传输分支必须有 UDP 丢包、SSDP 多播阻断或抓包证据；没有时保持证据不足。

