## 2026-02-04

### 📝 代码注释中文化
- `backend_py/services/music_controller.py`：将所有英文注释/docstring 翻译为中文（约 70+ 处）
- `test_scripts/debug_kugou_search_flow.py`：英文注释中文化
- `test_scripts/debug_kugou_coord_roundtrip.py`：英文注释中文化
- `test_scripts/debug_kugou_search_entry_locator.py`：英文注释中文化
- `test_scripts/debug_kugou_searchbox_calib.py`：英文注释中文化
- `test_scripts/debug_kugou_panel_back_locator.py`：英文注释/docstring 中文化
- `test_scripts/debug_kugou_sidebar_music_calib.py`：英文注释/docstring 中文化
- `test_scripts/debug_kugou_sidebar_music_click_probe.py`：英文注释/docstring 中文化
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
- `backend_py/services/macos_ui_automation.py`：`click_at_debug` 新增 `cursorShots`（`screencapture -C` 光标截图）落盘，用“视觉 + mouseAfterClick/deltaAfterClick”双证据证明真实点击点。
- `backend_py/services/macos_ui_automation.py`：`screenMeta` 新增多口径 `globalMaxY`（`globalMaxYByScreens/globalMaxYInferred/globalMaxYUsed`），并落盘 `inferMeta`（同点双 API 采样推断：`appkitY + quartzY`）。
- `backend_py/services/macos_ui_automation.py`：修复窗口截图坐标→屏幕点击坐标的 Y 换算口径，避免在多屏环境下产生系统性纵向偏移导致“看起来乱点”。
- `backend_py/services/macos_ui_automation.py`：新增 `scroll_wheel`（Quartz 滚轮事件）供 UI 自动化滚动使用。
- `test_scripts/debug_wecom_coord_calibration.py`：新增企微截图坐标校准脚本（可选 warp 光标），将企微读数与多口径坐标输出到 `ui_debug/<runId>/`。
- `backend_py/services/music_controller.py`：重做 `favorites_first` 为“我的 → 内容区音乐 → 我喜欢 → 右侧半边栏选歌播放”，并支持 `pickMode=first/random`。

### 🔧 问题修复
- `backend_py/services/music_controller.py`：修复 KuGou 进入搜索页验证 ROI 过窄导致 OCR 截断（“取消”→“取”、漏掉“历史搜索”）从而触发多轮无效点击重试的问题；新增 `top_search_verify` 并对“历史搜索”拆词做容错。
- `backend_py/services/music_controller.py`：KuGou OCR v2 播放改为“单击目标歌曲名”；移除对底栏进度条的播放确认校验（避免底栏不稳定导致假失败）。
- `backend_py/services/music_controller.py`：KuGou v2 在 `kugou_song_list` 步骤改用专用 ROI 识别歌曲标题（避免标题左侧被裁剪）；当找不到目标歌曲时，额外落盘 `kugou_song_list_roi_crop_*` 与 `kugou_song_list_ocr_boxes_*` 证据文件便于复盘。
- `backend_py/services/music_controller.py`：修复 KuGou “我喜欢（favorites_first）”工作流不稳定：
  - 先判定是否已在“我的-音乐”内容区，必要时才点击顶部“音乐”tab（避免 ROI 采样到内容卡片导致找不到“音乐”）
  - “我喜欢”入口使用专用 ROI + 过滤异常宽框，降低误点“已购音乐”概率
  - 增加右侧半边栏打开成功宽松校验（候选关键词命中≥3）
  - 补齐证据链：落盘 `kugou_favorites_ocr_boxes_*` / `kugou_favorites_roi_crop_*`，并在成功/失败都写 `kugou_favorites_summary_*`
- `backend_py/controllers/conversation_controller.py`：删除后端“随机听歌”关键词识别与内置候选曲库兜底；随机听歌改为由 LLM 直接生成真实 `query` 并统一走 `music_ui(kugou/search)`。
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
- `backend_py/services/music_controller.py` 在非 warp 模式下补充点击证据：将屏幕点击点回映射到窗口截图坐标，并输出 OCR 命中判定与偏移量，便于复盘“目标点 vs OCR 框”。
- `backend_py/services/music_controller.py` 侧边栏“音乐”入口采用组合 ROI 二次裁剪，优先紧凑 ROI 命中，失败回退完整侧栏 ROI。
- `backend_py/.env` 补全 Python 后端环境变量模板（DashScope、调试 runId、LLM stub、企业微信等）。

### 📝 修改的文件
- `backend_py/.env`
- `backend_py/services/music_controller.py`
- `docs/CHANGELOG.md`
- `temp_md/2026-01-31_kugou_click_evidence.md`
- `test_scripts/debug_kugou_coord_roundtrip.py`

## 2026-01-30

### 🔧 问题修复
- `backend_py/services/macos_ui_automation.py` 修复鼠标点击坐标系 Y 轴方向错误导致的“窗口内点击上下颠倒”（点击顶部控件却落到播放条附近）。将“窗口截图坐标（top-left）→CGEvent 鼠标坐标（top-left）”的换算改为：先用主屏高度把 Quartz `windowBounds` 的 bottom-left y 转成 top-left，再叠加窗口内 y；并同步修正 `click_window_relative` 的同类换算。
- `backend_py/services/macos_ui_automation.py` 新增鼠标位置探针：点击后可立刻读取当前鼠标位置并落盘到 `ui_debug/<runId>/`，用于判定“期望点击点 vs 实际鼠标位置”的偏差。
- `backend_py/services/macos_ui_automation.py` 新增 `click_at_debug`（可选 warp 光标）：在 debug/probe 场景下先将真实光标移动到目标点再点击，避免“CGEvent 注入但光标不动”导致证据误判。
- `backend_py/services/macos_ui_automation.py` 增强 `click_at_debug` 证据：额外记录点击前/后的前台进程（`frontmostBefore/After`），用于确认点击是否发生在酷狗前台窗口，避免误点到其它应用/Space。
- `backend_py/services/music_controller.py` 酷狗 `music_ui(search)`：收紧侧边栏“音乐”入口的 OCR ROI（新增 `KUGOU_ROIS["sidebar_music"]`），避免误点到相邻的“视频”入口导致进入 MV 页。
- `backend_py/services/music_controller.py` 酷狗 OCR 搜索链路增加“置前 + 校验”护栏：每次关键点击前强制验证前台进程为酷狗，否则立即中止并落盘证据，避免误操作落到内容区（如自建歌单）或其它窗口。
- `test_scripts/debug_kugou_sidebar_music_click_probe.py` 探针脚本记录每次点击后的鼠标位置与偏差，减少误判。

### 📝 修改的文件
- `backend_py/services/macos_ui_automation.py`
- `backend_py/services/music_controller.py`
- `docs/CHANGELOG.md`

## 2026-01-29

### ✨ 功能增强
- `backend_py/services/music_controller.py` 酷狗 `music_ui(search)`：结果页判定改为 OCR（必须出现“取消”且结果页 tabs 命中率≥50%），播放后增加“底栏正在播放目标歌 + 进度推进”确认作为停机条件，避免已成功播放却继续重复搜索；点歌改为优先 OCR 直接点击匹配目标歌名的结果项。
- `backend_py/services/music_controller.py` 新增 `_kugou_search_ocr_workflow`：`music_ui(search)` 入口改为直接走**纯 OCR 工作流**（先精确点击侧边栏`音乐`；主界面以 tabs 命中为主，不再强依赖 OCR 识别到低对比度 placeholder“搜索”；入搜优先用更聚焦的 `search_bar` ROI（高 scale）识别包含“搜索”的锚点并偏移点击进入输入区，失败则在 ROI 内确定性双击兜底；入搜后以“出现`取消`/`历史搜索`”作为硬验证信号；结果页按`单曲`→`播放全部`右侧播放按钮播放）。
- `backend_py/services/macos_ui_automation.py`：窗口截图增加 `screencapture -o` 禁用阴影，修复 `imageSize` 与 `windowBounds` 不一致导致的 OCR 点击点系统性偏移（表现为误点“猜你喜欢/歌单”等内容区）。
- `backend_py/services/music_controller.py`：OCR 工作流补齐 `kugou_flow_init` 初始截图（首个动作前），并将 `debug_info`（state/click/capture）落盘到 `ui_debug/<runId>/`，方便复盘与定位误点原因。
- `backend_py/services/music_controller.py` 修复 OCR 点击坐标系：统一使用窗口截图坐标→屏幕坐标转换，避免在主界面误点内容卡片（如“猜你喜欢”）。
- `backend_py/services/music_controller.py` 增强收敛：`unknown` 状态（如“分类”子页）优先用**顶部标题锚点+左偏移**点击返回，收敛回可搜索界面。
- `test_scripts/debug_kugou_panel_back_locator.py`：验证遮挡窗口 ROI 裁剪与标题锚点 OCR 识别（不再依赖 OCR 识别返回图标）。
- `test_scripts/debug_kugou_search_entry_locator.py`：验证 `top_search` ROI 内`搜索/取消`等文字锚点 OCR 可识别性（加入 `search_bar` 子 ROI + 多组 OCR 配置对照输出，避免单 ROI/单配置误判）。
- `test_scripts/test_e2e_socketio_music_flow.py` 输出 `playbackCheck`/`resultsPageDetect` 调试摘要，并支持 `E2E_ASSERT_PLAYING=1` 开启强断言。

## 2026-01-28

### ✨ 功能增强
- `test_scripts/debug_ocr_vision_kugou.py` 新增 `--anchor-dry-run`：基于 OCR 识别到的锚点文本框计算“理论点击点”，输出标注图与 JSON 明细（dry-run，不执行点击），用于评估“锚点 + 几何偏移”点击可行性。
- `backend_py/services/music_controller.py` 的 `music_ui(player="kugou", action="search")` 集成 OCR-first：先用 OCR 锚点与状态机进入搜索并播放；若失败自动回退到原有“回到音乐页 + 硬编码坐标点击”的兜底链路。
- `backend_py/controllers/conversation_controller.py` 修复“随便/随机听歌” badcase：不再把用户原话当作歌名搜索，改为从内置真实曲目候选随机挑选 query 并走 `music_ui(search)`（仍按高风险流程触发确认）。
- 新增测试脚本：
  - `test_scripts/test_music_intent_random_query.py`（意图解析回归）
  - `test_scripts/debug_kugou_search_service_ocr_fallback.py`（端到端服务调用验证）
  - `test_scripts/test_e2e_socketio_music_flow.py`（Socket.IO 全流程 E2E：含确认弹窗自动确认 + tool-result 校验）
- `backend_py/services/macos_ui_automation.py` 下沉 OCR 引擎能力：新增 `ocr_screenshot_advanced`，并让 `click_text` 支持 ROI 裁剪 + 灰度/缩放预处理参数（对小字号中文 UI 更稳）。
- `backend_py/services/music_controller.py` 将酷狗 `random_favorites` / `favorites_first` 统一为 `ocr_first + 坐标兜底`，并在“列表 OCR 抽不出曲目”时用列表区域坐标点选兜底，避免整条链路硬失败。
- `backend_py/services/llm_service.py` 新增 `VOICE_ASSISTANT_LLM_STUB=1` 本地模式（用于 E2E 不依赖外部千问 API）；并调整“随机听歌”策略为优先走 `music_ui(search)`。
- 强化首次 LLM 输出规范：当用户要求播放具体歌曲时，必须返回 `music_ui(search)` 且 `query` 为规范化搜索词（避免把“帮我播放/请/麻烦”等带进 query）。
- `backend_py/controllers/conversation_controller.py` 不再通过硬编码从用户原话强行提取 query 来升级 `play_music`，避免脏 query 误导酷狗搜索。
- `backend_py/services/music_controller.py` 增强酷狗 `music_ui(search)`：引入 OCR 状态机，优先循环点击“返回”按钮收敛到可搜索态；进入搜索后再多点聚焦输入框，并使用 pHash 距离判定是否进入搜索结果页，失败则回退坐标兜底。
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
- 修复酷狗 `music_ui(search)` “✅ 执行成功但实际未进入搜索/未播放”的假成功问题：不再依赖 `Cmd+F` 或中文 OCR，改为“菜单入口尝试 + 窗口顶部相对坐标聚焦 + 键盘播放第一首”，并用前后窗口截图 hash 校验界面变化；若无变化则直接报错。
- 第二轮根据 `ui_debug/<requestId>/` 截图确认旧坐标会点到底部当前播放条或歌曲详情页，调整酷狗搜索流程：先尝试点击左上角返回箭头退回主界面，再用顶部中间偏右的一组相对坐标多次点击搜索框；若能通过 `get_focused_ui_element_info` 确认文本输入控件则优先使用该信号，否则仅记录 warning，最终仍以搜索前后窗口截图 hash 是否变化作为“是否成功进入搜索并触发播放”的硬判定，避免再出现“表面成功、实际上没有任何动作”的情况。
- 增强排障信息：菜单匹配失败时导出菜单结构快照；在 debug 中记录前后截图路径与 hash，以及搜索框点击的坐标和焦点信息，帮助后续微调。
- 支持按请求分目录落盘调试产物：后端每次工具调用会设置 `VOICE_ASSISTANT_DEBUG_RUN`，使截图/OCR dump/菜单 dump 进入 `ui_debug/<requestId>/`，避免多次测试日志混在一起。

### 📝 修改的文件
- `backend_py/services/macos_ui_automation.py`
- `backend_py/services/music_controller.py`
- `docs/CHANGELOG.md`
- `temp_md/2026-01-27_kugou_search_entry_fix.md`

## 2026-01-22

### 🔧 问题修复
- 修复酷狗 `music_ui` 偶发“未找到可见窗口”的问题：放宽窗口 ownerName 匹配策略，并在失败时导出窗口快照用于排障。
- 增强调试可观测性：当仍无法匹配窗口时，额外导出 `window_probe_*.json`（进程/前台应用/窗口计数）帮助定位权限或会话问题。
- 修复窗口枚举解析逻辑：兼容 Quartz 返回的 `NSDictionary/NSCFDictionary`（此前误用 `isinstance(..., dict)` 导致窗口全部被过滤，表现为快照为空且无法匹配酷狗窗口）。
- 修复酷狗中文 UI 文本 OCR 识别效果不佳导致的“找不到搜索/我的”等按钮：为 Vision OCR 显式设置中文识别语言，并在匹配时做文本归一化（去空白/替换字符）。
- 增加 OCR 排障日志：当 `click_text` 匹配失败时导出 `ocr_dump_*.json`（截图路径 + Top OCR boxes）。
- 增强酷狗搜索入口：优先用 `Cmd+F` 聚焦搜索框，避免依赖“搜索”中文 OCR 点击。
- 修复点歌请求可能只执行 `play_music`（仅打开播放器）而未执行“搜索并播放第一首”的问题：服务端对明确曲目请求做动作升级，改为 `music_ui(search)` 并触发确认。
- 新增“我喜欢第一首”播放能力：支持 `music_ui(player="kugou", action="favorites_first")`。

### 📝 修改的文件
- `backend_py/services/macos_ui_automation.py`
- `backend_py/controllers/conversation_controller.py`
- `backend_py/services/music_controller.py`
- `backend_py/services/llm_service.py`
- `backend_py/safety.py`
- `docs/CHANGELOG.md`

## 2026-01-19

### 🔧 问题修复
- 修复用户未指定播放器时可能出现“口头说在播放，但没有触发任何工具调用”的问题：服务端加入音乐请求兜底与文本纠偏。
- 修复酷狗 UI 自动化 OCR 可能读取到其他窗口（如 IDE）的问题：支持按酷狗窗口截图并进行窗口坐标换算。

### ✨ 功能增强
- `play_music` 新增 `source="kugou"` 的低风险动作：打开酷狗并触发系统媒体键播放/暂停。
- `LLMService` 提示词增强：禁止无动作却声称“已/正在播放”，并声明未指定播放器时默认 `kugou`。

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
- 阶段3（macOS）新增 `music_ui`：通过 UI 自动化控制音乐播放器（截图 + Vision OCR + 鼠标点击/键盘输入），支持酷狗/Apple Music 的“收藏随机 / 指定歌单 / 搜索播放”。
- 阶段3（macOS）新增 `media_control`：系统媒体键兜底（播放/暂停、上一首/下一首、音量、当前曲目信息等）。
- 由于 API 密钥难以获得，`send_message` 等外部 API 能力当前不作为默认能力暴露给 LLM（实现保留，后续可再启用）。
- `.gitignore` 新增忽略 `backend/.env` 与 `backend/.env.example`，避免误提交本地敏感配置。
- 启动脚本 `start.sh` 不再依赖 `backend/.env.example`，缺失时会生成最小的 `backend/.env` 模板。

### 🔧 问题修复
- 修复新请求开始时因前端 `stopAllRef` 短暂置位导致的音频块丢弃（表现为“开头内容没念”）。
- 修复 Python 千问 TTS 在 `websockets==14.1` 下 `extra_headers` 参数不兼容导致的运行时错误，恢复服务端音频输出。
- 前端语音输入切换为“录音直传后端 + 千问 Audio 语音识别”，并在停止录音时加入缓冲，降低短句截断概率。

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
