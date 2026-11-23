# 开发交接

本目录是 BORING MIST 固件更新与校准工具（官方 run37 版本）的公开源码。用户只有 USB 数据线，不能拆机或按 BOOT。工具把内置、带官方发布签名的固件写入一台正常开机的设备，然后清空蓝牙槽位、设置第 8 键并校准摇杆。

- 入口 `authority_update_app.py`（pywebview 窗口 + 本机 HTTP）；页面状态和各步骤在 `provisioner/authority_update_server.py`；USB 更新 `ota.py` / `workflow.py`；串口协议 `protocol.py`；重启核对 `runtime.py`；蓝牙槽位 `ble_recovery.py`；第 8 键 `voice_key.py`；摇杆校准 `joystick_recovery.py`；固件包读取 `release.py`；打包前整理固件 `packaging/macos/prepare_bundle.py`。
- 固件只来自打包时整理进 `releases/` 的那一份（`bundled-authority.json` 指向它），打包后的程序不会去找别处的固件包。先核对清单和镜像哈希、设备硬件，再写入应用并等待同一设备的精确版本和分区回读。不允许整片擦除、改分区、写身份或 eFuse。
- 未通过样机验证（`built`）的固件只能用 `--acceptance-test` 打成明确标注的测试包。
- 蓝牙槽位只在固件验证通过后清除，逐个清除并读回；第 8 键只改两个出厂 Profile；校准必须通过保存读回、静止和四向检查才算完成。
- 界面只显示能执行的操作；检测到串口不是成功。无法通信时保留错误和记录，不承诺能修复所有硬件故障。
- 不伪造签名、不改信任公钥、不把自改固件当官方。发布私钥、生产身份签发工具不在本仓库。
- 验证：`python -m unittest discover -s tests -t .`；改动后重新打包，并在实体样机上实测扫描、写入、版本回读、槽位清空、第 8 键和校准。
