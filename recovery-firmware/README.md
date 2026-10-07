# 20261007.01 恢复固件源码

这是与官方售后恢复包功能对应的源码快照，独立于仓库原有 `firmware/` 历史参考，避免把此前参考版本静默替换。

来源为固件提交 `ae3b06fbe40a3140016202440b9e8d3aee3a14a3` 加本目录 `startup-recovery.patch`。运行源码已经包含补丁；不要再重复应用。官方镜像构建号为 `20261007.01-gae3b06fb-dirty`，产品版本 `0.3.0-alpha.1`，目标 `matrix12-power-v2-codex-usb`，硬件 `WMP-S3-MATRIX12-POWER-V2`。

包含两项修复：首次电量读取无效时保留此前的开机状态；开机自检灯光从首次实际渲染计时。这个快照保留原始工程状态，不保证重新构建得到逐字节相同的官方二进制。

使用 ESP-IDF 6.0.2；组件管理器按 dependencies.lock 获取依赖。激活 ESP-IDF 后：

```sh
cd recovery-firmware/firmware
sh tools/build.sh --target matrix12-power-v2-codex-usb --custom-name recovery-debug
```

输出为自定义固件。修改后不能复用原官方签名或冒称官方恢复包。普通客户应使用 [恢复工具](https://github.com/JoeySystem/boring-developer-kit/tree/main/recovery-tool) 附带的已签名二进制，优先修改电脑端诊断/连接问题。

公开范围包含应用运行源码、编译所需内嵌资源、公开构建/自定义打包工具及协议常量头，不包含生产签发工具、私钥、实际设备证书、台账、用户备份或内部硬件资料。既有开发包许可适用，见 LICENSE/NOTICE。

样机已验证启动、USB 读写和工具恢复后官方 Console 0.2.1 识别；客户闪灯故障尚未复现，蓝牙异常仍未定位。具体证据见 [恢复验证记录](https://github.com/JoeySystem/boring-developer-kit/blob/main/docs/verification-mist-recovery-20261007.md)。
