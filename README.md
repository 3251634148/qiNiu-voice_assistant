# 智能语音助手 - 开发指南

## 快速开始

### 环境要求
- Node.js 18+
- npm 或 yarn
- Git

### 安装步骤

1. **克隆项目**
```bash
git clone <repository-url>
cd voice-assistant
```

2. **安装依赖**
```bash
npm run install:all
```

3. **配置环境变量**
```bash
cd backend
# 编辑 .env 文件，填入你的 API 密钥
```

4. **启动开发服务器**
```bash
# 在项目根目录
npm run dev
```

（可选）仅启动 Python 后端（`backend_py/`，用于同协议替换/本地自动化能力）：
```bash
# 建议 Python 3.11
source backend_py/.venv/bin/activate
uvicorn backend_py.main:asgi_app --host 0.0.0.0 --port 3001
```

### 环境变量配置

在 `backend/.env` 文件中配置以下变量：

```env
# 基础配置
NODE_ENV=development
PORT=3001

# DashScope（千问）：LLM/TTS/ASR（Python 后端与部分 Node 路径会用到）
DASHSCOPE_API_KEY=your_dashscope_api_key_here

# OpenAI（历史依赖，是否生效以实际运行路径为准）
OPENAI_API_KEY=your_openai_api_key_here

# 语音识别服务（可选）
AZURE_SPEECH_KEY=your_azure_speech_key
AZURE_SPEECH_REGION=your_azure_speech_region
GOOGLE_SPEECH_API_KEY=your_google_speech_api_key
BAIDU_SPEECH_API_KEY=your_baidu_speech_api_key
BAIDU_SPEECH_SECRET_KEY=your_baidu_speech_secret_key

# TTS服务（可选）
AZURE_SPEECH_ENDPOINT=your_azure_speech_endpoint
GOOGLE_TTS_API_KEY=your_google_tts_api_key
BAIDU_TTS_API_KEY=your_baidu_tts_api_key
BAIDU_TTS_SECRET_KEY=your_baidu_tts_secret_key

# 调试产物 runId（可选）：用于把 ui_debug 产物落盘到固定目录
VOICE_ASSISTANT_DEBUG_RUN=debug_run_$(date +%s)

# 设备定位（macOS CoreLocation）：需前端开启“设备定位”开关，并在系统弹窗中授予定位权限
# - 后端会在需要时实时调用 CoreLocation 获取经纬度与街道/区/市信息
# - 未开启设备定位时，定位/未指明城市的天气不会回退公网 IP

# 联网信息工具（可选）：需前端开启“联网查询”开关后生效
SERPAPI_API_KEY=your_serpapi_api_key_here
NEWSDATA_API_KEY=your_newsdata_api_key_here

# QWeather（推荐 JWT 方式）
QWEATHER_API_HOST=your_qweather_api_host_here
QWEATHER_JWT_SUB=your_project_id_here
QWEATHER_JWT_KID=your_credential_id_here
QWEATHER_JWT_PRIVATE_KEY_PATH=/absolute/path/to/ed25519-private.pem
QWEATHER_JWT_TTL_SECONDS=3600

# （兼容）旧版 Key 方式：若未配置 JWT 才会回退使用
QWEATHER_API_KEY=your_qweather_api_key_here
```

## 项目结构

```
voice-assistant/
├── backend/                 # 后端服务
│   ├── services/           # 业务逻辑服务
│   │   ├── voiceAssistant.js    # 语音助手核心服务
│   │   ├── systemController.js  # 系统控制服务
│   │   ├── speechService.js     # 语音识别服务
│   │   └── ttsService.js        # 语音合成服务
│   ├── utils/              # 工具函数
│   │   └── logger.js            # 日志工具
│   ├── server.js           # 主服务器文件
│   └── package.json
├── frontend/               # 前端应用
│   ├── src/
│   │   ├── components/     # React 组件
│   │   ├── hooks/          # 自定义 Hooks
│   │   ├── utils/          # 工具函数
│   │   ├── types/          # TypeScript 类型定义
│   │   └── App.tsx         # 主应用组件
│   ├── public/             # 静态资源
│   │   └── electron.js     # Electron 主进程
│   └── package.json
├── docs/                   # 文档
└── package.json           # 根项目配置
```

## 核心功能说明

### 1. 语音识别
支持多种语音识别服务：
- Web Speech API（浏览器原生）
- Azure Speech Services
- Google Speech-to-Text
- 百度语音识别

### 2. 自然语言理解
基于 OpenAI GPT 模型：
- 智能意图识别
- 指令结构化
- 上下文理解

### 3. 电脑控制
支持的操作类型：
- 文件操作：创建、删除、移动、复制
- 应用控制：启动、关闭应用
- 系统控制：音量、设置等
- 信息查询：文件内容、系统信息

### 4. 语音合成
支持多种 TTS 服务：
- OpenAI TTS
- Azure Speech Services
- Google Text-to-Speech
- 百度语音合成

## 开发指南

### 后端开发

#### 添加新的系统操作
1. 在 `systemController.js` 中添加新方法：
```javascript
async newOperation(params) {
  try {
    // 实现操作逻辑
    return { success: true, message: '操作成功' };
  } catch (error) {
    throw new Error(`操作失败: ${error.message}`);
  }
}
```

2. 在 `executeAction` 方法中添加新的 case：
```javascript
case 'new_operation':
  return await this.newOperation(action.params);
```

3. 更新系统提示词，添加新操作的说明

#### 添加新的语音识别服务
1. 在 `speechService.js` 中添加新方法：
```javascript
async newProviderSpeechToText(audioData, language) {
  // 实现新的语音识别逻辑
}
```

2. 在 `speechToText` 方法中添加新的 case

### 前端开发

#### 添加新组件
1. 在 `src/components/` 目录下创建新组件
2. 使用 TypeScript 定义 props 类型
3. 遵循现有的设计规范

#### 添加新功能
1. 在 `src/types/` 中定义相关类型
2. 在 `src/hooks/` 中添加业务逻辑
3. 更新状态管理

### 部署

#### 开发环境
```bash
npm run dev
```

#### 生产环境构建
```bash
# 构建前端
npm run build

# 启动生产服务器
npm start
```

#### Electron 应用打包
```bash
cd frontend
npm run electron:build
```

## API 文档

### Socket.IO 事件

#### 客户端发送事件
- `voice-input`: 发送语音数据
- `text-command`: 发送文本命令
- `get-system-info`: 获取系统信息

#### 服务端发送事件
- `speech-recognized`: 语音识别结果
- `assistant-response`: 助手响应
- `voice-response`: 语音合成结果
- `action-result`: 操作执行结果
- `system-info`: 系统信息
- `error`: 错误信息

### REST API
- `GET /api/health`: 健康检查
- `GET /api/capabilities`: 获取功能列表

## 故障排除

### 常见问题

1. **语音识别失败**
   - 检查麦克风权限
   - 确认网络连接
   - 验证 API 密钥配置

2. **无法连接服务器**
   - 检查后端服务是否启动
   - 确认端口配置
   - 检查防火墙设置

3. **系统操作失败**
   - 检查权限设置
   - 确认路径正确性
   - 查看错误日志

### 日志查看
```bash
# 查看后端日志
tail -f backend/logs/combined.log

# 查看错误日志
tail -f backend/logs/error.log
```

### KuGou UI 自动化排障（坐标映射步骤3）

当出现“OCR 识别到了文字但点击点不中/点歪”时，可先用 round-trip 验证脚本证伪是否为坐标映射问题（ROI offset / OCR scale 回缩 / y 轴翻转 / Retina points↔pixels 比例等）。

- **脚本**：`test_scripts/debug_kugou_coord_roundtrip.py`
- **产物目录**：`~/Documents/VoiceAssistant/ui_debug/<VOICE_ASSISTANT_DEBUG_RUN>/`
  - `coord_roundtrip_*.json`：包含 image→screen→image 的误差统计与 OCR 命中信息
  - `coord_roundtrip_*.png`：把 OCR 框与测试点画回整图，便于人工核对

运行（默认 dry-run，不执行点击）：
```bash
VOICE_ASSISTANT_DEBUG_RUN=coord_roundtrip_$(date +%s) backend_py/.venv/bin/python test_scripts/debug_kugou_coord_roundtrip.py
```

可选：开启真实点击验证（高风险，谨慎使用）：
```bash
VOICE_ASSISTANT_DEBUG_RUN=coord_roundtrip_click_$(date +%s) backend_py/.venv/bin/python test_scripts/debug_kugou_coord_roundtrip.py --do-click
```

### KuGou UI 自动化排障脚本索引（摘要）

- **OCR 评测 / anchor dry-run**：`test_scripts/debug_ocr_vision_kugou.py`
  - 用于评估 Vision OCR 对关键字（音乐/我的/取消/单曲/歌单/综合…）的命中；或只做锚点识别→计算理论点击点→输出标注（不点击）。
- **搜索入口定位排障**：`test_scripts/debug_kugou_search_entry_locator.py`
  - 用于验证“顶部搜索框 placeholder 很浅/低对比度”导致 OCR 不稳时，ROI/scale 调参的对照输出。
- **坐标映射 round-trip 证伪（步骤3）**：`test_scripts/debug_kugou_coord_roundtrip.py`
  - 用于验证窗口截图坐标↔屏幕点击坐标的双向换算是否自洽，排除 ROI offset/scale 回缩/y 翻转/Retina 比例等系统性偏移。
- **E2E（Socket.IO）链路验证**：`test_scripts/test_e2e_socketio_music_flow.py`
  - 用于在不依赖真实前端交互的情况下，跑“确认→工具执行→产物落盘”的端到端回归，并输出 `ui_debug/<requestId>/` 索引。

### TTS 停止/打断排障（摘要）

- **关键机制**：前端应保证全局只有一份音频播放控制面（例如通过 `SocketProvider` 单例化 `useSocket()`），并通过 `requestId` 丢弃旧请求“晚到音频”。
- **快速定位**：若“点击停止后仍继续播放”，优先检查：
  - 是否存在多个 `useSocket()` 实例分别持有不同的 `AudioContext`/队列；
  - 后端是否支持 `stop-tts`/cancel 并停止继续下发 `audio-chunk`；
  - 是否使用“静音闸（GainNode）”实现瞬时静音，避免队列中残留音频继续播完。

### 低延迟流式与工具兜底（摘要）

- **低延迟模式**通常用于闲聊类快速出声；明确的本地控制指令应进入工具模式，避免“只说不做”。
- 若出现“工具未执行但口头说已执行”，建议在后端增加“进入工具模式但模型未给出动作时的兜底意图解析/纠偏”，并将真实执行结果通过 `tool-result` 与 `ui_debug` 证据落盘。

## 贡献指南

1. Fork 项目
2. 创建功能分支
3. 提交更改
4. 创建 Pull Request

## 许可证
MIT License