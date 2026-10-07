# 恢复工具第三方组件

本 DMG 包含 Python 3.13、pyserial 3.5、cryptography 46.0.7、cffi 2.1.1 、OpenSSL 3.0.16 及其运行依赖。构建使用 PyInstaller 6.22.3（含分发例外）、hooks-contrib、altgraph、macholib 和 packaging。许可文本位于 licenses/，保留其原授权；BORING 非商业许可不重新授权第三方代码。

内置固件基于 ESP-IDF 6.0.2，包含 cJSON、TinyUSB、esp_tinyusb、esp_secure_cert_mgr、led_strip、esp_lcd_st7735 等组件；组件许可位于 licenses/firmware。实际依赖版本见配套固件源码的 dependencies.lock。

本工具不依赖 Qt。修改后重新打包时，保留实际分发组件的许可证和版权声明。
