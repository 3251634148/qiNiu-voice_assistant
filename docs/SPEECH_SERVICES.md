# 语音服务配置指南

## 🎯 概述

本项目支持多种语音识别和语音合成服务，您可以根据需要选择合适的服务提供商。

## 🚀 快速开始

### 1. 复制环境变量模板
```bash
cp .env.example .env
```

### 2. 选择语音服务提供商

## 🎤 语音识别服务 (ASR)

### Azure Speech Services (推荐)
1. 访问 [Azure Portal](https://portal.azure.com)
2. 创建 Speech Services 资源
3. 获取密钥和区域信息
4. 配置环境变量：
```
AZURE_SPEECH_ENDPOINT=https://your-region.stt.speech.microsoft.com
AZURE_SPEECH_KEY=your-speech-key
AZURE_SPEECH_REGION=your-region
```

### Google Speech-to-Text
1. 访问 [Google Cloud Console](https://console.cloud.google.com)
2. 启用 Speech-to-Text API
3. 创建服务账号并获取 API 密钥
4. 配置环境变量：
```
GOOGLE_SPEECH_API_KEY=your-google-speech-api-key
```

### 百度语音识别
1. 访问 [百度智能云](https://cloud.baidu.com)
2. 创建语音识别应用
3. 获取 API Key 和 Secret Key
4. 配置环境变量：
```
BAIDU_SPEECH_API_KEY=your-baidu-speech-api-key
BAIDU_SPEECH_SECRET_KEY=your-baidu-speech-secret-key
```

## 🔊 语音合成服务 (TTS)

### Azure Text-to-Speech (推荐)
使用与语音识别相同的配置即可。

### Google Text-to-Speech
1. 启用 Text-to-Speech API
2. 配置环境变量：
```
GOOGLE_TTS_API_KEY=your-google-tts-api-key
```

### 百度语音合成
1. 创建语音合成应用
2. 配置环境变量：
```
BAIDU_TTS_API_KEY=your-baidu-tts-api-key
BAIDU_TTS_SECRET_KEY=your-baidu-tts-secret-key
```

## ⚙️ 服务切换

### 后端配置
在 `conversationController.js` 中修改服务提供商：

```javascript
// 语音识别
const recognizedText = await this.speechService.speechToText(audioData, language, 'azure'); // azure, google, baidu

// 语音合成
const audioResponse = await this.ttsService.textToSpeech(text, 'zh-CN', 'female', 'azure'); // azure, google, baidu, mock
```

### 前端配置
前端默认使用 Web Speech API，如果需要使用后端服务，确保：
1. 后端服务已正确配置
2. 网络连接正常
3. 音频数据正确发送到后端

## 🔧 故障排除

### 语音识别失败
1. 检查麦克风权限
2. 确认音频数据格式正确 (WebM/Opus)
3. 验证服务提供商配置
4. 查看后端日志获取详细错误信息

### 语音合成失败
1. 检查文本内容是否为空
2. 确认语言代码正确 (如: zh-CN, en-US)
3. 验证服务提供商配置
4. 检查网络连接状态

### 服务降级
如果配置的服务失败，系统会自动降级到模拟服务：
- 语音识别：返回预设文本
- 语音合成：返回空音频数据，前端使用 Web Speech API

## 📊 性能对比

| 服务提供商 | 准确率 | 延迟 | 价格 | 支持语言 |
|------------|--------|------|------|----------|
| Azure | 高 | 低 | 中等 | 多 |
| Google | 高 | 低 | 中等 | 多 |
| 百度 | 中 | 中 | 低 | 少 |
| Web Speech API | 中 | 低 | 免费 | 浏览器相关 |

## 🔒 安全建议

1. **保护 API 密钥**：不要将密钥提交到代码仓库
2. **使用环境变量**：所有敏感信息都应通过环境变量配置
3. **限制访问**：在服务提供商控制台设置访问限制
4. **监控使用**：定期检查 API 使用情况和费用