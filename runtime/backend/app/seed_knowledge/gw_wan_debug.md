# GW WAN、DHCP 与 PPPoE 故障诊断规则

## DHCP
DHCP 诊断应检查 DISCOVER、OFFER、REQUEST、ACK/NAK 是否完整，并结合接口 link 状态、VLAN、桥接、地址池和防火墙转发。没有收到 OFFER 可能是报文未发出、链路/VLAN 不通或对端无响应，不能一概归因于 DHCP 客户端。

## PPPoE
PPPoE 需要区分发现阶段和会话阶段。PADI 超时优先检查链路和 VLAN；PAP/CHAP 认证失败优先检查账号、密码和对端返回码；会话建立后断开需结合 LCP、超时和链路变化分析。

## WAN 链路
WAN link down、carrier lost 和 PPP 会话中止之间可存在关联。应以物理链路事件、驱动日志和协议时序共同确认。
