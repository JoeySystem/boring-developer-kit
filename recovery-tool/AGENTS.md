# 开发交接

本目录为从 BORING 权威包临时更新工具裁剪的独立售后恢复工具。用户通常只有 USB 数据线，不能拆机或按 BOOT。目标是恢复到内置、带官方发布签名的固件。

- 入口 authority_update_app.py；HTTP 和任务状态 provisioner/authority_update_server.py；USB 更新 ota.py/workflow.py；串口协议 protocol.py；重启核对 runtime.py；签名与 Console 官方记录 official_recovery.py。
- 固件仅来自 releases/recovery，禁止误选开发者机器上的其他权威包。先验证签名和镜像，再核对设备硬件，刷写应用并等待同一设备的精确版本和分区回读。不允许整片擦除、身份/eFuse 写入。
- 用户界面只显示可执行操作；检测到串口不是恢复成功。无法通信时保留错误并提供诊断，不承诺能修复所有硬件故障。
- 官方识别记录只在确切恢复成功后登记，且必须通过内置官方公钥验签。不伪造签名、不改信任密钥、不把自改固件登记为官方。发布私钥不在源码中。
- 不引入新的 hash、状态框架或过度防御；保留复用工具已有校验。
- 验证：运行 tests/ 的协议、重连、签名与登记测试；构建后必须实测安装包的扫描、刷入、版本回读和 Console 识别。
