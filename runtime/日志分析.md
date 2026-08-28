---

## 6. 核心日志分析

### 6.1 日志分类总览

Hilink日志按**采集方式**分为以下几类，适用于不同场景：

| 日志类型 | 采集方式 | 适用场景 | 宏函数 |
|----------|----------|----------|--------|
| **文件日志** | `wlancmd show log` | 无法远程，只能采集日志文件 | `HW_WLAN_SaveLog` |
| **调试日志** | `cat /proc/wifilog/debug/All` | WiFi模块调试，汇总到文件 | `HILINK_DEBUG_LOG` |
| **TRACE日志** | `mid set mid {MID} flag 1` | 可远程或现场复现时开启 | `HW_WAP_TRACE` |
| **静态日志** | `WAP:display debuglog info` | 系统静态状态日志(心跳超时等) | `HW_WAP_DEBUG_LOG` |
| **Action日志** | `udm show log` | UDM协议Action交互详情 | `HW_UDM_RecordDebugLog` |

**关键MID定义** (来源: `pdt_mid.h`):

| MID | 宏定义 | 模块 |
|-----|--------|------|
| 33 | `HW_MID_SSMP_UDM` | UDM协议栈日志(主网关侧) |
| 364 | `HW_MID_UDM_LOG` | UDM日志 |
| 411 | `HW_MID_SSMP_UDM_INNER` | UDM内部日志 |
| 504 | `HW_MID_UDM_UPNP` | UDM_UPNP模块 |
| 544 | `HW_MID_WIFI_AP1` | WiFi API消息处理 |
| **545** | **`HW_MID_WIFI_AP2`** | **WiFi AP上下线流程** |
| 546 | `HW_MID_WIFI_AP3` | WiFi API消息处理 |
| 649 | `WLAN_MID_HILINK` | 从AP Hilink模块(Commit/Action) |
| 1035 | `MID_HILINK_EVENT_NOTIFY` | HILINK事件通知消息处理 |

---

### 6.2 按流程和模块分类日志

#### 6.2.1 拓扑和AP上下线日志 (wlancmd show log)

**主网关侧 - 拓扑日志** (`/var/smartlink/topodebug`)

| 日志关键字 | 含义 |
|-----------|------|
| `Topo, apInst=[X] Status=[1]` | AP X 在线 |
| `Topo, apInst=[X] Status=[0]` | AP X 离线 |
| `AddAPTopoTree, APInst: X, Parent: Y` | AP X 上线,父节点为Y |
| `DelAPTopoTree DelType: 0, APInstId: X, Parent APInstId: Y` | AP X 离线,父节点为Y |
| `RefreshTopoTree APInstId: X TestLinkOK failed` | AP X 链路检测失败 |
| `Broadcast NeighborList apInst:[X] offline` | 广播邻居AP X离线 |

**关键判断:**
- `Parent: 33` = 直接连接主网关
- `Parent: 1` = 通过AP1中继连接
- `Status=[0]` = AP已离线

#### 6.2.2 UDM心跳超时日志 (display debuglog info)

**主网关侧 - 心跳超时检测**

| 日志关键字 | 含义 |
|-----------|------|
| `ApInst:X Recv Offline Event, src=CtrlPointVerify` | UDM控制点检测到AP离线 |
| `[Abnormal] curTime[XXX], iAdvrTimeOut[250], lastEventTime[YYY]` | 心跳超时判定 |
| `UDN[uuid:00e0fc37-2525-2828-2500-2ca79e1ebd50]` | UDN最后一段=AP的MAC |

**超时计算:** `curTime - lastEventTime > iAdvrTimeOut(250)` → 超时

**AP侧 - UDM进程异常**

| 日志关键字 | 含义 |
|-----------|------|
| `[Debug] dynamic:[udm] listen port check failed` | UDM监听端口检查失败 |
| `[Debug] dynamic:[udm] listen port not exist` | UDM监听端口不存在 |
| `[Critical] udm Is Abnormal!Reset Proc! [Proc Not Exist]` | UDM进程异常重启 |

#### 6.2.3 物理链路日志 (display ip neigh)

| 日志关键字 | 含义 |
|-----------|------|
| `LAN3 REACHABLE 2c:a7:9e:1e:bd:50 192.168.0.8` | AP MAC正常,IP可达 |
| `REACHABLE` | 链路正常 |
| `FAILED` | 链路故障 |

#### 6.2.4 SmartLink协议版本识别

| 日志位置 | 1.0协议特征 | 2.0协议特征 |
|----------|------------|-------------|
| `libmap`段 | 只有`libhw_smp_udm_api.so` | 包含`libudm_rpc_adapt.so` |

---

#### 流程1: AP发现 (Discovery)

**主网关侧 - UDM控制点**

| 日志关键字 | 宏 | 采集方式 | 文件位置 | 含义 |
|------------|-----|----------|----------|------|
| `Obtaining device description from %s error = %d` | `HW_WAP_TRACE(HW_MID_SSMP_UDM)` | TRACE | `hw_udm_ctrlpt.c:1152` | 下载设备描述XML失败 |
| **`Add new AP to list %s`** | `HW_WAP_TRACE(HW_MID_SSMP_UDM)` | TRACE | `hw_udm_ctrlpt.c:1156` | **发现新AP并添加入链表(入口日志)** |
| **`Update Device info: %s`** | `HW_WAP_TRACE(HW_MID_SSMP_UDM)` | TRACE | `hw_udm_ctrlpt.c:1163` | **设备信息更新** |
| `Error in Discovery Callback -- %d` | `HW_WAP_TRACE(HW_MID_SSMP_UDM)` | TRACE | `hw_udm_ctrlpt.c:1192` | 发现回调错误 |
| **`Curr system busy, not process MSearch`** | `HW_WAP_TRACE(HW_MID_SSMP_UDM)` | TRACE | `hw_udm_ctrlpt.c:1201` | **系统忙(30秒内),丢弃MSearch** |

**主网关侧 - WiFi进程**

| 日志关键字 | 宏 | 采集方式 | 文件位置 | 含义 |
|------------|-----|----------|----------|------|
| **`COVER_ApOnlineDbProc: uuid=%s`** | `HW_WAP_TRACE(HW_MID_SSMP_UDM)` | TRACE | `cover_ap_online.c` | 收到UDM通知,准备处理AP上线 |
| **`ApOnline UUID:%s src=%s`** | `HW_WAP_TRACE(HW_MID_WIFI_AP2)` | TRACE | `cover_ap_online.c:2603` | **AP上线事件入口** |
| `ApInst:%u Recv Offline Event, src=%s` | `HW_WLAN_SaveLog` | 文件日志 | `cover_ap_offline.c:427` | 收到离线事件 |

---

#### 流程2: AP上线 (Online)

**主网关侧 - WiFi进程**

| 日志关键字 | 宏 | 采集方式 | 文件位置 | 含义 |
|------------|-----|----------|----------|------|
| **`COVER_ApOnlineProc apInst[%u], uuid[%s]`** | `HW_WAP_TRACE(HW_MID_WIFI_AP2)` | TRACE | `cover_ap_online.c:2363` | **AP上线处理开始** |
| `Ap[%u] SCN version not support skip` | `HW_WLAN_SaveLog` | 文件日志 | `cover_ap_online.c:2377` | SCN版本不匹配,跳过 |
| **`Fttr control, ap instid:%u`** | `HW_WAP_TRACE(HW_MID_WIFI_AP2)` | TRACE | `cover_ap_online.c:2382` | **AP被FTTR管控,忽略上线事件** |
| **`Refresh ApInst:[%u], OnlineType:%u, UpSsidChangeFlag:%u Success`** | `HW_WAP_TRACE(HW_MID_WIFI_AP2)` | TRACE | `cover_ap_online.c:2112` | **AP信息刷新成功** |
| `apInst:%u RefreshTopoTree failed` | `HW_WLAN_SaveLog` | 文件日志 | `cover_ap_online.c:944` | 刷新拓扑树失败 |

---

#### 流程3: 配置同步 (Config Sync)

**主网关侧 - WiFi进程配置事务**

| 日志关键字 | 宏 | 采集方式 | 文件位置 | 含义 |
|------------|-----|----------|----------|------|
| **`[XLINK] ApId: %u do start fail:Ret:%u`** | `HW_WLAN_SaveLog` | **文件日志** | `wifi_cover_vap_sync.c:431` | **Start事务失败(重要!)** |
| **`[XLINK] ApId: %u do CfgAp fail:Ret:%u`** | `HW_WLAN_SaveLog` | **文件日志** | `wifi_cover_vap_sync.c:443` | **CfgAp配置失败(重要!)** |
| `HW_WIFI_COVER_CfgApTransaction: apInst=%u, onlineFlag=%u` | `HW_WAP_TRACE(HW_MID_SSMP_UDM)` | TRACE | - | 配置事务触发 |
| `noSyncTimesRecords[apInstId]++` | - | - | `wifi_cover_vap_sync.c` | 记录配置失败次数 |

**从AP侧 - WiFi进程**

| 日志关键字 | 宏 | 采集方式 | 文件位置 | 含义 |
|------------|-----|----------|----------|------|
| **`[HILINK Commit start]`** | `HW_WAP_TRACE(WLAN_MID_HILINK)` | TRACE | `hilink_wlan_handle.c:1085` | **Commit开始** |
| **`[HILINK Commit end]`** | `HW_WAP_TRACE(WLAN_MID_HILINK)` | TRACE | `hilink_wlan_handle.c:1096` | **Commit结束** |

**从AP侧 - UDM协议栈**

| 日志关键字 | 宏 | 采集方式 | 文件位置 | 含义 |
|------------|-----|----------|----------|------|
| `UDM_RecvMSearchResultProc` 触发 | `HW_WAP_TRACE(HW_MID_SSMP_UDM)` | TRACE | `udm_ctrlpt.c` | 主网关发现AP |
| `UDM_AP_EventHandler` 接收Action | `HW_WAP_TRACE(HW_MID_SSMP_UDM)` | TRACE | `udm_ap.c` | 从AP接收主网关命令 |

---

#### 流程4: 心跳保活 (Heartbeat)

**从AP侧 - UDM协议栈**

| 日志关键字 | 宏 | 采集方式 | 文件位置 | 含义 |
|------------|-----|----------|----------|------|
| `deviceApHandle:%d alive` | `HW_OS_Printf` | 控制台 | `udm_ap.c` | 心跳发送(调试用) |
| **`Error sending alive advertisements : %d`** | `HW_WAP_TRACE(HW_MID_SSMP_UDM)` | TRACE | `udm_ap.c:664` | **心跳发送失败** |
| `UpnpSendAdvertisement success` | - | - | `udm_ap.c` | 心跳发送成功 |

**主网关侧 - 静态日志**

| 日志格式 | 宏 | 采集方式 | 含义 |
|----------|-----|----------|------|
| `[Abnormal] curTime[%u], iAdvrTimeOut[%d], lastEventTime[%u]` | `HW_WAP_DEBUG_LOG` | **静态日志** | **AP心跳超时检测(重要!)** |

**心跳超时判断**:
```
判断: (curTime - lastEventTime) > iAdvrTimeOut → AP判定为离线
示例: (114598 - 114277) = 321 > 250 → 超时，AP离线
```

---

#### 流程5: AP离线 (Offline)

**主网关侧 - UDM控制点**

| 日志关键字 | 宏 | 采集方式 | 文件位置 | 含义 |
|------------|-----|----------|----------|------|
| **`Received ByeBye for Device: %s`** | `HW_WAP_TRACE(HW_MID_SSMP_UDM)` | TRACE | `hw_udm_ctrlpt.c:1238` | **收到AP下线ByeBye报文** |
| `No need to solve byebye pkt:%s` | `HW_WAP_TRACE(HW_MID_SSMP_UDM)` | TRACE | `hw_udm_ctrlpt.c:1234` | 重复ByeBye,无需处理 |

**主网关侧 - WiFi进程**

| 日志关键字 | 宏 | 采集方式 | 文件位置 | 含义 |
|------------|-----|----------|----------|------|
| **`COVER_PonApLeaveProc ApInst offline:%u`** | `HW_WAP_TRACE(HW_MID_WIFI_AP2)` | TRACE | `cover_ap_offline.c:453` | **PON AP离线(TRACE)** |
| **`COVER_PonApLeaveProc ApInst offline:%u`** | `HILINK_DEBUG_LOG` | 调试日志 | `cover_ap_offline.c:454` | **PON AP离线(调试日志)** |
| **`COVER_PonApLeaveProc ApInst offline:%u`** | `HW_WLAN_SaveLog` | **文件日志** | `cover_ap_offline.c:455` | **PON AP离线(文件日志,重要!)** |
| **`COVER_WifiApLeaveProc ApInst offline:%u`** | `HILINK_DEBUG_LOG` | 调试日志 | `cover_ap_offline.c:484` | **WiFi AP离线** |
| **`DM Reboot Ap:%u Offline`** | `HILINK_DEBUG_LOG` | 调试日志 | `cover_ap_offline.c:508` | **DM重启AP离线** |
| **`DM Reboot Ap:%u Offline`** | `HW_WLAN_SaveLog` | **文件日志** | `cover_ap_offline.c:509` | **DM重启AP离线(文件日志)** |
| **`need to del ap:%u`** | `HW_WAP_TRACE(HW_MID_WIFI_AP2)` | TRACE | `cover_ap_online.c:2410` | **删除AP** |

**主网关侧 - SSMP事件**

| 日志格式 | 宏 | 采集方式 | 含义 |
|----------|-----|----------|------|
| `Send terminal offline to Ssmp, type:smartlink, Mac:%s, sn:%s` | `HW_WAP_DEBUG_LOG` | 静态日志 | AP离线事件上报 |
| `[Ssmp] delete terminal node, instId:%u, mac:%s` | `HW_WAP_DEBUG_LOG` | 静态日志 | 终端节点删除 |

---

### 6.3 Action交互日志详解

**采集命令**: 在主网关上执行 `udm show log`

**日志格式**:
```
W->U,time,uuid:xx,actionName:xx,para1:xx,para2:xx,para3:-
U->W,time,uuid:xx,actionName:xx,para1:xx,para2:xx,para3:-
```

| 字段 | 含义 |
|------|------|
| `W->U` | WiFi进程 → UDM协议栈（发送Action到AP） |
| `U->W` | UDM协议栈 → WiFi进程（AP响应） |
| `uuid` | AP设备UUID |
| `actionName` | Action名称（Start/SetWlanBaseConfiguration/Commit等） |

**常用Action**:

| Action名称 | 说明 |
|------------|------|
| `Start` | 开始配置事务 |
| `SetWlanBaseConfiguration` | 设置WLAN基础配置(SSID、安全等) |
| `SetWlanRadioConfiguration` | 设置射频配置(信道、功率等) |
| `Commit` | 提交配置 |
| `GetDeviceInfo` | 获取设备信息 |
| `GetWlStats` | 获取无线统计 |

---

### 6.4 日志采集命令汇总

```bash
# ===== 文件日志（无法远程时使用） =====
wlancmd show log                                    # AP上下线操作日志(汇总)
cat /proc/wifilog/debug/All                         # WiFi模块所有调试日志
cat /var/smartlink/topodebug.0.log                  # 拓扑调试日志

# ===== TRACE日志（可远程或现场复现） =====
# 登录光猫telnet执行
mid set mid 33 flag 1                               # 开启UDM模块TRACE (Master和Slave都用33)
mid set mid 1000 flag 1                             # 开启WiFi AP2模块TRACE
mid set mid 500 flag 1                              # 开启控制台TRACE
mid get                                             # 查看已开启的MID
mid set mid 33 flag 0                               # 关闭TRACE

# ===== 从AP侧TRACE日志 =====
# 需要telnet到AP设备执行
mid set mid 33 flag 1                               # 开启AP侧UDM TRACE

# ===== Action交互日志 =====
# 在主网关上执行
udm show log                                        # 查看Hilink Action交互
udm cl log                                          # 清空日志

# ===== 静态日志 =====
WAP:display debuglog info                           # 查看静态调试日志(含心跳超时)
```

---

### 6.5 三重日志机制

某些关键事件会**同时记录三种日志**，方便不同场景下定位：

**AP离线事件示例** (`cover_ap_offline.c:453-455`):
```c
HW_WAP_TRACE(HW_MID_WIFI_AP2, "COVER_PonApLeaveProc ApInst offline:%u", apInst);  // TRACE
HILINK_DEBUG_LOG("COVER_PonApLeaveProc ApInst offline:%u", apInst);                 // 调试日志
HW_WLAN_SaveLog("COVER_PonApLeaveProc ApInst offline:%u", apInst);                  // 文件日志
```

**配置失败事件示例** (`wifi_cover_vap_sync.c`):
```c
HW_WLAN_SaveLog("[XLINK] ApId: %u do start fail:Ret:%u", apInstId, ret);  // 文件日志(重要!)
LAST_WORD(ret, apInstId);                                                  // 错误日志
```

| 场景 | TRACE | 调试日志 | 文件日志 |
|------|-------|----------|----------|
| AP离线 | ✓ | ✓ | ✓ |
| Start失败 | - | - | ✓ |
| CfgAp失败 | - | - | ✓ |
| 心跳超时 | - | - | - (仅静态日志) |

---