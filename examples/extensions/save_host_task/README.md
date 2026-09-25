# 保存电脑端任务事件

要求 Console 本地 API 1.1 / SDK 1.1.0。导入此示例后，将一个电脑端任务绑定到扩展的 `save_event` 动作，再主动触发该任务。

示例将本次 `host_action.triggered` 事件写入系统临时目录的 `boring-extension-example-*/task-event.json`，并在运行结果中显示路径。任务处理步骤之间会检查停止请求；停止前已经写入的文件会保留。

仅演示本地文件输出，不发送网络请求，也不修改设备配置。请勿将包含个人内容的输出文件直接公开。运行由控制台专用 Runner 管理，不直接运行 main.py。
