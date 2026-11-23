# esp_hid（覆盖 ESP-IDF v6.0.2）

本目录是 ESP-IDF **v6.0.2**（tag 提交 `7101770dc6db2667b3c477cc31365dd1acd6db4e`）
`components/esp_hid` 的副本。项目 `components/` 下的同名组件会覆盖 ESP-IDF
自带的组件，所以任何一台机器构建本项目都会自动用这份代码，不需要给工具链打补丁。

## 为什么要覆盖

ESP-IDF 的 BLE HID 只有在主机写过某个输入报告的 CCCD（订阅开关）之后才发送
该报告（`esp_ble_hidd_dev_input_set`：`p_rpt->ccc.value` 为 0 时返回
`ESP_FAIL`，日志 `Indicate Not Enabled`）。这些值只在内存里，设备一重启就清零。

蓝牙规范要求服务端为已配对主机记住 CCCD；Windows 依赖这一点，重连时通常不再
重新订阅。结果是设备关机再开、OTA、菜单重启之后，Windows 显示已连接但收不到
任何按键。macOS 每次连接都会重新订阅，所以不受影响。

## 改了什么

只**新增**，不改原有行为：

- `include/esp_hidd_ble_subscriptions.h`：新头文件。
- `src/ble_hidd.c`：文件末尾新增四个函数（读取、读取全集、设置订阅掩码，以及
  查询某个输入报告是否已订阅）。`set` 只改内存状态，不调用
  `esp_ble_gatts_set_attr_value()`，以免打乱 `input_set()` 等待的信号量。

保存和恢复由应用层完成（`components/codex_micro/codex_micro_ble.c`）：按槽位把
掩码存进 NVS，加密完成时恢复。

## 升级 ESP-IDF 时

把新版本的 `components/esp_hid` 整体拷过来，再把上面两处新增内容合进去。
`tools/tests/test_esp_hid_override.py` 会检查本目录除新增部分外与记录的上游
版本一致。
