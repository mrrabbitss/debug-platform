# 历史案例：2.4GHz 配置非法信道导致 hostapd 重载失败

设备类型：AP
模块：WLAN
现象：配置下发后 SSID 消失，hostapd 日志出现 Failed to set beacon parameters，驱动返回 -22。
根因：配置转换模块未根据 band 校验 channel，将 5GHz 信道写入 2.4GHz profile。
修复：在配置落盘前联合校验 band、country code 和 channel；hostapd 重载失败时保留旧配置并回滚。
验证：覆盖合法边界、跨频段信道、配置回滚和连续重载测试。
