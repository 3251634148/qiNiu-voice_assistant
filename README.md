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
cp .env.example .env
# 编辑 .env 文件，填入你的 API 密钥
```

4. **启动开发服务器**
```bash
# 在项目根目录
npm run dev
```

### 环境变量配置

在 `backend/.env` 文件中配置以下变量：

```env
# 基础配置
NODE_ENV=development
PORT=3001

# OpenAI API（必需）
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

## 贡献指南

1. Fork 项目
2. 创建功能分支
3. 提交更改
4. 创建 Pull Request

## 许可证
MIT License