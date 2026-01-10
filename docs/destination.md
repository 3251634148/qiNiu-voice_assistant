1. 工作的时候常用的工作软件比如飞书，企业微信等  
日常通信软件比如微信，qq，微博，小红书等  
音视频播放软件比如：qq音乐，网易云音乐，苹果音乐，spotify，爱奇艺，抖音，酷狗音乐，优酷等等  
开发软件比如：vscode，cursor等ide并在上面编写代码实现简单功能；  
游戏软件比如用户本地下载好的游戏；  
2. 需要的动作是：打开应用，到处，发消息，填表，点击某个按钮，播放音乐，编写代码，新建/删除/保存/运行文件；  
3. 控制端与 Agent 的通信方式偏好：websocket

按你列的应用类型与动作集合，“用脚本接口（osascript/ScriptingBridge/COM）精确控制”只能覆盖很小一部分；真正能覆盖 飞书/企微/微信/QQ/小红书/抖音/各种播放器/IDE/游戏 这种混合场景的通用方案，基本都要落到：

能启动应用（跨平台容易）  
能做 UI 自动化（找控件/点按钮/输入/快捷键）（跨平台但脆弱）  
能优先走官方 API 的就别点 UI（稳定、合规、对 IM/社媒尤其重要）  
用 WebSocket 做指令通道（OK）

下面按“可行性/推荐技术/落地架构”给你一个清晰方案。

1) 这些应用，分别该怎么“控”
A. 飞书、企业微信（强烈建议：优先用官方能力）
你要的“发消息/填表/导出”等，最稳的是走官方开放能力，而不是去点 UI：

飞书：机器人、开放平台 API（消息、表格、多维表格、文档等）
企业微信：应用消息、机器人、通讯录/审批等 API

UI 自动化只做兜底（例如：某个内部系统没有 API，只能点）。
结论：IM 类“发消息”优先 API；UI 自动化做补洞。
B. 微信、QQ、微博、小红书、抖音（桌面端“可靠自动化”普遍很难）

这些要么没有官方桌面自动化 API，要么限制很强；用 UI 自动化可以“点得动”，但：
UI 结构改版就挂
登录/验证码/风控会打断
有合规与封号风险（尤其社媒/短视频）



可行的“工程化策略”通常是：

能用 Web 版就用 Web 版 + 浏览器自动化（Playwright/Selenium）  
需要客户端的，只能 UIA/Accessibility + 模板化流程，并接受不稳定

C. 音乐/视频播放器（两条路）

通用控制（播放/暂停/上一首/下一首/音量）：  
macOS/Windows 都可以走 媒体键/系统媒体会话（更通用、更稳）


精细控制（选歌、搜索、指定播放列表）：  
Spotify：有比较成熟的 Web API（前提是账号/授权）
Apple Music 在 macOS 上有一定脚本能力（但跨 Windows 不成立）
国内播放器多数还是要 UI 自动化兜底



D. VSCode / Cursor 这类 IDE（强烈建议：走“编辑器自身的自动化入口”）
不要用 UI 自动化去点菜单写代码（能做但很蠢也很脆）：

VSCode：命令行 code、--install-extension、workspace、tasks、launch；以及 Extension API
Cursor：如果兼容 VSCode 生态，可同样考虑命令/扩展/内部协议；否则退回到“打开文件 + 键盘操作”的下策
文件层面的新建/删除/保存：直接用系统文件 API 做（Python 即可），比点 UI 稳定一万倍

E. 游戏
游戏的“自动化控制”通常只剩：

启动（Steam URI、可执行文件、启动器参数）
输入模拟（键鼠）——但很多游戏有反作弊/焦点/全屏限制，稳定性差且风险高


2) 结论：你不该纠结 osascript vs ScriptingBridge
因为你目标是跨 Windows/macOS、还要覆盖大量“非脚本化应用”，核心能力必须是：

跨平台 UI 自动化（Windows UIA / macOS Accessibility）
对能用官方 API 的应用走 API
对 IDE 走 CLI/扩展
osascript/ScriptingBridge 只作为 macOS 上“锦上添花”的插件能力（例如控制 Finder、Apple Music、部分脚本化应用）


3) 推荐你的实现方案（WebSocket + 插件化 Agent）
Agent 进程（跨平台）

语言建议：Python
通信：websockets（或 FastAPI + WebSocket）
结构：动作统一抽象 + OS/应用插件

动作层（通用）建议先做这些 MVP：

launch_app(app_id)（启动/激活）
type(text)、hotkey(keys)（键盘输入/快捷键）
click(selector)、find(selector)（UI 元素定位与点击）
wait(condition)、sleep(ms)
screenshot()（用于调试与“人看得懂”的回放/定位）
read_ui_tree()（可选：导出可访问性树用于调试/生成 selector）

Windows 自动化后端

首选：pywinauto（UIA backend）做找控件/点/输入
兜底：纯键鼠模拟（pynput 等）
注意：必须运行在“用户桌面会话”里；用 Windows Service 直接操控桌面通常会踩坑

macOS 自动化后端

UI 自动化：Accessibility（AX API，通过 PyObjC 调用）
兜底：AppleScript（osascript）用于“脚本化应用”的快速集成
权限：需要“辅助功能”“自动化”等授权（你的 Agent 要引导用户去开）


4) Selector 怎么设计（不然 UI 自动化会很痛苦）
UI 自动化成败在 selector。建议你支持三种定位策略（越往后越不稳）：

可访问性唯一标识：role + identifier + hierarchy path（最稳）
文本/标签匹配：button name contains “发送”
坐标点击：x/y（最脆，最后兜底）

并且做两件工程化的事：

录制器：用户点一下控件，你抓取该元素的可访问性信息生成 selector
回放时自愈：找不到就降级（identifier 找不到→文本找→最后坐标）


5) 针对你列的动作，给你“正确实现方式”映射

打开应用：跨平台直接 subprocess / OS API（可）
导出：能用应用 API/CLI 优先；否则 UI 自动化流程化
发消息：
飞书/企微：API（强烈推荐）
微信/QQ/社媒：尽量 Web 版 + Playwright；否则 UI 自动化（不稳定且风险）


填表：如果是网页表单 → Playwright；如果是原生客户端 → UI 自动化
点击按钮：UI 自动化
播放音乐：先做系统媒体控制；精细选曲再做各播放器插件
编写代码/新建删除保存运行：
文件操作：直接文件 API
运行：调用任务系统/CLI（如 code/npm test/python -m ...）
IDE 内操作：优先命令/扩展，其次快捷键，最后 UI 点击




6) 我还需要你做一个取舍（决定项目能不能“做得稳”）
请你选一个你们真正要落地的 优先级 Top 5 场景（写成“应用 + 动作 + 成功标准”），例如：

飞书：发送消息给指定群或者个人，导出文档，必须能稳定发送成功  （可接受偶尔 UI 变化要维护） 
VSCode：创建项目/写入文件/运行单测（必须稳定）  
网易云/酷狗/苹果音乐：播放某歌单（必须稳定）  
企业微信：发送消息给指定群或者个人，导出某报表，必须能稳定发送成功  （可接受偶尔 UI 变化要维护）  
微信：给某联系人发消息，必须能稳定发送成功  （可接受偶尔 UI 变化要维护）  

只要你给出 Top 5 + 你能接受的稳定性/维护成本（例如“每月可维护一次 UI 变化”），我就能进一步帮你定：

MVP 的插件列表
每个场景走 API/Playwright/UIA/AX/osascript 的具体选型
WebSocket 指令协议草案与返回码（含异步任务/截图回传/日志）

！！！然后我接下来又做了一些调研，得到了一个没有结合本地项目之后的分析得到的方案：
以下是一份可直接放进你们本地 IDE（VSCode/Cursor）里“内嵌开发 Agent”的 Prompt 指导文档。目标是让 Agent 按统一架构开发一个跨 Windows/macOS 的本地自动化 Agent：通过 WebSocket 接收指令，稳定地完成 飞书机器人发消息/导出飞书云文档、企业微信机器人发消息/导出 iWiki 文档、以及 VSCode 项目/文件/单测自动化（必须稳定）。

本地 IDE 内嵌 Agent 开发 Prompt 指导文档
0. 你的角色
你是一个“资深跨平台自动化与后端工程师”，在本地 IDE 中协助我完成编码实现。你需要：

先澄清需求与边界，再输出可执行的任务拆分与代码修改计划；
以“最小可行 MVP → 可扩展插件化”的方式实现；
以稳定为第一优先级：飞书/企微全部走官方 API（机器人身份），避免 UI 自动化；
用 WebSocket 作为指令通道，支持任务状态回传、幂等、重试、审计日志。


1. 项目目标（必须实现）
1.1 飞书（机器人身份，必须稳定）

发送消息给指定群或个人（至少支持 text；可扩展 rich）  
导出飞书云文档（按 doc URL 或 token 导出为 pdf/docx 等）要求：调用返回要可验证，提供 message_id 或导出文件的下载信息作为证据。

1.2 企业微信（机器人身份，必须稳定）

发送消息给指定群或个人（群机器人 webhook）或（如需求）应用消息  
导出企业微信里的 iWiki 文档（先调研是否存在官方接口；若无则给出可行替代方案并实现一个“适配器接口”，先留空或仅实现可确认的部分）
要求：同样要可验证并产出证据（响应码、media_id、文件 hash/大小等）。

1.3 VSCode/开发自动化（必须稳定）
实现本地开发辅助能力（不靠点 UI）：

创建项目目录结构、写入文件、修改文件、删除文件
运行单测（pytest/jest/go test 等可配置）
返回：退出码、标准输出/错误输出（截断与保存）、测试报告路径（如有）

1.4 通信方式

WebSocket server 运行在本机（默认 127.0.0.1:<port>，端口可配置）
支持指令请求/响应 + 异步任务状态推送
支持鉴权 token（最少 shared secret）


2. 非目标（本阶段不要做）

不做微信桌面端 UI 自动化（不稳定、合规风险高）
不做网易云/酷狗播放歌单（若要做也应后续作为独立插件）
不做“远程公网控制端”的完整平台（只要本地 Agent + WebSocket 即可）
不做复杂的权限自助引导 UI（只输出日志与错误提示）


3. 技术栈与工程约束（强约束）
3.1 语言与框架（建议默认）

Python 3.11+
WebSocket：websockets（或 fastapi + websocket，二选一，优先轻量）
HTTP 客户端：httpx
配置：pydantic + .env（或 python-dotenv）
日志：标准 logging + JSON 日志格式（便于审计）

3.2 项目结构（建议）
  
  
  
    
      
         渲染失败，请重新生成 
      
        
          
        
      
    
  
  
  agent/
  main.py
  config.py
  ws_server.py
  protocol.py
  tasks.py
  auth.py
  logging_setup.py

  connectors/
    feishu.py
    wecom.py
    iwiki.py   # 若无法确认API，先做接口与占位实现

  actions/
    base.py
    feishu_actions.py
    wecom_actions.py
    vscode_actions.py

  utils/
    files.py
    subprocess_run.py
    retry.py
    redact.py

tests/
README.md

4. 指令协议（必须按此实现，便于扩展）
采用“任务式”协议：提交任务后返回 task_id，并通过 WebSocket 推送状态。
4.1 Client → Server：提交任务
  
  
  
    
      
         渲染失败，请重新生成 
      
        
          
        
      
    
  
  
  {
  "type": "task.submit",
  "idempotency_key": "string-optional",
  "auth": {"token": "YOUR_TOKEN"},
  "task": {
    "action": "feishu.send_message",
    "params": {}
  }
}
4.2 Server → Client：立即响应（已接收）
  
  
  
    
      
         渲染失败，请重新生成 
      
        
          
        
      
    
  
  
  {
  "type": "task.accepted",
  "task_id": "uuid",
  "queued_at": 1730000000
}
4.3 Server → Client：任务状态推送
  
  
  
    
      
         渲染失败，请重新生成 
      
        
          
        
      
    
  
  
  {
  "type": "task.status",
  "task_id": "uuid",
  "state": "running|success|failed",
  "progress": 0.0,
  "message": "human readable",
  "result": {},
  "error": {"code": "STRING", "detail": "STRING"}
}
4.4 幂等与重试（必须）

若提交时带 idempotency_key，相同 key 在 TTL 内应返回同一 task_id 或同一结果（至少避免重复发送消息）
内部对网络类调用要带重试（指数退避，最大次数可配置）


5. 必须实现的 Actions 规范（定义清晰输入输出）
5.1 feishu.send_message
params

receive_type: "open_id" | "chat_id" | "user_id" | "email"（按你们选型）
receive_id: string
msg_type: "text"（MVP）
content: { "text": "..." }

result

message_id: string
raw_response: object（可选，注意脱敏）

5.2 feishu.export_doc
params（建议）

doc_token 或 url（若传 url，需解析 token）
format: "pdf" | "docx"（按飞书能力）
output_path: string（本地保存路径，可选；不传则保存到默认 artifacts 目录）

result

file_path: string
sha256: string
size_bytes: number

5.3 wecom.send_message
如果是群机器人 webhook：

webhook_url: string（建议支持从配置里按 name 映射）
msg_type: "text"
content: { "text": "..." }

result

errcode: number
errmsg: string

5.4 iwiki.export_doc（先做接口，能实现就实现）
要求你先做“能力探测/调研输出”：

先搜索/阅读企业微信 iWiki 是否有公开导出接口、是否可通过网页后端接口实现（需要 cookie/token）
如果无稳定 API：必须输出清晰结论，并将该 action 标记为 not_supported 或 experimental，同时预留“后续用 Playwright Web 端导出”的接口定义，但本期不实现 Playwright（除非我明确要求）

5.5 vscode.create_project / vscode.write_file / vscode.run_tests
全部走文件与子进程：

write_file: path + content + mode: overwrite|append
run_tests: cmd（数组形式）+ cwd + env + timeout_sec
result 必须包含：
exit_code
stdout_tail / stderr_tail
duration_ms


6. 安全与合规（必须做的最小集合）

所有外部 token、webhook、app_secret 只从环境变量/配置读，不写入代码
日志脱敏：token、webhook query、cookie 等必须打码（例如只保留前 4 后 4）
限制 action 白名单：只允许协议里定义的 action；禁止任意代码执行指令
记录审计日志：谁（token 标识）、什么时候、调用了什么 action、是否成功


7. 开发流程要求（你必须按这个节奏输出与执行）
你每次协助开发时必须遵循：

输出计划：列出要改的文件、要新增的类/函数、协议字段、测试点  
最小提交：先实现可跑通的骨架（ws server + task runner + 一个 action）  
逐个 action 完成：每完成一个 action，给出：
如何配置环境变量
如何用 WebSocket 发起示例请求
预期返回示例
单元测试/集成测试建议


错误处理与可观测性：每个 connector 的异常要转成统一错误码


8. 代码质量标准（必须）

类型标注（尽量）
重要函数要有 docstring，包含参数与返回结构
单元测试：至少覆盖 protocol 解析、幂等逻辑、一个 connector 的成功/失败分支（可用 httpx mock）
不引入重型依赖（Playwright、本机 UI 自动化库）到 MVP


9. 你需要先向我确认的信息（只问一次，集中提问）
在开始编码前，你需要一次性向我确认以下配置与选型（若我没给，就给默认）：

飞书：用“机器人 webhook”还是“应用 API（tenant access token）”？（默认建议：应用 API，更强）  
企业微信：仅群机器人 webhook？是否需要发个人消息？  
iWiki：是否能提供一个示例文档链接、导出格式期望、以及当前登录态/鉴权方式（如有）？  
VSCode/单测：你们主要语言栈（Python/Node/Go/Java）与默认测试命令？  
WebSocket：端口、token、是否需要 TLS（默认不需要，仅本地回环地址）


10. 交付物清单（你必须产出）

可运行的本地 Agent
README.md：配置、启动、WebSocket 调用示例、action 列表
最少 1 组 tests
一个 examples/：提供 ws client 示例（Python 脚本即可）


附：内嵌 Agent 开始工作的“第一条消息模板”
把下面这段作为你（开发 Agent）进入项目后的第一条输出模板：

我将按 MVP 顺序实现：WS Server → Task Runner → Feishu send → WeCom send → VSCode actions → Feishu export → iWiki export（调研/占位）。  
我将创建/修改文件：...（列清单）  
我需要你确认：飞书鉴权方式、企微发送范围、iWiki 样例链接、默认单测命令、WS 端口与 token。  
之后我会先提交一个可跑通的 feishu.send_message 端到端示例。

