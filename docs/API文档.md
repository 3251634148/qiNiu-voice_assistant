# 语音控制电脑应用 - API 文档

## 概述

本文档描述了语音控制电脑应用的 REST API 和 Socket.IO 事件接口。

## 基础信息

- **服务器地址**: `http://localhost:3001`
- **API 版本**: v1
- **协议**: HTTP/HTTPS 和 WebSocket

## REST API

### 1. 健康检查

检查服务器运行状态。

**请求**
```
GET /api/health
```

**响应**
```json
{
  "status": "ok",
  "timestamp": "2024-01-01T00:00:00.000Z"
}
```

### 2. 获取系统能力

获取应用支持的功能列表。

**请求**
```
GET /api/capabilities
```

**响应**
```json
{
  "voiceRecognition": true,
  "textToSpeech": true,
  "systemControl": true,
  "fileOperations": true,
  "applicationControl": true,
  "musicControl": true,
  "safetyValidation": true,
  "sessionManagement": true,
  "toolExecution": true,
  "supportedTools": [
    "play_music",
    "stop_music", 
    "write_article",
    "write_file",
    "open_app"
  ]
}
```

### 3. 获取服务器统计

获取服务器运行统计信息。

**请求**
```
GET /api/stats
```

**响应**
```json
{
  "totalSessions": 5,
  "activeSessions": 3,
  "pendingToolCalls": 1,
  "pendingConfirmations": 0,
  "totalMessages": 127,
  "averageMessagesPerSession": 25,
  "connectedClients": 3,
  "timestamp": "2024-01-01T00:00:00.000Z"
}
```

## Socket.IO 事件

### 客户端发送事件

#### 1. voice-input

发送语音数据到服务器进行识别和处理。

**参数**
```typescript
{
  audioData: ArrayBuffer,  // 音频数据
  language?: string       // 语言代码，默认 'zh-CN'
}
```

#### 2. text-command

发送文本命令到服务器。

**参数**
```typescript
{
  text: string  // 要处理的文本
}
```

#### 3. confirm-action

确认或拒绝工具执行请求。

**参数**
```typescript
{
  confirmationId: string,  // 确认ID
  approved: boolean        // 是否批准
}
```

#### 4. cancel

取消当前待处理的操作。

**参数**: 无

#### 5. get-system-info

请求系统信息。

**参数**: 无

#### 6. get-session-status

请求当前会话状态。

**参数**: 无

#### 7. get-session-history

请求会话历史记录。

**参数**
```typescript
{
  limit?: number  // 返回记录数量限制，默认 50
}
```

### 服务器发送事件

#### 1. assistant-message

助手消息响应。

**数据**
```typescript
{
  type: 'assistant' | 'system' | 'tool_call' | 'tool_result' | 'safety_warning' | 'canceled',
  content: string,
  timestamp?: string,
  toolCall?: ToolCall,
  metadata?: any
}
```

#### 2. audio-response

语音合成响应。

**数据**
```typescript
{
  audioData: ArrayBuffer  // 音频数据
}
```

#### 3. tool-result

工具执行结果。

**数据**
```typescript
{
  type: 'success' | 'error',
  toolCall: {
    id: string,
    name: string
  },
  result?: any,
  error?: string,
  timestamp: string
}
```

#### 4. request-confirmation

请求用户确认工具执行。

**数据**
```typescript
{
  id: string,
  toolCall: ToolCall,
  riskLevel: 'low' | 'medium' | 'high',
  summary: string,
  reason: string,
  suggestions: string[],
  timestamp: string,
  expiresAt: string
}
```

#### 5. session-status

会话状态信息。

**数据**
```typescript
{
  exists: boolean,
  active: boolean,
  sessionId: string,
  hasPendingToolCall: boolean,
  hasPendingConfirmation: boolean,
  isCanceled: boolean,
  messageCount: number,
  lastActivity: string,
  createdAt: string
}
```

#### 6. session-history

会话历史记录。

**数据**
```typescript
Message[]  // 消息对象数组
```

#### 7. system-info

系统信息。

**数据**
```typescript
{
  platform: string,
  arch: string,
  nodeVersion: string,
  homeDir: string,
  freeMemory: number,
  totalMemory: number,
  uptime: number,
  loadavg: number[]
}
```

#### 8. error

错误信息。

**数据**
```typescript
{
  message: string
}
```

## 工具系统

### 支持的工具

#### 1. play_music
播放音乐。

**参数**
```typescript
{
  source?: 'spotify' | 'apple' | 'local',  // 音乐来源（可选；缺省时自动选择本机可用播放器）
  query?: string                            // 搜索查询（可选）
}
```

#### 2. stop_music
停止音乐播放。

**参数**: 无

#### 3. write_article
写文章。

**参数**
```typescript
{
  topic: string,                              // 文章主题
  style?: 'formal' | 'casual' | 'professional' | 'creative',  // 写作风格
  length?: 'short' | 'medium' | 'long'       // 文章长度
}
```

#### 4. write_file
写文件。

**参数**
```typescript
{
  path: string,                    // 文件路径
  content: string,                 // 文件内容
  mode?: 'create' | 'append' | 'overwrite'  // 写入模式
}
```

#### 5. open_app
打开应用程序。

**参数**
```typescript
{
  name: string  // 应用程序名称
}
```

### 安全机制

所有工具调用都会经过安全验证：

1. **参数验证**: 检查参数类型和有效性
2. **路径安全**: 防止路径遍历攻击
3. **范围限制**: 文件操作受允许目录范围限制；应用打开采用“本机已安装检索”而非固定白名单
4. **风险评估**: 根据操作类型评估风险等级
5. **用户确认**: 高风险操作需要用户确认

### 风险等级

- **低风险**: 安全操作，可直接执行
- **中等风险**: 建议用户确认
- **高风险**: 必须用户确认才能执行

## 错误处理

### HTTP 状态码

- `200`: 成功
- `400`: 请求参数错误
- `500`: 服务器内部错误

### 错误响应格式

```json
{
  "error": "错误描述信息"
}
```

## 配置

### 环境变量

参考 `.env.example` 文件了解可配置项：

- `DASHSCOPE_API_KEY`: 千问大模型API密钥
- `PORT`: 服务器端口
- `ALLOWED_APPS`: 允许的应用程序列表（历史配置项，当前版本不再用于 open_app）
- `ALLOWED_DIRECTORIES`: 允许的目录列表
- `SESSION_TIMEOUT`: 会话超时时间
- `LOG_LEVEL`: 日志级别

### 千问API配置

本项目使用阿里云千问大模型，您需要：

1. 获取阿里云百炼平台的API Key
2. 配置环境变量 `DASHSCOPE_API_KEY`
3. 支持的模型：
   - `qwen-plus`: 默认模型，适合日常对话
   - `qwen-turbo`: 高性能模型，适合复杂任务
   - `qwen-max`: 最强模型，适合高质量要求

## 示例代码

### JavaScript 客户端示例

```javascript
import io from 'socket.io-client';

const socket = io('http://localhost:3001');

// 监听助手消息
socket.on('assistant-message', (data) => {
  console.log('助手消息:', data.content);
});

// 发送文本命令
socket.emit('text-command', { text: '播放音乐' });

// 处理确认请求
socket.on('request-confirmation', (confirmation) => {
  if (confirm(`是否执行: ${confirmation.summary}?`)) {
    socket.emit('confirm-action', { 
      confirmationId: confirmation.id, 
      approved: true 
    });
  } else {
    socket.emit('confirm-action', { 
      confirmationId: confirmation.id, 
      approved: false 
    });
  }
});
```

### Python 客户端示例

```python
import socketio
import asyncio

sio = socketio.AsyncClient()

@sio.event
async def assistant_message(data):
    print(f"助手消息: {data['content']}")

@sio.event  
async def request_confirmation(data):
    print(f"确认请求: {data['summary']}")
    # 这里可以添加用户确认逻辑
    await sio.emit('confirm-action', {
        'confirmationId': data['id'],
        'approved': True
    })

async def main():
    await sio.connect('http://localhost:3001')
    await sio.emit('text-command', {'text': '写一篇文章'})
    await sio.wait()

asyncio.run(main())
```

## 更新日志

### v1.0.0
- 初始版本发布
- 支持基础语音识别和合成
- 实现工具系统和安全验证
- 添加会话管理和确认机制
- 完整的前后端集成