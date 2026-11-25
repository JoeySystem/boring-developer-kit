# 工具第三方组件

打包后的程序包含 Python（Mac 为构建机的 Python 3，Windows 为 Python 3.12 x64）、pyserial 3.5、pywebview 6 及其依赖：bottle、proxy_tools、typing_extensions；Mac 版另含 PyObjC（core、Cocoa、Quartz、Security、UniformTypeIdentifiers、WebKit），Windows 版另含 pythonnet、clr_loader、cffi、pycparser，并使用系统自带的 Microsoft Edge WebView2。构建使用 PyInstaller 6（含分发例外）、pyinstaller-hooks-contrib、altgraph、macholib 和 packaging。

许可文本位于 `licenses/`。pythonnet、clr_loader 与 proxy_tools 为 MIT 许可，重新分发 Windows 版时请随包附上其许可文本。BORING 非商业许可不重新授权第三方代码。

内置固件基于 ESP-IDF 6.0.2，包含 cJSON、TinyUSB、esp_tinyusb、esp_secure_cert_mgr、led_strip、esp_lcd_st7735 等组件，以及项目内覆盖的 ESP-IDF `esp_hid` 组件（Apache-2.0）；组件许可位于 `licenses/firmware`，实际依赖版本见 `../firmware/dependencies.lock`。

修改后重新打包时，保留实际分发组件的许可证和版权声明。
