# propose_mapping

启用时读取当前设备上下文，提出 key.12 改为 Enter（HID usage 40）的建议，并保持在线等待用户审阅；完成后可在控制台停用本示例。日志中的 pending 不代表写入成功。请先审阅建议；批准到草稿后仍需在控制台确认写入。若只观察示例，可拒绝建议，不必改写设备。

需要带扩展 Runner / API 1.0 的 BORING Console 和被接受的设备会话。不支持通过直接运行 main.py 替代宿主。

修改并分享时保留 LICENSE 和 NOTICE，遵守 PolyForm Noncommercial 1.0.0；商业复用另行授权。
