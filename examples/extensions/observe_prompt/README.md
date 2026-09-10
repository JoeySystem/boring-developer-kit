# observe_prompt

启用后打印 extension.ready 和当前设备标识，然后持续打印 prompt.triggered 事件。需要设备已配置可触发的提示词。只观察，不阻止默认粘贴。停用后进程结束。

需要带扩展 Runner / API 1.0 的 BORING Console 和被接受的设备会话。不支持通过直接运行 main.py 替代宿主。

修改并分享时保留 LICENSE 和 NOTICE，遵守 PolyForm Noncommercial 1.0.0；商业复用另行授权。
