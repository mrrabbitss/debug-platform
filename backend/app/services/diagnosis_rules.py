from __future__ import annotations

import re
from typing import Any


_HEARTBEAT_TIMING = re.compile(
    r"curtime\[(?P<current>\d+)\].*?"
    r"iadvrtimeout\[(?P<timeout>-?\d+)\].*?"
    r"lasteventtime\[(?P<last>\d+)\]",
    re.IGNORECASE,
)


def heartbeat_timeout_confirmed(value: str) -> bool:
    """Apply the fault-tree timeout equation to one GW log record."""

    match = _HEARTBEAT_TIMING.search(value)
    if not match:
        return False
    current = int(match.group("current"))
    timeout = int(match.group("timeout"))
    last = int(match.group("last"))
    return timeout >= 0 and current >= last and current - last > timeout


HYPOTHESIS_RULES: dict[str, dict[str, Any]] = {
    "KERNEL_OOPS": {
        "title": "内核或驱动异常导致设备服务不可用",
        "description": "日志出现内核 Oops、panic、调用栈或段错误，应优先检查驱动、内核模块和崩溃前后的资源状态。",
        "priority": "P0",
        "actions": ["保留完整调用栈和崩溃时间前后日志", "确认固件与驱动版本", "结合符号表定位崩溃函数"],
    },
    "PROCESS_CRASH": {
        "title": "关键进程异常退出",
        "description": "用户态进程出现崩溃或被信号终止，可能由非法配置、内存错误或依赖服务异常触发。",
        "priority": "P0",
        "actions": ["检查 core dump 和 backtrace", "核对崩溃前配置变更", "使用 ASan/静态分析复现相关模块"],
    },
    "AP_UDM_PROCESS_ABNORMAL": {
        "title": "AP 侧 UDM 进程或协议栈异常",
        "description": "AP 侧明确出现 UDM 进程异常、被信号终止或复位；该状态会破坏监听端口与 Advertise 心跳，是频繁离线的高优先级根因候选。",
        "priority": "P0",
        "actions": [
            "保存 UDM core dump/backtrace 并核对崩溃前资源状态",
            "检查 UDM 进程重启后 1900/37443 监听端口是否持续可用",
            "按 AP 标识对齐 AP 心跳发送失败与 GW 心跳超时、拓扑离线时序",
        ],
    },
    "AP_UDM_LISTEN_PORT_FAILED": {
        "title": "AP 侧 UDM 监听端口不可用",
        "description": "UDM 监听端口检查失败或端口不存在，会影响 SSDP/HTTPS 协议交互，应结合进程存活和端口恢复记录判断。",
        "priority": "P1",
        "actions": ["检查 1900/37443 端口占用和监听状态", "核对 UDM 进程启动、端口创建与异常复位时序"],
    },
    "AP_UDM_HEARTBEAT_SEND_FAILED": {
        "title": "AP 侧 Advertise 心跳发送失败",
        "description": "AP 未能发送 alive advertisement；若随后 GW 出现心跳超时和拓扑离线，可形成跨设备因果证据链。",
        "priority": "P1",
        "actions": ["核对发送失败返回码", "检查 SSDP 多播路径及 UDM 进程/端口状态", "与 GW 侧超时日志按时间对齐"],
    },
    "GW_AP_HEARTBEAT_TIMEOUT": {
        "title": "GW 侧 AP 心跳超时",
        "description": "GW 侧 curTime 与 lastEventTime 的差值超过 iAdvrTimeOut，表明控制点未按期收到 AP Advertise 心跳。",
        "priority": "P1",
        "actions": ["核对超时计算和 AP 标识", "关联 AP 侧心跳发送、进程与端口日志", "确认同一时刻拓扑离线事件"],
    },
    "HOSTAPD_START_FAILED": {
        "title": "hostapd 配置或驱动交互失败",
        "description": "无线服务在启动/重载阶段失败，常见原因包括信道、国家码、加密参数、接口状态或驱动能力不匹配。",
        "priority": "P0",
        "actions": ["对比生效配置与产品约束", "检查驱动初始化和无线接口状态", "核对失败前后的配置下发日志"],
    },
    "AUTH_FAILED": {
        "title": "无线认证或密钥协商失败",
        "description": "认证、EAP 或四次握手失败，需结合安全模式、密钥、时间同步和终端兼容性分析。",
        "priority": "P1",
        "actions": ["确认认证模式与密钥配置", "检查 EAP/4-way handshake 前后日志", "必要时采集空口报文"],
    },
    "DHCP_FAILED": {
        "title": "DHCP 地址分配链路异常",
        "description": "客户端或 WAN 侧未完成 DHCP 交互，可能与接口状态、地址池、转发/VLAN 或对端响应有关。",
        "priority": "P1",
        "actions": ["检查 DISCOVER/OFFER/REQUEST/ACK 链路", "核对 VLAN 和桥接配置", "使用抓包确认报文是否到达"],
    },
    "PPPOE_FAILED": {
        "title": "PPPoE 建链或认证失败",
        "description": "PADI/PADO 或 PAP/CHAP 阶段失败，需区分链路不可达、账号认证和会话异常。",
        "priority": "P1",
        "actions": ["确认 WAN 链路与 VLAN", "核对 PADI/PADO 时序", "检查账号认证返回码"],
    },
    "PON_LOS": {
        "title": "PON 光链路异常",
        "description": "检测到 LOS、光信号丢失或 PON 状态异常，应优先检查光功率、链路和注册状态。",
        "priority": "P0",
        "actions": ["检查光功率和 LOS 告警", "确认 ONU 注册状态", "核对异常前后的 PON 状态变化"],
    },
    "OMCI_ERROR": {
        "title": "OMCI 配置或交互异常",
        "description": "OMCI 消息超时、失败或属性不匹配，可能影响业务配置下发和 ONU 管理。",
        "priority": "P1",
        "actions": ["定位失败的 ME/属性", "对比 OLT 下发与设备响应", "检查同版本历史案例"],
    },
    "TR069_ERROR": {
        "title": "TR-069/CWMP 管理链路异常",
        "description": "远程管理参数或会话异常，需检查 ACS 连通性、参数合法性和会话状态。",
        "priority": "P2",
        "actions": ["检查 Inform/响应流程", "核对参数路径和类型", "确认 ACS 网络连通性"],
    },
    "MEMORY_PRESSURE": {
        "title": "内存压力或资源泄漏",
        "description": "日志出现 OOM、分配失败或内存泄漏特征，可能导致进程异常、看门狗重启或业务退化。",
        "priority": "P0",
        "actions": ["对比故障前后内存指标", "检查长期增长进程", "使用 Valgrind/ASan 或内存统计复现"],
    },
    "CONFIG_INVALID": {
        "title": "配置项缺失或取值不合法",
        "description": "系统明确报告配置错误，应追踪配置来源、版本迁移和参数校验链路。",
        "priority": "P1",
        "actions": ["确认配置来源和最后修改时间", "核对字段范围及默认值", "检查配置转换和落盘结果"],
    },
}
