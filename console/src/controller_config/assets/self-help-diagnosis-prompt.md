请帮我排查 BORING 设备遇到的问题，最后整理一份报告。我会先看报告，再自己决定要不要发给 BORING 团队。

【背景】
BORING 是 AI 任务调度和状态反馈面板：BORING MIST 设备（用 USB 线或蓝牙连电脑）加 BORING Console 配置软件（Mac／Windows）。设备上的状态灯显示 AI 任务进行到哪一步，按键用来切换和发送 AI 任务。它不是普通打字键盘，不要用“能不能打出字”判断设备好坏。
提示词版本：diag-v1-20261123

【规矩，必须遵守】
1. 只读：只运行查看信息的命令。不安装软件，不用 sudo 或管理员权限，不改系统设置，不删文件，不结束任何进程。
2. 不碰设备：不要用代码、串口或 HID 工具给设备发数据。不刷固件、不恢复出厂、不删除或重新配对蓝牙。如果你认为需要做这些，先说明原因和影响，由我自己决定、自己操作。
3. 隐私：不收集密码、令牌、API Key、聊天内容、提示词和代码，不读和本问题无关的文件，不写其他设备的名字。路径里的用户名改成 ~。不上传任何内容，报告只存在这台电脑上。
4. 需要我动手时，标出【请你操作】，一次只说一件事：点哪里、看什么。等我回复“好了”或我看到的情况，再继续。
5. 如果你不能在这台电脑上运行命令，就改成一步一步指导我做，不要假装已经查过。
6. 别凭一次现象下结论。分清“已确认”“可能原因”“还要验证”。不要承诺能修好。

【设备识别关键词】
BORING、Boring Mist（蓝牙默认名，可能被改过）、Codex Micro、Work Louder、WMP Config CDC、VID 0x303A（十进制 12346）、PID 0x8360（十进制 33632）。下文说的“设备”都按这些关键词找。

【Console 诊断信息】
[[BORING_CONSOLE_DIAGNOSTICS]]
如果上面写的是“未带上 Console 诊断信息”：问我能不能打开 BORING Console。能打开，就请我找“复制诊断信息”或“复制诊断摘要”按钮，点一下，把内容贴给你。找不到就跳过，不要反复要。

==== 第 1 步 弄清问题 ====
先从我已经说的话里整理下面几项。缺的再问我，一次最多问 3 个，用简单的话问。
- 问题类型，选最接近的一个：
  A 电脑或 Console 找不到设备
  B 蓝牙连不上、连得慢、经常断
  C 开不了机、屏幕不亮、像坏了
  D 状态灯不跟着 AI 任务变，或按状态灯键不跳到对应对话
  E 按键或语音键没效果、发不出去
  F 快捷键和别的软件冲突
  G 摇杆自己动、误触
  H Console 打不开、闪退、卡住、占用高
  I 固件更新失败
  J 其他
- 原本想看到什么，实际看到什么；设备屏幕和灯是什么样子。
- 什么时候开始；每次都会还是偶尔；做了什么之后出现。
- 用 USB 还是蓝牙；USB 线是直接插电脑，还是经过扩展坞、显示器或转接头。
- 配合哪个 AI 应用用（Codex、Claude Code、豆包等），是桌面应用、命令行还是编辑器插件。
- 最近有没有升级 Console 或固件、更新系统、换电脑。

==== 第 2 步 通用信息（你直接收集）====
先判断这台电脑是 Mac 还是 Windows，只走对应的一边。每段输出只保留和设备有关的部分。

Mac（终端）：
- sw_vers；sysctl -n hw.model；uname -m
- Console 版本：/Applications 和 ~/Applications 里名字含 BORING 的 .app，各运行 defaults read "<应用路径>/Contents/Info" CFBundleShortVersionString
- Console 是否在运行：ps -axo pid,etime,comm | grep -i boring | grep -v grep
- USB：system_profiler SPUSBHostDataType（没有结果就用 SPUSBDataType），只留设备那段；ls /dev/cu.usbmodem*
- 蓝牙：system_profiler SPBluetoothDataType，只留设备那段，写明它在“已连接”还是“未连接”下面
- hidutil list：保留表头和含关键词的行（Transport 列说明走 USB 还是蓝牙）
- 崩溃记录（只要文件名和时间）：ls -lt ~/Library/Logs/DiagnosticReports | grep -i boring | head -10

Windows（PowerShell，普通权限）：
- Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,BuildNumber；(Get-CimInstance Win32_ComputerSystem).Model
- Console 版本：Get-ItemProperty HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*,HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\* -ErrorAction SilentlyContinue | Where-Object DisplayName -like "BORING*" | Select-Object DisplayName,DisplayVersion,InstallLocation
- Console 是否在运行：Get-Process | Where-Object Name -like "BORING*" | Select-Object Name,Id,StartTime
- 设备（含已配对但没连上的）：Get-PnpDevice | Where-Object { $_.InstanceId -match "303A" -or $_.FriendlyName -match "BORING|Boring Mist|Codex Micro" } | Select-Object Status,Class,FriendlyName,InstanceId
- 串口：Get-PnpDevice -Class Ports -PresentOnly | Select-Object Status,FriendlyName,InstanceId
- 崩溃记录：Get-WinEvent -FilterHashtable @{LogName='Application';ProviderName='Application Error';StartTime=(Get-Date).AddDays(-7)} -ErrorAction SilentlyContinue | Where-Object Message -match "BORING" | Select-Object -First 10 TimeCreated,Message

==== 第 3 步 按问题类型查（只做对应的那一类）====
A 找不到设备
- USB：设备有没有出现在第 2 步的 USB 或设备列表里。没有的话，【请你操作】换一根确定能传数据的线，直接插电脑本身的接口（不经过扩展坞、显示器或转接头），等 10 秒，你再查一次。
- 蓝牙：系统蓝牙里设备是否“已连接”。插着 USB 线时设备走 USB，不走蓝牙。
- Mac 用蓝牙时：【请你操作】打开“系统设置 › 隐私与安全性 › 蓝牙”，告诉我 BORING Console 是否已允许（只看，不改）。
- 设备屏幕亮不亮、显示什么。
B 蓝牙连不上、慢、断
- 这台设备以前有没有和别的电脑配过蓝牙；现在那台电脑是不是也开着。
- 连不上时系统或 Console 的提示原文。
- 慢：从设备开机或唤醒，到 Console 显示“蓝牙已连接”，大约几秒。Console 诊断信息里的 connection_timing、“连接耗时”原样保留。
- 断：大约连多久会断；断的时候电脑是否在睡眠；断了以后按设备任意键能不能连回来。
C 开不了机、屏幕不亮
- 只问我看到的：插充电器、插电脑时，屏幕和灯各有什么反应。
- 【请你操作】用能传数据的线把设备直接插到电脑上（不经过扩展坞），你再做一次第 2 步的 USB 检查。
- 屏幕如果提示用 USB 连接电脑修复：设备进入了自我保护，配置还在。请我用 USB 直连后打开 Console，按 Console 的提示操作。
- 不要让我尝试按键组合、刷机或拆机。
D 状态灯不跟、按状态灯键不跳转
- 用的是哪个 AI 应用，以及桌面应用、命令行还是插件。
- 设备屏幕主页显示的是 BORING 字样，还是 AI 应用的图标。
- 【请你操作】让那个 AI 应用跑一个简单任务（比如回复“OK”），告诉我灯的颜色有没有变；再按一下对应的状态灯键，看有没有切到那个对话、窗口有没有到最前面。
- 【请你操作】完全退出再重开那个 AI 应用，再试一次。
- 【请你操作】设备关机再开机，看灯是先变白、过一会儿恢复颜色，还是一直白。
- Mac 上用 Codex 时：ioreg -l -w0 -r -c IOHIDDevice，找出设备的每个节点，记下 Product、Transport、PrimaryUsagePage 和下面的 IOUserClientCreator。写明 PrimaryUsagePage 为 65280 的节点有没有被 Codex 或 ChatGPT 进程打开。
E 按键或语音键没效果
- 哪个键，在哪个应用里按的；Console 里这个键设的是什么（只看）。
- 语音键：用的是什么语音输入软件，它的快捷键是什么。设备出厂发的是右 Option + 空格（Windows 为右 Alt + 空格），两边要一致。
- 快捷提示词发不出去：Mac 上【请你操作】看“系统设置 › 隐私与安全性 › 辅助功能”里 BORING Console 是否打开（只看，不改）；Console 提示词页“诊断信息”里提示词助手是什么状态。
F 快捷键冲突
- 冲突的是哪个组合键，按下后实际触发了什么。
- 正在运行的输入法、语音软件、截图或会议软件里，有没有设了同一个组合键（只看它们的设置）。
G 摇杆误触
- 不碰摇杆时会不会自己触发，偏向哪个方向，多久一次。
- 有没有在 Console 里做过摇杆校准；Console 诊断信息里的 joystick_diagnostics 原样保留。
H Console 打不开、闪退、卡住、占用高
- 打开时的提示原文（截图里的文字也行）；第 2 步查到的崩溃记录。
- 是否同时装了或开着两个 BORING Console（例如官方版和 DIY 版、开发版）。
- Mac：log show --last 30m --style compact --predicate 'process BEGINSWITH "BORING"' | tail -200
- 占用高：【请你操作】在活动监视器（Windows 是任务管理器）里看是哪个进程占得高，进程名原样告诉我；关掉 Console 后会不会回落。
I 固件更新失败
- 更新时是 USB 直连、经过扩展坞，还是蓝牙（固件更新需要 USB 直连）。
- 进度停在哪里、报错原文；失败后设备屏幕和 Console 显示什么。
- 不要让我反复重试；最多再试一次，结果写进报告。

==== 第 4 步 能确定的，直接告诉我 ====
下面几种情况已经清楚，对上了就直接告诉我原因和办法（由我自己去做）：
- USB 经过扩展坞、显示器、转接头，或线只能充电：电脑常常认不到设备。换能传数据的线直接插电脑。
- 设备插着 USB 线时走 USB，拔掉线才走蓝牙。
- 只有蓝牙下 Console 提示“配置连接失败”，USB 正常：旧版 Console 的已知问题，请升级到官网最新版 Console。
- 屏幕主页显示 BORING 字样是普通模式，显示 AI 应用图标是对应的 AI 模式，在设备上双击旋钮切换。普通模式下按状态灯键会不会跳到对话，要看 Console 里“按下状态灯键时”的设置。
- 语音键没反应，而语音软件的快捷键和设备发的不一样，或者被别的软件占用：把两边改成一致即可。
其他情况不要猜原因，写进报告。

==== 第 5 步 写报告 ====
把报告保存到桌面：BORING-诊断-<年月日-时分>.md。写完把全文给我看，并告诉我文件的完整路径。我看过以后自己决定发不发。不要替我发送或上传。
报告按下面的格式写，没查到的项写“未查到”，不要编：

# BORING 诊断报告
- 生成时间：
- 提示词版本：diag-v1-20261123
- 收集方式：AI 自动收集／AI 指导我手动收集
## 1. 概要
| 项目 | 内容 |
|---|---|
| 问题类型 | A～J 中的一个 |
| 一句话现象 | 预期什么，实际什么 |
| 电脑 | 系统和版本、机型、芯片 |
| Console 版本 | |
| 固件版本 | 来自 Console 诊断信息 |
| 连接方式 | USB 直连／USB 经扩展坞或转接头／蓝牙／两种都试过 |
| 配合的 AI 应用 | 名称、形态、版本 |
| 出现频率 | 每次／偶尔／只出现过一次 |
## 2. 复现步骤
编号写：我做了什么 → 预期看到什么 → 实际看到什么。
## 3. 结论
- 已确认：
- 可能原因：
- 还要验证：
- 已排除（每条写依据）：
## 4. 已经试过的办法和结果
## 5. 原始报错（原样照抄）
## 6. 收集到的信息
按步骤分段，命令输出放进代码块，注明步骤和时间。Console 诊断信息原样放在这里。
## 7. 没查到的项

现在先根据我说的情况开始。
