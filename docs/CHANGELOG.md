## 2026-04-02（企微窗口绑定修复：避免多窗口场景下验证看错窗口）

### 🔧 问题修复
- 修复企业微信 `wecom_ui` 在多窗口场景下，主流程截图/标题 OCR 验证可能反复抓到错误企微窗口（如图片窗口、其他企微主窗），导致“明明已经进入目标联系人会话却仍被判定失败”的问题。
- `wecom_ui` 在窗口归一化后会立即按目标 `bounds` 绑定同一个主窗口引用；后续聊天列表探测、全局搜索后的标题校验、发送前焦点确认都统一基于该 `windowId` 截图，不再每一步按 owner 名重新猜窗口。
- 全局搜索弹窗截取新增排除已绑定主窗口 `windowId` 的能力，并过滤掉与主窗口近似同尺寸的候选，避免把主窗口自己误识别成“搜索弹窗”。
- 保持既有交互策略不变：继续保留点击“联系人”tab、搜索结果缺失时清空并重输联系人名刷新、以及最终聊天标题 OCR 安全护栏。

### 🧪 测试
- `cd /Users/westar/Desktop/code/voice_assistant && source backend_py/.venv/bin/activate && python -m pytest test_scripts/test_macos_ui_automation_popup_pick.py test_scripts/test_wecom_ui_controller_pick_and_noise.py -q`
- 结果：`7 passed`

### 📝 修改的文件
| 文件 | 修改内容 |
|------|----------|
| `backend_py/services/macos_ui_automation.py` | 新增按期望 bounds 绑定窗口、按 `windowId` 截图能力；弹窗选择支持排除已绑定主窗口 |
| `backend_py/services/wecom_ui_controller.py` | 企微发送流程改为绑定主窗口后再做截图/OCR/验证，保留联系人 tab 与刷新逻辑 |
| `test_scripts/test_macos_ui_automation_popup_pick.py` | 新增“排除已绑定主窗口 ID”回归用例 |
| `docs/CHANGELOG.md` | 记录本次修复 |

---

## 2026-04-01（企微全局搜索更稳：联系人 tab + Enter 选第一条 + 保留刷新）

### 🔧 问题修复
- 修复企业微信 `wecom_ui` 全局搜索弹窗在“结果列表点击”场景下，可能因水印/详情文本框干扰导致点击落空、无法进入目标会话的问题。
- 新策略：输入联系人名后尽力切到“联系人”tab，并使用 `Enter` 选择第一条最匹配结果进入会话；当搜索结果偶发不渲染时，保留“清空并重输”刷新一次的逻辑。
- 安全护栏不变：仍以聊天标题区 OCR 命中目标联系人为最终判定，否则中止发送以避免误发。

### 📝 修改的文件
| 文件 | 修改内容 |
|------|----------|
| `backend_py/services/wecom_ui_controller.py` | 全局搜索：改为“联系人 tab + Enter 选第一条”，并保留清空重输刷新 |
| `docs/CHANGELOG.md` | 记录本次修复 |

---

## 2026-03-27（修复语音 E2E 卡住超时：Socket.IO 包体上限 + 脚本 fail-fast）

### 🔧 问题修复
- 修复语音 E2E 发送较大音频（尤其 `m4a → wav` 膨胀）可能触发 Socket.IO 默认包体上限导致服务端断开、脚本“假卡死直到超时”的问题。
- E2E/benchmark 脚本在 `disconnect`/后端 `error` 时会立刻退出，并仍然落盘 `voice_flow_timeline.json` / `voice_perf_benchmark.json` 作为证据链。

### ✨ 新功能
- 在真实工作流中落盘性能复盘：每次请求会生成 `ui_debug/<requestId>/perf_summary.json`，记录 ASR/LLM/工具/TTS 的关键耗时与少量元信息（不落盘敏感明文）。

### 🔧 兼容性增强
- 后端 Socket.IO 提升 `max_http_buffer_size` 到 16MB，避免语音 bytes 上送被误伤。
- E2E/benchmark 默认不再强制将 `m4a/mp4` 转 `wav`（可用参数显式开启转码），降低体积膨胀与触发上限/ASR 大小限制的概率。

### 📝 修改的文件
| 文件 | 修改内容 |
|------|----------|
| `backend_py/main.py` | Socket.IO：设置 `max_http_buffer_size=16MB` |
| `backend_py/services/perf_recorder.py` | 新增：请求级性能记录器，固定落盘 `perf_summary.json` |
| `backend_py/controllers/conversation_controller.py` | 集成 ASR/LLM/工具/TTS 性能打点与落盘 |
| `test_scripts/test_e2e_socketio_voice_flow.py` | E2E：fail-fast 与音频转码策略优化 |
| `test_scripts/test_voice_perf_benchmark.py` | benchmark：转码策略与失败落盘增强 |
| `docs/CHANGELOG.md` | 记录本次修复 |

---

## 2026-03-25（语音 E2E + m4a 输入 + DashScope/Ollama 对照基准）

### ✨ 新功能
- 新增语音端到端 E2E 脚本：支持本地音频文件（含 `m4a`）通过 Socket.IO 走 `voice-input` → ASR → LLM →（可选工具）→ TTS，并落盘事件时间线 JSON。
- 新增性能基准脚本：输出 ASR/LLM/TTS 的延迟统计，并支持 DashScope 与 Ollama 的 LLM 对照实验。

### 🔧 兼容性增强
- `ASRService` 增强音频头识别：支持 `m4a/mp4` 的 MIME 推断与 `input_audio.format` 映射。

### 📝 修改的文件
| 文件 | 修改内容 |
|------|----------|
| `backend_py/services/asr_service.py` | 补齐 `m4a/mp4` MIME 探测与 format 映射 |
| `test_scripts/test_e2e_socketio_voice_flow.py` | 新增：语音 E2E（本地音频→Socket.IO→全链路） |
| `test_scripts/test_voice_perf_benchmark.py` | 新增：ASR/LLM/TTS 性能基准与 provider 对照 |
| `temp_md/2026-03-25_voice_e2e_and_benchmark.md` | 新增：测试与论文数据产出说明 |
| `docs/CHANGELOG.md` | 记录本次变更 |

---

## 2026-03-13（Ollama 适配增强 — provider 分层 / 原生 /api/chat / tool-loop 回填兼容）

### 🔧 问题修复

- **修复 `LLM_PROVIDER=ollama` 时“配置层已切换但协议层仍沿用 OpenAI 兼容 `/chat/completions`”导致的链路不一致问题**：
  - **根本原因**：`backend_py/services/llm_service.py` 虽然已支持 `dashscope/ollama` provider 切换，但实际请求仍统一走 OpenAI 兼容 `/chat/completions`；而项目内 `backend_py/services/ollama_client.py` 的原生 `/api/chat` 能力并未接入主会话链路。
  - **修复**：
    - `LLMService.invoke_llm()` 拆分为 DashScope 与 Ollama 两条 provider 专用适配路径。
    - DashScope 继续保留现有 `/chat/completions` 行为；Ollama 改为走原生 `/api/chat`。
    - Ollama 路径新增 `output_mode=json/schema_json` 与 `format` 透传能力，为 structured outputs 预留统一入口。
- **修复 Ollama tool calling 返回结构与现有控制器内部协议不一致的问题**：
  - **根本原因**：Ollama 常将 `message.tool_calls[].function.arguments` 直接返回为对象，而现有 `ConversationController/ToolRouter` 主要按 JSON 字符串读取参数。
  - **修复**：
    - `backend_py/services/ollama_client.py` 新增 tool_calls 规范化逻辑，统一转换为项目内部使用的 `function.arguments=<json string>` 结构。
    - 若模型未返回 tool call id，则由本地生成稳定 id，避免后续 tool-loop / 调试链路缺失标识。
- **修复 Ollama tool-loop 工具结果回填协议与官方文档不一致的问题**：
  - **根本原因**：`backend_py/controllers/conversation_controller.py` 之前统一将工具结果回填为 `role=tool + tool_call_id`，这更接近 OpenAI 兼容思路；而 Ollama 官方文档推荐 `role=tool + tool_name + content`。
  - **修复**：
    - 新增 provider 感知的 tool-loop 消息构造器。
    - assistant 的 `tool_calls` 回填保持统一；tool 结果回填在 Ollama provider 下改为 `tool_name` 形式，在 DashScope 下继续使用原 `tool_call_id` 形式。
- **修复 `OLLAMA_BASE_URL` 带 `/v1` 时调用原生 `/api/chat` 可能拼错地址的问题**：
  - **修复**：`OllamaClient` 新增 base URL 归一化逻辑，兼容 `http://host:11434`、`/v1`、`/api`、`/api/chat` 多种输入形态。
- **优化长期记忆偏好抽取在 Ollama 下的结构化输出稳定性**：
  - **修复**：偏好抽取调用在 Ollama provider 下显式使用 `output_mode="json"`，增强 JSON 返回一致性，同时不改动现有 `parse_and_save_profile()` 的解析入口。

### 🧪 测试

- 新增：
  - `test_scripts/test_ollama_provider_adaptation.py`
- 更新：
  - `test_scripts/test_ollama_chat_smoke.py`（补充 normalizedBaseUrl 与 toolCalls 输出）
- 已执行：
  - `cd backend_py && source .venv/bin/activate && python -m pytest ../test_scripts/test_ollama_provider_adaptation.py ../test_scripts/test_device_location_tool_loop_regression.py -q`
  - 结果：`7 passed`

## 2026-03-04（根因修复 — 设备定位开关不生效 / tool-loop 读错会话 / 热键链路一致性）

### 🔧 问题修复

- **修复设备定位开关“已开启但实际执行层仍判定未开启”的根因问题**：
  - **根因 1（会话迁移丢失会话态）**：启用 `register-client` 绑定 `sid → clientId` 后，`SessionStore.migrate(from_id=sid, to_id=clientId)` 在 `to_id` 已存在时历史逻辑会丢弃 `sid` 会话，导致刚同步到 `sid` 的 `device_location_enabled/network_access_enabled/tts_settings` 等被丢弃（典型竞态：update 先到 sid、register-client 后到触发 migrate）。
  - **根因 2（tool-loop 读错会话）**：`handle_text_command()` 已解析 `session_id=clientId` 并用于 capability，但调用 `_maybe_run_network_tool_loop()` 仍传入原始 `sid`，导致 tool-loop 内 `session_store.get_or_create(sid)` 创建了默认关闭的新会话，从而出现“capability 显示 enabled=true，但 local_tool_request 里 enabled=false”的矛盾。
  - **修复**：
    - `SessionStore.migrate()` 改为“目标会话已存在时合并会话态”而非直接丢弃，并引入布尔开关的“显式设置时间戳”合并策略（开启/关闭都能正确传播）。
    - tool-loop 统一使用解析后的 `session_id` 读取会话态（不再使用 raw `sid`）。
    - 天气工具 `location=""` 等占位符增强：可触发一次自动设备定位；在缺少定位时不再抛未捕获异常，改为返回工具失败结构，避免 `asyncio.Task exception was never retrieved`。
  - **回归测试**：新增回归用例覆盖会话迁移合并与 tool-loop session_id 读取、`location=""` 占位符场景等。

### 🧪 测试

- 新增：
  - `test_scripts/test_session_store_migrate_merge_settings.py`
  - `test_scripts/test_device_location_tool_loop_regression.py`

## 2026-03-02（新功能迭代 — LLM 配置化 / 长期记忆 / 热键唤醒 / ESP32 对接）

### 🔧 问题修复
- **修复热键 `<cmd>+<shift>+s` 停止录音与系统"另存为"快捷键冲突**：
  - **根本原因**：macOS 上 pynput Listener 无法拦截按键事件传递给前台应用，`Cmd+Shift+S` 同时被前台应用识别为"另存为"
  - **修复**：取消独立的停止录音热键，改为 toggle 模式——同一个热键 `Cmd+Shift+Space` 按第一次开始录音、按第二次停止录音
- **修复热键录音链路与 Web 端会话不一致（权限/配置/音色/TTS 推送丢失）**：
  - **根本原因**：热键链路使用虚拟 sid（例如 `__hotkey__`），与真实 Socket.IO sid 不同，导致会话态（`network_access_enabled`/`device_location_enabled`/`tts_settings` 等）不共享；同时 `sio.emit(..., to=sid)` 也无法推送到任何真实客户端，表现为无法播放指定音色的 TTS
  - **修复**：新增 `register-client` 机制，将 socket sid 绑定到稳定 `clientId` 并加入 room（`client:<clientId>`）；热键请求优先绑定到 `client:<HOTKEY_CLIENT_ID>`（默认 `desktop`），若该 clientId 当前不在线则自动 fallback 到最近在线的 clientId（例如 `web_...`），确保热键请求必能回传并复用当前客户端配置
- **修复本机 Electron/前端连接被 Socket.IO 连接限流误伤，导致 room 无成员、TTS 听不到**：
  - **根本原因**：本机环境下 Socket.IO 可能在握手/升级/重连阶段产生短时间多次连接，原限流策略会拒绝这些连接，导致 `register-client` 无法稳定完成、`client:<clientId>` room 为空
  - **修复**：后端对 localhost 连接跳过限流；前端强制使用 websocket 传输，减少多次握手连接
- **修复天气兜底 web_search 调用时 `NameError: name '_exec_one' is not defined`**：
  - **根本原因**：`_maybe_run_network_tool_loop` 方法内第 1221 行调用了 `_exec_one(fallback_call)`，但该内部函数从未定义；方法内只有 `_exec_device_location` 和 `_exec_network` 两个内部函数，`_exec_one` 是历史重构残留
  - **修复**：将 `_exec_one(fallback_call)` 替换为 `_exec_network(fallback_call)`
- **修复天气兜底 web_search 后未生成最终答案（只说“我马上去搜”）**：
  - **根本原因**：兜底阶段再次调用 LLM 时仍允许 tools，模型可能再次生成 `web_search` 意图而非总结工具结果
  - **修复**：在兜底 web_search 工具结果回填后注入 system hint，要求直接总结并将 `INTENT_JSON.actions` 置空；同时该总结阶段显式禁用 tools，保证输出为最终回答
- **修复 conversation_controller.py 和 memory_service.py 中中文引号被替换为 ASCII 双引号导致的 SyntaxError**：
  - 5 处 f-string/字符串中的 `\u201c`/`\u201d` 被损坏为 ASCII `"`，导致字符串定界符冲突
  - 受影响行：conversation_controller.py 第 1434/1480/1495/1518/1709 行，memory_service.py 第 27/37 行
- **修复 pynput 热键格式错误**：默认热键 `<cmd>+<shift>+space` 改为 `<cmd>+<shift>+<space>`（`space` 需要用 `<>` 包裹）
- **修复 pynput 1.8.x GlobalHotKeys 在 macOS 上崩溃导致热键永久失效**：
  - **根本原因**：pynput 1.8.1 的 `_darwin.py` 第 313 行（`Listener._handle_message` 的 `NSSystemDefined` 媒体键分支）调用 `self.on_press(self._SPECIAL_KEYS[key])` 时缺少 `injected` 参数，而 `GlobalHotKeys._on_press(self, key, injected)` 需要 2 个位置参数，导致 `TypeError`。崩溃后 pynput 内部异常处理器将监听线程标记为失败，后续所有热键均无响应。
  - **修复**：不使用有 bug 的 `GlobalHotKeys`，改用底层 `Listener` + `HotKey` 手动组合，`on_press`/`on_release` 回调签名使用 `*args` 兼容 `injected` 参数的有无
- **修复热键 `<cmd>+<shift>+<space>` 按下后无响应**：
  - **根本原因**：Listener 回调收到空格键时传入 `Key.space`（枚举类型），但 `HotKey.parse('<space>')` 产出的是 `KeyCode(vk=49)`（虚拟键码），两者 `__eq__` 返回 `False`，导致 `HotKey.press()` 内部集合匹配永远不成立。自定义 `_canonical` 方法仅处理了 `KeyCode` 带 `char` 的字母键场景，未处理 `Key` 枚举 → `KeyCode` 的转换。
  - **修复**：将 `on_press`/`on_release` 中的按键规范化从自定义 `_canonical` 改为使用 pynput 内置的 `Listener.canonical()` 实例方法，该方法能正确将 `Key.space` → `KeyCode(vk=49)`，同时将左右修饰键统一为通用形式。

### ✨ 新功能

#### 模块3：LLM 提供者配置化
- 支持通过环境变量 `LLM_PROVIDER` 切换 DashScope（远程）和 Ollama（本地）两种 LLM 后端
- Ollama 使用 OpenAI 兼容 API（`http://localhost:11434/v1`），默认模型 `qwen3.5`
- 新增配置项：`LLM_PROVIDER`、`OLLAMA_BASE_URL`、`OLLAMA_MODEL`

#### 模块4：本地 LLM 长期记忆系统
- 新增 `MemoryService`：管理 user_profile（JSON 偏好）+ rolling_summary（滚动摘要）
- 每次 LLM 调用自动注入记忆上下文到 system prompt
- 每轮对话后异步更新滚动摘要（通过 LLM 生成）
- 检测到偏好触发词（"以后/记住/我叫/我喜欢..."）时自动抽取并持久化用户偏好
- 存储路径：`~/.voice_assistant/memory/`
- 新增配置项：`MEMORY_ENABLED`、`MEMORY_DIR`

#### 模块1：全局热键唤醒语音接收
- 新增 `HotkeyVoiceService`：通过 pynput 全局热键监听 + sounddevice 麦克风录音
- 默认热键：`Cmd+Shift+Space`（开始录音）、`Cmd+Shift+S`（停止录音）
- 录音完成后自动打包 WAV 并注入 `handle_voice_input` 链路
- 应用启动时自动初始化（可通过 `HOTKEY_ENABLED=0` 禁用）
- 新增配置项：`HOTKEY_TRIGGER`、`HOTKEY_STOP`、`HOTKEY_ENABLED`

#### 模块2：ESP32 WiFi WebSocket 对接
- 新增 `HardwareVoiceService`：管理 ESP32 硬件模块的 WebSocket 连接和双向音频传输
- WebSocket 端点：`/ws/hardware`
- 支持 ESP32 发送 PCM 音频帧 + JSON 控制帧（start_record/stop_record/text_command/ping）
- 支持向 ESP32 下发 TTS 音频 + JSON 状态帧
- 硬件方案：ESP32-S3-N16R8 + INMP441 麦克风 + MAX98357A 功放
- 新增配置项：`HARDWARE_WS_ENABLED`

### 📝 修改的文件
| 文件 | 修改内容 |
|------|----------|
| `backend_py/config.py` | 新增 LLM_PROVIDER/Ollama/热键/硬件 WS/记忆 相关配置项 |
| `backend_py/services/llm_service.py` | 支持 DashScope/Ollama 双后端切换；invoke_llm 新增 memory_context 参数 |
| `backend_py/services/memory_service.py` | **新增**：MemoryService（偏好/摘要管理、记忆上下文构建、偏好抽取触发） |
| `backend_py/services/hotkey_voice_service.py` | **新增**：HotkeyVoiceService（pynput 热键 + sounddevice 录音） |
| `backend_py/services/hardware_voice_service.py` | **新增**：HardwareVoiceService（ESP32 WebSocket 双向通信） |
| `backend_py/controllers/conversation_controller.py` | 集成 MemoryService：初始化、注入记忆上下文、异步摘要更新、偏好抽取 |
| `backend_py/main.py` | 启动热键服务、注册 /ws/hardware 端点、capabilities 增加新能力字段 |
| `backend_py/requirements.txt` | 新增 pynput、sounddevice、numpy 依赖 |
| `hardware_recommendations.txt` | **新增**：ESP32-S3 硬件推荐方案及接线参考 |
| `docs/CHANGELOG.md` | 记录本次变更 |

---

## 2026-03-01（第三批修复 — 企微端到端测试两个根因修复）

### 🔧 问题修复
- **修复企微聊天列表滚动导致直达匹配失败**：
  - **根因**：代码先执行 `Cmd+1` 切换到消息 tab（已完成切换），随后又 OCR 找到"消息"按钮并点击，这个冗余点击触发了聊天列表刷新/滚动，导致目标联系人从可视范围消失。
  - **证据**：`wecom_after_go_messages` 与 `wecom_flow_init` PNG 大小完全相同（1374319 bytes），证明 `Cmd+1` 未导致滚动；`wecom_chat_list_probe`（点击"消息"后）PNG 大小变为 1335479，罗晨曦消失。
  - **修复**：删除冗余的 OCR+点击"消息"按钮逻辑，仅保留 `Cmd+1` 快捷键 + 增加等待时间（0.15s→0.35s）。
- **修复企微全局搜索弹窗流程完全失效（截图+OCR+Escape 三处根因）**：
  - **根因1**：`screenshot_window` 的 `_find_best_window_sync` 按窗口面积排序取最大值，全局搜索弹窗面积远小于主窗口，因此始终截取主窗口（windowId=29725），所有后续 OCR 都在主窗口上执行，找不到弹窗中的搜索结果和 tabs。
  - **根因2**：`hotkey("escape")` 使用 AppleScript `keystroke "escape"` 输入的是字符串 "escape" 文本，而非发送 Escape 键码（应使用 `key code 53`）。
  - **证据**：12张截图 windowId 全是 29725；`searchTabsPreview` 识别到聊天列表内容而非弹窗 tabs；用户亲眼看到搜索框出现 "escape" 文字。
  - **修复**：放弃"截图+OCR 在弹窗中操作"策略，改为纯键盘导航：`Shift+Cmd+F` 打开弹窗 → 输入联系人名 → `Return`（key_code 36）选中第一个结果 → `key_code(53)` 关闭弹窗 → 截图验证主窗口标题区是否切换成功。所有 `hotkey("escape")` 替换为 `key_code(53)`。

### 📝 修改的文件
| 文件 | 修改内容 |
|------|----------|
| `backend_py/services/wecom_ui_controller.py` | 删除冗余"消息"按钮点击；全局搜索弹窗改为纯键盘导航；`hotkey("escape")`→`key_code(53)` |
| `docs/CHANGELOG.md` | 记录本次变更 |

## 2026-03-01（第二批修复 — 端到端测试失败）

### 🔧 问题修复
- **修复抖音视频 tab 切换后点击到"相关搜索"而非视频内容**：
  - **根因**：视频 tab 切换后仅等 0.5s，视频封面/标题尚未加载（全是占位图），但右侧"相关搜索"已完全加载。`results_content` ROI 宽度 0.96 覆盖了右侧"相关搜索"区域，OCR 将右侧文本当作有效候选，轮询条件立即满足并退出。
  - **修复**：
    1. `results_content` ROI 宽度从 0.96 收窄到 0.62，排除 x>0.64 的右侧"相关搜索"区域
    2. 视频 tab 切换后最小等待从 0.5s 增加到 1.5s
    3. 轮询退出条件增加 `content_poll_elapsed >= 2.0` 最低等待保护
- **修复企业微信备用搜索流程执行异常（发消息失败）**：
  - **根因**：备用搜索流程使用 `Cmd+F` 后直接在搜索框内输入联系人并回车，走的是内联搜索而非全局搜索弹窗。点击联系人后搜索面板未关闭，`Cmd+A`+`Cmd+X` 剪切的是搜索框内的联系人名字（3字）而非聊天输入框草稿，导致消息发送到错误位置。
  - **修复**：
    1. 备用搜索流程从 `Cmd+F` 改为 `Shift+Cmd+F`（全局搜索弹窗），在弹窗中输入联系人 → 点击"联系人" tab → 点击搜索结果 → Escape 关闭弹窗
    2. 发送消息前新增焦点保障：先点击聊天输入区域（x≈0.65, y≈0.85），确保焦点不在搜索框

### 📝 修改的文件
| 文件 | 修改内容 |
|------|----------|
| `backend_py/services/douyin_controller.py` | ROI 宽度 0.96→0.62；最小等待 0.5s→1.5s；轮询退出增加 ≥2.0s 保护 |
| `backend_py/services/wecom_ui_controller.py` | 重写备用搜索流程为 Shift+Cmd+F 全局搜索弹窗；发送前新增点击输入区域确保焦点 |
| `docs/CHANGELOG.md` | 记录本次变更 |

## 2026-03-01

### 🔧 问题修复
- **修复前端无法连接服务器（"正在连接服务器"）**：
  - **根因**：CodeBuddy（IDE）进程占用了 `localhost:3002` 端口，拦截所有 HTTP/WS 请求并返回 `426 Upgrade Required`，导致前端 Socket.IO 永远无法与 Python 后端建立连接。
  - **修复**：将前后端通信端口从 `3002` 统一改为 `3002`（前端 `socket.ts`、Python `config.py`、Node `server.js`、`package.json`、`start.sh`）。
- **修复抖音搜索框 fallback 点击 y 偏移问题**：
  - **根因**：`_fallback_click_point` 中 y 系数为 `0.55`，导致 `y_norm=0.066`（image_y≈106），实际搜索框中心在 `y_norm≈0.03`（image_y≈48），点击落在搜索框下方。
  - **修复**：将 y 系数从 `0.55` 降为 `0.25`，使 `y_norm = 0.00 + 0.12 × 0.25 = 0.03`，精准命中搜索框中心。
  - **验证**：dry-run 确认 fallback 坐标 image_y=48（正确）；真实执行成功进入搜索结果页（综合→视频 tab），选择并播放 "貔柴解说甄嬛传"。
- **修复企业微信发送前标题区 OCR 校验 7 次全部失败的 bug**：
  - **根因**：`chat_header` ROI 默认值 `(0.42, 0.10, 0.56, 0.12)` 偏移到聊天内容区（y=0.10 远低于实际标题位置 y≈0.01~0.03，x=0.42 也偏右），导致 OCR 识别的不是标题文字而是聊天消息内容。
  - **修复**：将 `chat_header` ROI 修正为 `(0.33, 0.01, 0.35, 0.08)`，经离线 OCR 对照实验（13 组 ROI × 多种 scale/accurate 参数组合）验证，该配置在 x=0.30~0.36、y=0.00~0.03 区间内稳定命中标题文字。
  - **验证**：真实发送测试通过（联系人"罗晨曦"，标题校验在 attempt 2 通过）。
- **修复异常路径不落盘 workflow JSON**：将核心逻辑拆分为 `_search_contact_and_send_inner`，外层用 `try/except` 包裹，异常时也调用 `_dump_json_once("wecom_workflow_debug_error")` 确保诊断数据（`chatHeaderPreview`、`chatHeaderPick` 等）不丢失。

### 🧪 测试
- 新增离线 OCR 对照实验脚本：
  - `test_scripts/debug_wecom_header_ocr_roi.py`（v1：大范围 ROI 扫描）
  - `test_scripts/debug_wecom_header_ocr_roi_v2.py`（v2：围绕命中区域微调）
- 测试联系人从"顾老师"切换为"罗晨曦"（更新测试脚本文档中的示例命令）。

### 📝 修改的文件
| 文件 | 修改内容 |
|------|----------|
| `backend_py/services/wecom_ui_controller.py` | 修正 `chat_header` ROI 默认值；拆分 `_search_contact_and_send_inner`，异常路径兜底落盘 JSON |
| `backend_py/services/douyin_controller.py` | 修复 `_fallback_click_point` y 系数 0.55→0.25，修正搜索框点击偏移 |
| `frontend/src/utils/socket.ts` | Socket.IO 连接端口 3002→3002 |
| `backend_py/config.py` | 默认端口 3002→3002 |
| `backend/server.js` | 默认端口 3002→3002 |
| `package.json` | `dev:backend_py` 脚本端口 3002→3002 |
| `start.sh` | 端口提示与 .env 模板 3002→3002 |
| `test_scripts/debug_wecom_search_send_flow.py` | 文档示例联系人从"顾老师"改为"罗晨曦" |
| `test_scripts/debug_wecom_header_ocr_roi.py` | 新增：离线 OCR 对照实验 v1 |
| `test_scripts/debug_wecom_header_ocr_roi_v2.py` | 新增：离线 OCR 对照实验 v2 |
| `docs/CHANGELOG.md` | 记录本次变更 |

## 2026-02-23

### ✨ 功能增强
- 新增 UI 自动化工具：`douyin_ui`（抖音搜索并播放最匹配视频）与 `wecom_ui`（企业微信搜索联系人并发送消息），采用 OCR-first（窗口级截图 + ROI OCR + 坐标映射点击）方案，调试产物按 `VOICE_ASSISTANT_DEBUG_RUN` 落盘到 `ui_debug/<runId>/`。
- 工具链路接入：补齐 `ToolRouter` 路由执行、`SafetyService` 高风险确认与参数校验、`LLMService` tools schema 与提示词约束（含 query/contact/message 生成规则）。

### 🧪 测试
- 新增调试脚本：
  - `test_scripts/debug_douyin_search_play_flow.py`
  - `test_scripts/debug_wecom_search_send_flow.py`

### 📝 修改的文件
| 文件 | 修改内容 |
|------|----------|
| `backend_py/services/tool_router.py` | 注册并路由执行 `douyin_ui/wecom_ui` |
| `backend_py/safety.py` | 两工具高风险确认、参数校验、确认摘要补齐 |
| `backend_py/services/llm_service.py` | tools schema + 提示词约束，stub 增加基础识别 |
| `backend_py/services/douyin_controller.py` | 新增：抖音 OCR-first 工作流控制器 |
| `backend_py/services/wecom_ui_controller.py` | 新增：企业微信 OCR-first 工作流控制器 |
| `backend_py/services/ui_workflow_utils.py` | 新增：ui_debug/ROI/剪贴板/裁剪等通用工具 |
| `test_scripts/debug_douyin_search_play_flow.py` | 新增：抖音工作流可复现脚本 |
| `test_scripts/debug_wecom_search_send_flow.py` | 新增：企业微信工作流可复现脚本 |
| `temp_md/2026-02-23_douyin_wecom_ui_tools.md` | 新增：本次接入记录 |
| `docs/CHANGELOG.md` | 记录本次变更 |

## 2026-02-16

### ✨ 功能增强
- KuGou `music_ui(search)`：VLM 全控模式升级为 **单次推理 `UI_PLAN_JSON`**（包含 state + action + target + bbox_norm），并在本地做硬校验（state→target 约束、bbox 合法性、置信度门槛），必要时触发一次纠错重问。
- VLM 输入图像策略调整为 **WebP only（quality=95）**：窗口截图（PNG）转换为 WebP 后喂给模型，不提供 PNG/JPEG 兜底；失败则 fail-fast 并落盘证据。
- 新增 **KuGou VLM Playbook（持久化规则）**：将搜索播放全流程与遮挡分支固化为可注入的本地规则，减少模型“临时猜测”。

### 🧪 测试
- 新增 `test_scripts/test_ollama_chat_smoke.py`：验证本地 Ollama `/api/chat` 基础连通性。
- 新增 `test_scripts/debug_kugou_vlm_driver_dry_run.py`：KuGou VLM driver dry-run（不点击不键入，仅落盘证据）。

### 📝 修改的文件
| 文件 | 修改内容 |
|------|----------|
| `backend_py/services/vlm_ui_driver.py` | 改为 `UI_PLAN_JSON` 单次推理 + 硬校验纠错；输入图转 WebP(q=95) 后调用 Ollama |
| `backend_py/resources/kugou_vlm_playbook_v1.json` | 新增 KuGou VLM Playbook（state→target 约束） |
| `backend_py/services/music_controller.py` | `search` 动作按 `ocr/vlm` 配置分流 |
| `test_scripts/test_ollama_chat_smoke.py` | 新增 Ollama smoke |
| `test_scripts/debug_kugou_vlm_driver_dry_run.py` | 更新说明：VLM 输入为 WebP(q=95) |
| `temp_md/2026-02-16_kugou_vlm_ui_driver_impl.md` | 更新：记录协议从 `UI_ACTION_JSON` 升级为 `UI_PLAN_JSON` |
| `temp_md/kugou_vlm_playbook_v1.md` | 新增：KuGou VLM Playbook v1（规则文档） |
| `docs/CHANGELOG.md` | 记录本次变更 |

## 2026-02-14

### 🔧 问题修复
- 修复 `get_device_location` 在模型仅通过 `INTENT_JSON.actions` 表达（未返回 `tool_calls`）时，未进入 tool-loop 回填链路而被 `SafetyService` 误拦截为“未知工具类型”的问题。
- 修复本地 CoreLocation 多次调用时，PyObjC 重复注册 delegate 类导致的异常：`_Delegate is overriding existing Objective-C class`。
- 修复 CoreLocation 授权/回调在后台线程执行导致授权状态不刷新、最终超时的问题：设备定位改为主线程执行，并补充授权状态采样信息用于排障。
- 新增 macOS 定位 Helper（Swift `.app` + `Info.plist`）：用于稳定触发系统定位授权弹窗与 TCC 记录，后端优先通过该 Helper 获取经纬度与地址信息。
- 设备定位补齐城市信息：基于 `lon,lat` 调用 QWeather `/geo/v2/city/lookup`，将 `name/adm1/adm2/id` 写回定位结果的 `address`。

### 📝 修改的文件
| 文件 | 修改内容 |
|------|----------|
| `backend_py/controllers/conversation_controller.py` | tool-loop 合成逻辑支持 `get_device_location`；补齐经纬度→城市 geo lookup，并落盘调试产物 |
| `backend_py/services/device_location_service.py` | 优先通过 `open -W` 启动定位 Helper；delegate 模块级定义；输出结构兼容 |
| `backend_py/services/network_tools_service.py` | 新增 `qweather_city_lookup()` 供定位链路复用 |
| `backend_py/config.py` | 修复 `.env` 加载依赖 cwd 的问题（改为基于仓库根目录） |
| `backend_py/macos_location_helper/` | 新增 Swift 定位 Helper（`.app` + `Info.plist`，支持 `--outPath`） |
| `test_scripts/debug_device_location.py` | 连续两次定位；并用 QWeather Geo lookup 验证城市信息 |
| `docs/CHANGELOG.md` | 记录本次变更 |

## 2026-02-13

### 🔧 问题修复
- KuGou `music_ui(search)`：修复“严格推荐 tabs ROI 校验”误判导致流程中断。更新默认 ROI 并支持环境变量覆盖 `KUGOU_RECOMMEND_TABS_STRICT_ROI`；同时将“强制回到推荐子页”改为 best-effort（失败仅记录 warning，不阻断后续搜索/播放）。
- KuGou `music_ui(search)`：修复“已进搜索页但误判未进入导致重复点击”，`top_search_verify` ROI 内出现 `搜索/搜/索` 即视为搜索态，并增加“焦点文本控件”兜底；同时为“单曲列表 OCR”增加加载态等待，避免把“加载中，请稍候”当作最终列表页。

### 🧪 测试
- 新增离线回归脚本 `test_scripts/test_kugou_recommend_tabs_roi_regression.py`：对给定截图验证推荐 tabs ROI 是否只命中「推荐/频道/歌单/歌手」。
- 新增耗时阶段基准脚本 `test_scripts/test_kugou_search_latency_benchmark.py`：拆分并记录 KuGou 搜索链路关键阶段耗时（置前/截图/窗口归一化/OCR），产物按 `VOICE_ASSISTANT_DEBUG_RUN` 落盘到 `ui_debug/<runId>/`。

### 📝 修改的文件
| 文件 | 修改内容 |
|------|----------|
| `backend_py/services/music_controller.py` | 修正推荐 tabs ROI；支持 env 覆盖；best-effort + legacy ROI 兜底 |
| `test_scripts/test_kugou_recommend_tabs_roi_regression.py` | 新增推荐 tabs ROI 离线回归脚本 |
| `test_scripts/test_kugou_search_latency_benchmark.py` | 新增耗时阶段基准脚本（置前/截图/归一化/OCR 分阶段计时） |
| `temp_md/2026-02-13_kugou_recommend_tabs_roi_fix.md` | 记录本次排障证据与复盘（含性能基准脚本） |
| `docs/CHANGELOG.md` | 记录本次变更 |

## 2026-02-12

### ✨ 功能增强
- 天气链路增强：新增预报工具 `get_weather_12h`（基于 QWeather `/v7/weather/24h`，截取前 12 小时），用于生成"未来 12 小时趋势 + 温度区间 + 暖心建议"。
- QWeather 鉴权升级：支持 EdDSA(JWT, Ed25519) 方式生成 `Authorization: Bearer <jwt>`（通过 `QWEATHER_JWT_KID/QWEATHER_JWT_PRIVATE_KEY_PATH` 配置），不再依赖 `QWEATHER_API_KEY`。
- 新增本地工具 `get_device_location`（macOS CoreLocation）：获取实时经纬度 + 精度（米）+ 街道/区/市地址信息；当用户问“我在哪/当前位置”或问天气但未指明城市时，模型必须优先调用该工具，禁止回退公网 IP 定位。
- 可观测性增强：对每次请求落盘 `device_location_capability`、`local_tool_*_get_device_location`、`net_tool_calls_round_*`、`location_override` 调试产物，明确本次是否成功使用 CoreLocation 以及是否纠正了 `location=auto`。



### 🔧 问题修复
- 修复模型输出偶发出现 `SAY:`/协议字段泄漏：后端对 `SAY`/中英文冒号/空格变体做强制清洗，确保对用户输出为纯文本。
- 收紧音乐兜底识别：避免"听故事/解释"等对话请求被误判为播放音乐。
- 稳定化自造 tool_call 结构：补齐 `type: "function"`，降低 OpenAI 兼容格式校验导致的偶发断链风险。

### 📝 修改的文件
| 文件 | 修改内容 |
|------|----------|
| `backend_py/controllers/conversation_controller.py` | 强化 SAY 清洗、收紧音乐兜底、补齐 tool_call type、支持设备定位上下文注入 | 
| `backend_py/services/network_tools_service.py` | 新增 `get_weather_12h`；坐标精度提升至最多四位小数；schema 修正 | 
| `backend_py/services/llm_service.py` | 提示词更新：天气 now+12h + 暖心输出规范；工具列表补齐 | 
| `backend_py/session_store.py` | 会话新增设备定位字段 | 
| `backend_py/main.py` | 新增 Socket.IO 事件 `update-device-location` | 
| `frontend/src/components/SettingsModal.tsx` | 新增设备定位授权开关与同步 | 
| `frontend/src/utils/settings.ts` | 设置新增设备定位字段 | 
| `frontend/src/utils/socket.ts` | 新增 `update-device-location` 通道封装 | 
| `frontend/src/hooks/useSocket.ts` | 连接后自动同步设备定位到后端 | 
| `test_scripts/test_network_tools_smoke.py` | 增加 `get_weather_12h` smoke 调用 | 
| `docs/CHANGELOG.md` | 记录本次变更 |

## 2026-02-11

### ✨ 功能增强
- `backend_py/`：新增"联网信息工具（MCP Client 执行端）"并接入 Qwen tools 决策链路：
  - SerpAPI Google 搜索（默认本土化：`google.com.hk` + `gl=cn` + `hl=zh-cn`）
  - newsdata.io 最新新闻（默认 `country=cn&language=zh`）
  - ipinfo widget demo 公网 IP 定位（用于自动获取当前城市/经纬度/时区）
  - QWeather `/v7/weather/now` 当前天气（支持 `LocationID`、`lon,lat`，并支持城市名→经纬度解析兜底）
  - 本地当前时间
- `backend_py/controllers/conversation_controller.py`：新增联网工具 tool-loop（支持 `parallel_tool_calls` 并行执行并回填给模型），且仅在前端开启联网开关后暴露这些工具
- `frontend/src/components/SettingsModal.tsx`：新增"联网查询"开关（默认关闭），首次开启弹出一次性授权确认并持久化；切换后同步到后端会话态
- `test_scripts/test_network_tools_smoke.py`：新增联网工具 smoke（可选联网验证），调试产物按 requestId 落盘

### 📝 修改的文件
| 文件 | 修改内容 |
|------|----------|
| `backend_py/services/network_tools_service.py` | 新增联网工具执行端（搜索/新闻/IP/天气/时间）与 `ui_debug/<requestId>/` 证据落盘 |
| `backend_py/services/llm_service.py` | 支持动态注入工具列表与 `parallel_tool_calls`；提示词补充联网工具使用规则 |
| `backend_py/controllers/conversation_controller.py` | 增加联网 tool-loop 与联网开关同步方法 |
| `backend_py/session_store.py` | 会话新增 `network_access_enabled` |
| `backend_py/main.py` | 新增 Socket.IO 事件 `update-network-settings` |
| `backend_py/config.py` | 新增联网相关环境变量读取（不落盘密钥） |
| `frontend/src/utils/settings.ts` | 设置新增 `networkAccessEnabled/networkAccessGranted` 并持久化 |
| `frontend/src/utils/socket.ts` | 新增 `update-network-settings` 通道封装 |
| `frontend/src/hooks/useSocket.ts` | 连接后自动同步联网开关到后端 |
| `frontend/src/components/SettingsModal.tsx` | 新增联网开关 UI + 首次授权弹窗 |
| `test_scripts/test_network_tools_smoke.py` | 新增 smoke 脚本 |
| `docs/CHANGELOG.md` | 记录本次变更 |

## 2026-02-09

### 🔧 问题修复
- `backend_py/services/music_controller.py`：修复第 2639-2649 行缩进错误（IndentationError），两个 `if` 判断块（pHash 验证 和 SHA256 验证）的 `raise` 语句及第二个 `if` 缩进层级不正确，导致服务启动失败
- `backend_py/services/music_controller.py`：修复"我喜欢（favorites_first）"路径顶部 tabs ROI 误采样导致无法识别"音乐"的问题；改用严格 ROI（只包含"音乐/艺人/动态"）并增加"必须且只能出现指定 tabs 文本"的校验与证据输出

### ✨ 功能增强
- `backend_py/services/asr_service.py`：STT 切换到 `qwen3-omni-flash-2025-12-01`（OpenAI 兼容模式，多模态 `input_audio` 输入），并输出 `dashscopeRequestId`/`responseId` 便于排障
- `backend_py/services/tts_service.py`：TTS 增强读稿一致性：对输入文本增加 `<READ_TEXT>` 边界标签、设置低随机性参数（`temperature=0`），并对账记录模型 `delta.content`，用于定位"UI 文本 vs 实际播报不一致"
- `frontend/src/components/SettingsModal.tsx`：语音设置音色列表切换为 Omni 新音色，并在 `update-tts-settings` 中透传 `voice`
- `backend_py/services/llm_service.py`：强化提示词策略：心情/随机听歌必须生成真实歌曲并走 `music_ui search`；写诗等纯文本任务默认直接产出且不反复确认
- `backend_py/services/music_controller.py`：`music_ui(search)` 在点击侧边栏"音乐"后，新增"强制双击内容区顶部'推荐'"步骤；并用严格 ROI 校验"推荐/频道/歌单/歌手"四词以确保回到推荐子页
- `test_scripts/debug_kugou_strict_tabs_roi_calib.py`：新增严格 tabs ROI 扫参脚本（`my_tabs`/`recommend_tabs`），输出 crop + JSON 证据用于调参

### 📝 修改的文件
| 文件 | 修改内容 |
|------|----------|
| `backend_py/services/music_controller.py` | 修正 pHash/SHA256 验证分支缩进；新增严格 tabs ROI 与"推荐"复位步骤 |
| `backend_py/services/asr_service.py` | STT 切换到 Omni 兼容模式（`input_audio`），并输出 requestId 相关日志 |
| `backend_py/services/tts_service.py` | 增强读稿一致性：标签边界 + 低随机性参数 + 文本对账日志 |
| `backend_py/controllers/conversation_controller.py` | TTS 设置新增 `voice` 字段并将 requestId 传入 TTS |
| `frontend/src/components/SettingsModal.tsx` | 更新音色列表与设置透传（`voice`） |
| `frontend/src/utils/settings.ts` | 默认音色改为 `Cherry` |
| `test_scripts/test_omni_tts_base64_buffer.py` | 新增离线可跑的 base64/音频分段解析自检 |
| `test_scripts/test_omni_stt_payload_smoke.py` | 新增 STT 请求结构自检（不依赖 pytest） |
| `test_scripts/test_llm_prompt_policy_smoke.py` | 新增提示词策略自检（不依赖 pytest） |
| `test_scripts/test_omni_tts_alignment_policy_smoke.py` | 新增 TTS 一致性策略自检（不依赖 pytest） |
| `test_scripts/debug_kugou_strict_tabs_roi_calib.py` | 新增严格 tabs ROI 扫参与证据落盘脚本 |
| `docs/CHANGELOG.md` | 记录本次修复与增强 |

## 2026-02-04

### 📝 代码注释中文化
- `backend_py/services/music_controller.py`：将所有英文注释/docstring 翻译为中文（约 70+ 处）
- `test_scripts/debug_kugou_search_flow.py`：英文注释中文化
- `test_scripts/debug_kugou_coord_roundtrip.py`：英文注释中文化
- `test_scripts/debug_kugou_search_entry_locator.py`：英文注释中文化
- `test_scripts/debug_kugou_searchbox_calib.py`：英文注释中文化
- `test_scripts/debug_kugou_panel_back_locator.py`：英文注释/docstring 中文化
- `test_scripts/debug_kugou_sidebar_music_calib.py`：英文注释/docstring 中文化
- `test_scripts/debug_kugou_sidebar_music_click_probe.py`：英文注释中文化
- `test_scripts/debug_kugou_search_service_ocr_fallback.py`：英文注释中文化
- `test_scripts/debug_ocr_vision_kugou.py`：英文注释/docstring 中文化（约 30+ 处）
- `test_scripts/debug_wecom_coord_calibration.py`：英文注释/docstring 中文化
- `test_scripts/test_e2e_socketio_music_flow.py`：英文注释中文化
- `test_scripts/test_music_intent_random_query.py`：英文注释中文化

### 📝 修改的文件
- `backend_py/services/music_controller.py`
- `test_scripts/debug_kugou_*.py`（7个文件）
- `test_scripts/debug_ocr_vision_kugou.py`
- `test_scripts/debug_wecom_coord_calibration.py`
- `test_scripts/test_e2e_socketio_music_flow.py`
- `test_scripts/test_music_intent_random_query.py`
- `docs/CHANGELOG.md`

## 2026-02-03

### ✨ 功能增强
- `backend_py/services/macos_ui_automation.py`：`click_at_debug` 新增 `cursorShots`（`screencapture -C` 光标截图）落盘，用"视觉 + mouseAfterClick/deltaAfterClick"双证据证明真实点击点。
- `backend_py/services/macos_ui_automation.py`：`screenMeta` 新增多口径 `globalMaxY`（`globalMaxYByScreens/globalMaxYInferred/globalMaxYUsed`），并落盘 `inferMeta`（同点双 API 采样推断：`appkitY + quartzY`）。
- `backend_py/services/macos_ui_automation.py`：修复窗口截图坐标→屏幕点击坐标的 Y 换算口径，避免在多屏环境下产生系统性纵向偏移导致"看起来乱点"。
- `backend_py/services/macos_ui_automation.py`：新增 `scroll_wheel`（Quartz 滚轮事件）供 UI 自动化滚动使用。
- `test_scripts/debug_wecom_coord_calibration.py`：新增企微截图坐标校准脚本（可选 warp 光标），将企微读数与多口径坐标输出到 `ui_debug/<runId>/`。
- `backend_py/services/music_controller.py`：重做 `favorites_first` 为"我的 → 内容区音乐 → 我喜欢 → 右侧半边栏选歌播放"，并支持 `pickMode=first/random`。

### 🔧 问题修复
- `backend_py/services/music_controller.py`：修复 KuGou 进入搜索页验证 ROI 过窄导致 OCR 截断（"取消"→"取"、漏掉"历史搜索"）从而触发多轮无效点击重试的问题；新增 `top_search_verify` 并对"历史搜索"拆词做容错。
- `backend_py/services/music_controller.py`：KuGou OCR v2 播放改为"单击目标歌曲名"；移除对底栏进度条的播放确认校验（避免底栏不稳定导致假失败）。
- `backend_py/services/music_controller.py`：KuGou v2 在 `kugou_song_list` 步骤改用专用 ROI 识别歌曲标题（避免标题左侧被裁剪）；当找不到目标歌曲时，额外落盘 `kugou_song_list_roi_crop_*` 与 `kugou_song_list_ocr_boxes_*` 证据文件便于复盘。
- `backend_py/services/music_controller.py`：修复 KuGou "我喜欢（favorites_first）"工作流不稳定：
  - 先判定是否已在"我的-音乐"内容区，必要时才点击顶部"音乐"tab（避免 ROI 采样到内容卡片导致找不到"音乐"）
  - "我喜欢"入口使用专用 ROI + 过滤异常宽框，降低误点"已购音乐"概率
  - 增加右侧半边栏打开成功宽松校验（候选关键词命中≥3）
  - 补齐证据链：落盘 `kugou_favorites_ocr_boxes_*` / `kugou_favorites_roi_crop_*`，并在成功/失败都写 `kugou_favorites_summary_*`
- `backend_py/controllers/conversation_controller.py`：删除后端"随机听歌"关键词识别与内置候选曲库兜底；随机听歌改为由 LLM 直接生成真实 `query` 并统一走 `music_ui(kugou/search)`。
- `backend_py/safety.py` / `backend_py/services/llm_service.py` / `backend_py/services/music_controller.py`：下线 `random_favorites` 动作枚举，避免模型/后端能力不一致。

### 📝 修改的文件
- `backend_py/controllers/conversation_controller.py`
- `backend_py/safety.py`
- `backend_py/services/llm_service.py`
- `backend_py/services/macos_ui_automation.py`
- `backend_py/services/music_controller.py`
- `backend_py/services/tool_router.py`
- `test_scripts/debug_wecom_coord_calibration.py`
- `docs/CHANGELOG.md`

## 2026-02-02

### ✨ 功能增强
- `test_scripts/debug_kugou_sidebar_music_click_probe.py` 探针脚本补齐 `click_at_debug` 的 `windowBounds/imageSize/ocrBox` 透传，使 `clickDebug` 落盘包含 `targetAsImagePoint/ocrHitTest`，与 UI 跳转截图形成闭环证据。
- `backend_py/services/macos_ui_automation.py` 为 `screencapture` 产出的 PNG 增加无损重压缩（重 deflate IDAT，像素不变），默认启用；可通过 `VOICE_ASSISTANT_PNG_LOSSLESS_COMPRESS=0` 关闭。
- `backend_py/services/macos_ui_automation.py` `click_at_debug` 落盘补齐多显示器坐标系信息：输出 `screenMeta`（screens frame、globalMaxY、以及目标点的 AppKit/屏幕局部坐标），用于对齐外部截图工具的坐标读数。

### 📝 修改的文件
- `backend_py/services/macos_ui_automation.py`
- `test_scripts/debug_kugou_sidebar_music_click_probe.py`
- `docs/CHANGELOG.md`
- `temp_md/2026-01-31_kugou_click_evidence.md`

## 2026-01-31

### ✨ 功能增强
- `test_scripts/debug_kugou_coord_roundtrip.py` 新增步骤3坐标映射 round-trip 验证脚本：对 KuGou 窗口截图坐标↔屏幕事件坐标做双向换算误差统计，并对 ROI OCR 与不同 scale 做对照，输出标注 PNG/JSON 证据到 `ui_debug/<runId>/`（默认 dry-run，可选真实点击验证）。
- `backend_py/services/music_controller.py` 在非 warp 模式下补充点击证据：将屏幕点击点回映射到窗口截图坐标，并输出 OCR 命中判定与偏移量，便于复盘"目标点 vs OCR 框"。
- `backend_py/services/music_controller.py` 侧边栏"音乐"入口采用组合 ROI 二次裁剪，优先紧凑 ROI 命中，失败回退完整侧栏 ROI。
- `backend_py/.env` 补全 Python 后端环境变量模板（DashScope、调试 runId、LLM stub、企业微信等）。

### 📝 修改的文件
- `backend_py/.env`
- `backend_py/services/music_controller.py`
- `docs/CHANGELOG.md`
- `temp_md/2026-01-31_kugou_click_evidence.md`
- `test_scripts/debug_kugou_coord_roundtrip.py`

## 2026-01-30

### 🔧 问题修复
- `backend_py/services/macos_ui_automation.py` 修复鼠标点击坐标系 Y 轴方向错误导致的"窗口内点击上下颠倒"（点击顶部控件却落到播放条附近）。将"窗口截图坐标（top-left）→CGEvent 鼠标坐标（top-left）"的换算改为：先用主屏高度把 Quartz `windowBounds` 的 bottom-left y 转成 top-left，再叠加窗口内 y；并同步修正 `click_window_relative` 的同类换算。
- `backend_py/services/macos_ui_automation.py` 新增鼠标位置探针：点击后可立刻读取当前鼠标位置并落盘到 `ui_debug/<runId>/`，用于判定"期望点击点 vs 实际鼠标位置"的偏差。
- `backend_py/services/macos_ui_automation.py` 新增 `click_at_debug`（可选 warp 光标）：在 debug/probe 场景下先将真实光标移动到目标点再点击，避免"CGEvent 注入但光标不动"导致证据误判。
- `backend_py/services/macos_ui_automation.py` 增强 `click_at_debug` 证据：额外记录点击前/后的前台进程（`frontmostBefore/After`），用于确认点击是否发生在酷狗前台窗口，避免误点到其它应用/Space。
- `backend_py/services/music_controller.py` 酷狗 `music_ui(search)`：收紧侧边栏"音乐"入口的 OCR ROI（新增 `KUGOU_ROIS["sidebar_music"]`），避免误点到相邻的"视频"入口导致进入 MV 页。
- `backend_py/services/music_controller.py` 酷狗 OCR 搜索链路增加"置前 + 校验"护栏：每次关键点击前强制验证前台进程为酷狗，否则立即中止并落盘证据，避免误操作落到内容区（如自建歌单）或其它窗口。
- `test_scripts/debug_kugou_sidebar_music_click_probe.py` 探针脚本记录每次点击后的鼠标位置与偏差，减少误判。

### 📝 修改的文件
- `backend_py/services/macos_ui_automation.py`
- `backend_py/services/music_controller.py`
- `docs/CHANGELOG.md`

## 2026-01-29

### ✨ 功能增强
- `backend_py/services/music_controller.py` 酷狗 `music_ui(search)`：结果页判定改为 OCR（必须出现"取消"且结果页 tabs 命中率≥50%），播放后增加"底栏正在播放目标歌 + 进度推进"确认作为停机条件，避免已成功播放却继续重复搜索；点歌改为优先 OCR 直接点击匹配目标歌名的结果项。
- `backend_py/services/music_controller.py` 新增 `_kugou_search_ocr_workflow`：`music_ui(search)` 入口改为直接走**纯 OCR 工作流**（先精确点击侧边栏`音乐`；主界面以 tabs 命中为主，不再强依赖 OCR 识别到低对比度 placeholder"搜索"；入搜优先用更聚焦的 `search_bar` ROI（高 scale）识别包含"搜索"的锚点并偏移点击进入输入区，失败则在 ROI 内确定性双击兜底；入搜后以"出现`取消`/`历史搜索`"作为硬验证信号；结果页按`单曲`→`播放全部`右侧播放按钮播放）。
- `backend_py/services/macos_ui_automation.py`：窗口截图增加 `screencapture -o` 禁用阴影，修复 `imageSize` 与 `windowBounds` 不一致导致的 OCR 点击点系统性偏移（表现为误点"猜你喜欢/歌单"等内容区）。
- `backend_py/services/music_controller.py`：OCR 工作流补齐 `kugou_flow_init` 初始截图（首个动作前），并将 `debug_info`（state/click/capture）落盘到 `ui_debug/<runId>/`，方便复盘与定位误点原因。
- `backend_py/services/music_controller.py` 修复 OCR 点击坐标系：统一使用窗口截图坐标→屏幕坐标转换，避免在主界面误点内容卡片（如"猜你喜欢"）。
- `backend_py/services/music_controller.py` 增强收敛：`unknown` 状态（如"分类"子页）优先用**顶部标题锚点+左偏移**点击返回，收敛回可搜索界面。
- `test_scripts/debug_kugou_panel_back_locator.py`：验证遮挡窗口 ROI 裁剪与标题锚点 OCR 识别（不再依赖 OCR 识别返回图标）。
- `test_scripts/debug_kugou_search_entry_locator.py`：验证 `top_search` ROI 内`搜索/取消`等文字锚点 OCR 可识别性（加入 `search_bar` 子 ROI + 多组 OCR 配置对照输出，避免单 ROI/单配置误判）。
- `test_scripts/test_e2e_socketio_music_flow.py` 输出 `playbackCheck`/`resultsPageDetect` 调试摘要，并支持 `E2E_ASSERT_PLAYING=1` 开启强断言。

## 2026-01-28

### ✨ 功能增强
- `test_scripts/debug_ocr_vision_kugou.py` 新增 `--anchor-dry-run`：基于 OCR 识别到的锚点文本框计算"理论点击点"，输出标注图与 JSON 明细（dry-run，不执行点击），用于评估"锚点 + 几何偏移"点击可行性。
- `backend_py/services/music_controller.py` 的 `music_ui(player="kugou", action="search")` 集成 OCR-first：先用 OCR 锚点与状态机进入搜索并播放；若失败自动回退到原有"回到音乐页 + 硬编码坐标点击"的兜底链路。
- `backend_py/controllers/conversation_controller.py` 修复"随便/随机听歌" badcase：不再把用户原话当作歌名搜索，改为从内置真实曲目候选随机挑选 query 并走 `music_ui(search)`（仍按高风险流程触发确认）。
- 新增测试脚本：
  - `test_scripts/test_music_intent_random_query.py`（意图解析回归）
  - `test_scripts/debug_kugou_search_service_ocr_fallback.py`（端到端服务调用验证）
  - `test_scripts/test_e2e_socketio_music_flow.py`（Socket.IO 全流程 E2E：含确认弹窗自动确认 + tool-result 校验）
- `backend_py/services/macos_ui_automation.py` 下沉 OCR 引擎能力：新增 `ocr_screenshot_advanced`，并让 `click_text` 支持 ROI 裁剪 + 灰度/缩放预处理参数（对小字号中文 UI 更稳）。
- `backend_py/services/music_controller.py` 将酷狗 `random_favorites` / `favorites_first` 统一为 `ocr_first + 坐标兜底`，并在"列表 OCR 抽不出曲目"时用列表区域坐标点选兜底，避免整条链路硬失败。
- `backend_py/services/llm_service.py` 新增 `VOICE_ASSISTANT_LLM_STUB=1` 本地模式（用于 E2E 不依赖外部千问 API）；并调整"随机听歌"策略为优先走 `music_ui(search)`。
- 强化首次 LLM 输出规范：当用户要求播放具体歌曲时，必须返回 `music_ui(search)` 且 `query` 为规范化搜索词（避免把"帮我播放/请/麻烦"等带进 query）。
- `backend_py/controllers/conversation_controller.py` 不再通过硬编码从用户原话强行提取 query 来升级 `play_music`，避免脏 query 误导酷狗搜索。
- `backend_py/services/music_controller.py` 增强酷狗 `music_ui(search)`：引入 OCR 状态机，优先循环点击"返回"按钮收敛到可搜索态；进入搜索后再多点聚焦输入框，并使用 pHash 距离判定是否进入搜索结果页，失败则回退坐标兜底。
- `test_scripts/test_e2e_socketio_music_flow.py` 增强可观测性：输出工作目录与每条用例的 `ui_debug/<requestId>` 产物索引，并打印 server 日志 tail。
- `backend_py/requirements.txt` 增加 `aiohttp`（Socket.IO AsyncClient 依赖）。

### 🔧 兼容性修复
- 兼容部分 PyObjC 环境缺失 `UniformTypeIdentifiers`：写 PNG 时回退使用 `public.png` 类型标识。
- 修复标注图生成：补齐 bitmapInfo（byte order + alpha）并改用 `CGBitmapContextCreateImage`，确保能稳定生成标注图片。

### 📝 修改的文件
- `backend_py/controllers/conversation_controller.py`
- `backend_py/requirements.txt`
- `backend_py/services/llm_service.py`
- `backend_py/services/macos_ui_automation.py`
- `backend_py/services/music_controller.py`
- `test_scripts/debug_ocr_vision_kugou.py`
- `test_scripts/debug_kugou_search_service_ocr_fallback.py`
- `test_scripts/test_e2e_socketio_music_flow.py`
- `test_scripts/test_music_intent_random_query.py`
- `docs/CHANGELOG.md`
- `temp_md/2026-01-28_ocr_eval_plan.md`

## 2026-01-27

### 🔧 问题修复
- 修复酷狗 `music_ui(search)` "✅ 执行成功但实际未进入搜索/未播放"的假成功问题：不再依赖 `Cmd+F` 或中文 OCR，改为"菜单入口尝试 + 窗口顶部相对坐标聚焦 + 键盘播放第一首"，并用前后窗口截图 hash 校验界面变化；若无变化则直接报错。
- 第二轮根据 `ui_debug/<requestId>/` 截图确认旧坐标会点到底部当前播放条或歌曲详情页，调整酷狗搜索流程：先尝试点击左上角返回箭头退回主界面，再用顶部中间偏右的一组相对坐标多次点击搜索框；若能通过 `get_focused_ui_element_info` 确认文本输入控件则优先使用该信号，否则仅记录 warning，最终仍以搜索前后窗口截图 hash 是否变化作为"是否成功进入搜索并触发播放"的硬判定，避免再出现"表面成功、实际上没有任何动作"的情况。
- 增强排障信息：菜单匹配失败时导出菜单结构快照；在 debug 中记录前后截图路径与 hash，以及搜索框点击的坐标和焦点信息，帮助后续微调。
- 支持按请求分目录落盘调试产物：后端每次工具调用会设置 `VOICE_ASSISTANT_DEBUG_RUN`，使截图/OCR dump/菜单 dump 进入 `ui_debug/<requestId>/`，避免多次测试日志混在一起。

### 📝 修改的文件
- `backend_py/services/macos_ui_automation.py`
- `backend_py/services/music_controller.py`
- `docs/CHANGELOG.md`
- `temp_md/2026-01-27_kugou_search_entry_fix.md`

## 2026-01-22

### 🔧 问题修复
- 修复酷狗 `music_ui` 偶发"未找到可见窗口"的问题：放宽窗口 ownerName 匹配策略，并在失败时导出窗口快照用于排障。
- 增强调试可观测性：当仍无法匹配窗口时，额外导出 `window_probe_*.json`（进程/前台应用/窗口计数）帮助定位权限或会话问题。
- 修复窗口枚举解析逻辑：兼容 Quartz 返回的 `NSDictionary/NSCFDictionary`（此前误用 `isinstance(..., dict)` 导致窗口全部被过滤，表现为快照为空且无法匹配酷狗窗口）。
- 修复酷狗中文 UI 文本 OCR 识别效果不佳导致的"找不到搜索/我的"等按钮：为 Vision OCR 显式设置中文识别语言，并在匹配时做文本归一化（去空白/替换字符）。
- 增加 OCR 排障日志：当 `click_text` 匹配失败时导出 `ocr_dump_*.json`（截图路径 + Top OCR boxes）。
- 增强酷狗搜索入口：优先用 `Cmd+F` 聚焦搜索框，避免依赖"搜索"中文 OCR 点击。
- 修复点歌请求可能只执行 `play_music`（仅打开播放器）而未执行"搜索并播放第一首"的问题：服务端对明确曲目请求做动作升级，改为 `music_ui(search)` 并触发确认。
- 新增"我喜欢第一首"播放能力：支持 `music_ui(player="kugou", action="favorites_first")`。

### 📝 修改的文件
- `backend_py/services/macos_ui_automation.py`
- `backend_py/controllers/conversation_controller.py`
- `backend_py/services/music_controller.py`
- `backend_py/services/llm_service.py`
- `backend_py/safety.py`
- `docs/CHANGELOG.md`

## 2026-01-19

### 🔧 问题修复
- 修复用户未指定播放器时可能出现"口头说在播放，但没有触发任何工具调用"的问题：服务端加入音乐请求兜底与文本纠偏。
- 修复酷狗 UI 自动化 OCR 可能读取到其他窗口（如 IDE）的问题：支持按酷狗窗口截图并进行窗口坐标换算。

### ✨ 功能增强
- `play_music` 新增 `source="kugou"` 的低风险动作：打开酷狗并触发系统媒体键播放/暂停。
- `LLMService` 提示词增强：禁止无动作却声称"已/正在播放"，并声明未指定播放器时默认 `kugou`。

### 📝 修改的文件
- `backend_py/controllers/conversation_controller.py`
- `backend_py/services/music_controller.py`
- `backend_py/services/macos_ui_automation.py`
- `backend_py/services/llm_service.py`
- `docs/CHANGELOG.md`

## 2026-01-11

### ✨ 功能增强
- 增加 LLM 单次返回的结构化意图 `INTENT_JSON` 协议，支持提问/操作/混合三态，并预留 `send_message`/`write_run_code`/`file_control` 等扩展动作。
- 后端将意图透传给前端，用于本地展示与调试。
- 新增 Python 后端 `backend_py/`（FastAPI + python-socketio），按现有 Socket.IO 协议对齐，作为阶段2替换骨架。
- Python 后端依赖收敛到 Python 3.11 可稳定安装的一组版本（见 `backend_py/requirements.txt`）。
- 阶段3（macOS）新增 `execute_workflow`（多步工具编排）与 `run_tests`（受控执行单测命令）。
- 阶段3（macOS）新增 `music_ui`：通过 UI 自动化控制音乐播放器（截图 + Vision OCR + 鼠标点击/键盘输入），支持酷狗/Apple Music 的"收藏随机 / 指定歌单 / 搜索播放"。
- 阶段3（macOS）新增 `media_control`：系统媒体键兜底（播放/暂停、上一首/下一首、音量、当前曲目信息等）。
- 由于 API 密钥难以获得，`send_message` 等外部 API 能力当前不作为默认能力暴露给 LLM（实现保留，后续可再启用）。
- `.gitignore` 新增忽略 `backend/.env` 与 `backend/.env.example`，避免误提交本地敏感配置。
- 启动脚本 `start.sh` 不再依赖 `backend/.env.example`，缺失时会生成最小的 `backend/.env` 模板。

### 🔧 问题修复
- 修复新请求开始时因前端 `stopAllRef` 短暂置位导致的音频块丢弃（表现为"开头内容没念"）。
- 修复 Python 千问 TTS 在 `websockets==14.1` 下 `extra_headers` 参数不兼容导致的运行时错误，恢复服务端音频输出。
- 前端语音输入切换为"录音直传后端 + 千问 Audio 语音识别"，并在停止录音时加入缓冲，降低短句截断概率。

### 📝 修改的文件
- `backend/services/llm.js`
- `backend/controllers/conversationController.js`
- `backend/services/toolRouter.js`
- `backend/utils/safety.js`
- `frontend/src/hooks/useSocket.ts`
- `frontend/src/hooks/useVoice.ts`
- `frontend/src/utils/socket.ts`
- `frontend/src/components/MessageList.tsx`
- `backend_py/main.py`
- `backend_py/controllers/conversation_controller.py`
- `backend_py/services/llm_service.py`
- `backend_py/services/tts_service.py`
- `backend_py/services/qwen_tts_ws.py`
- `backend_py/services/asr_service.py`
- `backend_py/services/tool_router.py`
- `backend_py/services/system_controller.py`
- `backend_py/config.py`
- `backend_py/services/llm_service.py`
- `backend_py/services/tool_router.py`
- `backend_py/services/music_controller.py`
- `backend_py/services/macos_ui_automation.py`
- `backend_py/services/macos_media_control.py`
- `backend_py/services/wecom_service.py`
- `backend_py/services/dev_runner.py`
- `backend_py/services/file_manager.py`
- `backend_py/services/file_writer.py`
- `backend_py/session_store.py`
- `backend_py/safety.py`
- `backend_py/utils/wav.py`
- `backend_py/requirements.txt`
- `requirements.txt`
- `start.sh`
