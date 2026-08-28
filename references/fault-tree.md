

## 7. 故障定位故障树

### 7.1 AP频繁离线问题定位流程

```
用户反馈: AP频繁离线
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│ 步骤1: 查看拓扑日志 /var/smartlink/topodebug                │
│ 关键字: Topo, apInst=[X] Status=[0/1], DelAPTopoTree        │
│ 判断: 确认是哪个AP离线，AP1还是AP2                           │
└─────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│ 步骤2: 根据Parent判断连接关系                                │
│ - Parent: 33 → 直接连接主网关                                │
│ - Parent: 1   → 通过AP1中继连接                              │
│ 注意: AP1离线会导致挂在它下面的AP2也跟着离线                 │
└─────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│ 步骤3: 判断物理链路问题还是协议层问题                        │
│                                                         │
│ 3.1 查看 display ip neigh 确认AP是否拿到IP                 │
│     - 能拿到IP(REACHABLE) → 物理链路正常                    │
│     - 拿不到IP → 物理链路问题(网线/光纤)                    │
│                                                         │
│ 3.2 查看 TestLinkOK failed                                 │
│     - 出现此日志 → 链路检测失败                             │
│     - 需进一步排查是物理还是协议问题                        │
└─────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│ 步骤4: 判断是UDM协议栈问题还是AP自身问题                    │
│                                                         │
│ 4.1 查看网关日志: src=CtrlPointVerify                      │
│     - ApInst:X Recv Offline Event, src=CtrlPointVerify     │
│     - 表示UDM控制点检测到AP离线                             │
│                                                         │
│ 4.2 查看心跳超时日志 (网关侧 display debuglog info)         │
│     - [Abnormal] curTime[XXX], iAdvrTimeOut[250]           │
│     - lastEventTime[YYY], UDN[uuid:...]                    │
│     - curTime - lastEventTime > iAdvrTimeOut → 超时判定    │
│                                                         │
│ 4.3 UDN最后一串即AP的MAC地址                                │
│     - UDN[uuid:00e0fc37-2525-2828-2500-2ca79e1ebd50]      │
│     - 2ca79e1ebd50 = AP的MAC (2C:A7:9E:1E:BD:50)           │
└─────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│ 步骤5: 定位AP侧根因 (AP侧 display debuglog info)            │
│                                                         │
│ 5.1 查看 udm 进程异常日志                                  │
│     - [Critical] udm Is Abnormal!Reset Proc! [Proc Not Exist] │
│     - AP侧UDM进程异常导致无法发送心跳                       │
│                                                         │
│ 5.2 查看 listen port check failed                          │
│     - [Debug] dynamic:[udm] listen port check failed       │
│     - [Debug] dynamic:[udm] listen port not exist          │
│     - UDM监听端口(1900/37443)不可用                        │
│                                                         │
│ 5.3 判断是SmartLink 1.0还是2.0                             │
│     - 查看 libmap 中是否加载 libudm_rpc_adapt.so           │
│     - 加载了 = 2.0协议; 没加载 = 1.0协议                    │
└─────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│ 根因分析结论                                                │
│                                                         │
│ 场景1: 物理链路问题                                        │
│   - 表现: AP拿不到IP                                       │
│   - 原因: 网线松动、光纤断裂、端口协商失败                  │
│                                                         │
│ 场景2: UDM协议栈问题 (AP侧UDM进程异常)                     │
│   - 表现: UDM进程崩溃/重启、listen端口不可用               │
│   - 原因: 内存泄漏、进程异常、协议栈故障                   │
│   - 后果: 无法发送Advertise心跳 → 网关超时判定离线          │
│                                                         │
│ 场景3: 网络传输问题                                        │
│   - 表现: 链路检测失败、UDP报文丢失                        │
│   - 原因: 网络拥塞、防火墙拦截、SSDP多播被隔离             │
└─────────────────────────────────────────────────────────────┘
```

### 7.2 关键判断节点

| 判断点 | 日志关键字 | 判断结论 |
|--------|-----------|----------|
| AP是否离线 | `Status=[0]` | AP已离线 |
| 物理链路是否通 | `display ip neigh` REACHABLE | 链路正常 |
| 链路检测 | `TestLinkOK failed` | 链路检测失败 |
| UDM检测离线 | `src=CtrlPointVerify` | UDM控制点判定离线 |
| 心跳超时 | `curTime - lastEventTime > iAdvrTimeOut` | 超时离线 |
| UDM进程异常 | `udm Is Abnormal!Reset Proc!` | AP侧UDM崩溃 |
| 监听端口异常 | `listen port check failed` | 端口不可用 |
| 协议版本 | `libudm_rpc_adapt.so` 存在性 | 2.0(有)/1.0(无) |

### 7.3 SmartLink协议版本识别

| 判断方法 | 1.0协议 | 2.0协议 |
|----------|---------|---------|
| SO库 | `libhw_smp_udm_api.so` | `libudm_rpc_adapt.so` |
| 端口 | 1900(SSDP) + 37443(HTTPS) | 差异待确认 |
| 心跳 | SSDP Advertise广播 | 待确认 |

### 7.4 AP离线问题排查清单

**必查日志文件：**
1. `/var/smartlink/topodebug` - 拓扑日志
2. `display ip neigh` - ARP表
3. `display debuglog info` - TRACE日志(网关)
4. `display debuglog info` - TRACE日志(AP)
5. `wlancmd show log` - WiFi业务日志

**必查关键词：**
- [ ] `Topo, apInst=[X] Status=[0]`
- [ ] `DelAPTopoTree`
- [ ] `TestLinkOK failed`
- [ ] `src=CtrlPointVerify`
- [ ] `[Abnormal]`
- [ ] `udm Is Abnormal!Reset Proc!`
- [ ] `listen port check failed`

---

## 8. 附录: 源文件索引与协议规格

### 主网关侧

| 路径 | 功能 |
|------|------|
| `service/main/hilink/cover/ap/` | 主网关WiFi进程 - AP管理 |
| `xlink/protocol/hilink/udm/ctrlpt/` | 主网关UDM协议栈 - 控制点 |
| `xlink/protocol/hilink/udm/slave_ap/` | 从AP UDM协议栈 |
| `service/main/hilink/slave_cover/` | 从AP WiFi进程 - 配置执行 |

### 关键端口定义

| 端口 | 协议 | 用途 |
|------|------|------|
| **1900** | UDP | SSDP多播监听端口，用于设备发现和Advertise心跳 |
| **37443** | TCP | HTTPS控制端口，用于主从之间SOAP Action通信 |

### 从AP侧

| 路径 | 功能 |
|------|------|
| `xlink/protocol/hilink/udm/slave_ap/udm_ap.c` | UDM AP主入口 |
| `xlink/protocol/hilink/udm/slave_ap/udm_dispatch.c` | Action派发 |
| `xlink/protocol/hilink/udm/slave_ap/udm_ap_action.c` | Action处理 |
| `service/main/hilink/slave_cover/hilink_wlan_handle.c` | 事务处理 |
| `service/main/hilink/slave_cover/hilink_wlan_set.c` | WLAN配置设置 |
| `service/main/hilink/slave_cover/hilink_slave.c` | 从AP初始化 |
