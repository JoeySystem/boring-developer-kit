# 应用更新代码与公开构建

控制台应用更新与设备固件维护是两条独立流程。本轮公开 macOS Sparkle 客户端、Windows 安装器客户端、取消下载及退出前保存/重启后恢复草稿的代码；没有发布对应 0.1.24 安装包。

## 默认行为

`console/src/controller_config/assets/app-update-source.json` 的 `macos`、`windows` 均为空 URL 和公钥。源码启动会显示未配置状态，不访问官方应用更新源。`firmware-source.json` 的固件地址也为空。

获取公开代码更新请使用本 GitHub 仓库或源码 Release。默认原生构建不需要官方签名凭据，也不会覆盖官方应用；Community 应用名称、设置和本地服务保持隔离。

## 自行维护分发渠道

自行分发更新需使用自己控制的 HTTPS 源、签名密钥和稳定的应用标识，不能把公开构建接到官方产品源。签名私钥留在发布机器，应用只携带公钥。

- macOS：`BORING_MACOS_UPDATE_FEED_URL`、`BORING_MACOS_UPDATE_PUBLIC_KEY`、`BORING_SPARKLE_FRAMEWORK` 供 `console/tools/stage_macos_updater.py` 和构建脚本使用。稳定渠道还需 `BORING_MACOS_SIGN_IDENTITY`，公证可指定 `BORING_NOTARY_PROFILE`。框架二进制不在源码包中，许可见 `console/src/controller_config/assets/Sparkle-LICENSE.md`。
- Windows：`BORING_WINDOWS_UPDATE_FEED_URL`、`BORING_WINDOWS_UPDATE_PUBLIC_KEY`，可选 `BORING_WINDOWS_SIGN_CERT_SHA1`。构建脚本校验渠道和安装器条件；稳定渠道要求系统签名，不能用 portable ZIP 替代 Setup EXE。`console/tools/build_windows_update_feed.py` 提供签名 feed 制作入口，密钥路径仅由发布时显式传入。

`trial` 是代码支持的测试渠道选项，不表示本公开仓库已经建立对应服务。已有应用公钥变更受构建校验约束；不要绕过校验，否则旧安装无法信任更新。

测试中的安装器、签名和更新 feed 使用临时合成数据；平台分支的测试通过，不代表 macOS/Windows 安装替换、系统提示、签名或恢复流程已实机验收。
