# Power V2 蓝牙配对保留补丁（编译前必做）

从 20261118.01 起，`matrix12-power-v2-codex-usb` 和 `matrix12-power-v2-codex-ble` 两个目标只能用打过补丁的 ESP-IDF 6.0.2 编译。没打补丁时 `tools/build.sh` 会直接停下并提示本文件。

## 为什么要打

ESP-IDF 6.0.2 自带的 Bluedroid 在一次蓝牙认证失败后，会删除设备端保存的配对密钥（运行态记录和 NVS 里的 bond）。电脑还留着旧密钥，设备却没有了，表现为“系统显示已配对但连不上”。Espressif 后续的 `release/v6.0` 已改为外设角色默认保留 bond；本补丁把这两处保留行为移植到固定使用的 6.0.2。

认证失败后只记日志和诊断，不自动清槽或删键。清除槽位和恢复出厂设置仍会删除 bond，但都需要用户在设备上明确操作。

补丁修改的是外部 ESP-IDF 源码，只复制本仓库的 `firmware/` 不会让镜像带上修复。官方签名包也用同一补丁构建。

## 一次性设置

从仓库根目录执行，用一份干净的 ESP-IDF 6.0.2 另建补丁副本（路径换成本机实际路径）：

```sh
repo_root="$(pwd)"
base_idf=/path/to/esp-idf-v6.0.2
patched_idf=/path/to/esp-idf-v6.0.2-boring-ble
git -C "$base_idf" worktree add --detach "$patched_idf" v6.0.2
git -C "$patched_idf" submodule update --init --recursive
git -C "$patched_idf" apply \
  "$repo_root/firmware/tools/patches/esp-idf-v6.0.2-preserve-peripheral-bond.patch"

source "$base_idf/export.sh"
export IDF_PATH="$patched_idf"
export PATH="$IDF_PATH/tools:$PATH"
cd "$repo_root/firmware"
sh tools/build.sh --target matrix12-power-v2-codex-usb --custom-name mykeys
```

补丁只打一次。已经打过旧版单处补丁的 SDK，建议按上面的方法从干净的 `v6.0.2` 重建，不要重复叠加。

## 另一处蓝牙修复不需要打补丁

`components/esp_hid/` 是 ESP-IDF 6.0.2 `esp_hid` 组件的副本（Apache-2.0，保留 Espressif 版权头），补上“为已配对主机记住输入报告订阅”的行为，修复 Windows 在设备重启或更新后显示已连接却收不到按键的问题。项目内同名组件会自动覆盖 ESP-IDF 自带版本，不需要改工具链。改动说明见 [components/esp_hid/BORING_CHANGES.md](components/esp_hid/BORING_CHANGES.md)。

## 排查

蓝牙再次连不上时，可通过 USB 读取 `GET_STATUS.codex_micro` 的 `auth_fail_events`、`last_auth_fail_reason`、`bond_count` 和 `active_slot_bond_state`。配对槽位还在、bond 却没了，说明两边状态失配；这个读数本身说明不了密钥为什么丢。

`CONFIG_BT_SMP_MAX_BONDS` 为 6，给三个主机槽位之外的配对过程留余量。不要调低：容量变小时协议栈可能删除已有配对。

已失配的旧设备不会因升级自动清除配对。需要用户在设备上清除对应的蓝牙槽位后重新配对，电脑端的旧记录也要自己“忽略设备”。
