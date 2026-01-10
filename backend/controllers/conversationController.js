const LLMService = require("../services/llm");
const ToolRouter = require("../services/toolRouter");
const SafetyService = require("../utils/safety");
const SessionStore = require("../utils/sessionStore");
const SpeechService = require("../services/speechService");
const TTSService = require("../services/ttsService");
const logger = require("../utils/logger");

class ConversationController {
  constructor() {
    this.llmService = new LLMService();
    this.toolRouter = new ToolRouter();
    this.safetyService = new SafetyService();
    this.sessionStore = new SessionStore();
    this.speechService = new SpeechService();
    this.ttsService = new TTSService();
  }

  async handleVoiceInput(audioData, language, socket) {
    const socketId = socket.id;

    try {
      logger.info(`处理语音输入: ${socketId}`, { language });

      // 添加用户消息到会话
      this.sessionStore.addMessage(socketId, {
        type: "user",
        content: "[语音输入]",
        metadata: { language, audioSize: audioData?.length },
      });

      // 语音识别应该在前端完成，后端只处理识别后的文本
      // 如果到达这里，说明前端需要后端协助处理语音识别
      logger.warn(`语音识别应该在前端使用Web Speech API完成: ${socketId}`);

      // 返回错误，提示前端使用Web Speech API
      socket.emit("assistant-message", {
        type: "error",
        content: "请使用浏览器的Web Speech API进行语音识别",
        timestamp: new Date().toISOString(),
      });

      return {
        success: false,
        error: "语音识别应该在前端完成",
        response: "请使用浏览器的Web Speech API进行语音识别",
      };
    } catch (error) {
      logger.error(`语音输入处理失败: ${socketId}`, error);

      const errorMessage = "语音处理失败，请重试。";

      socket.emit("assistant-message", {
        type: "error",
        content: errorMessage,
        timestamp: new Date().toISOString(),
      });

      return {
        success: false,
        error: error.message,
        response: errorMessage,
      };
    }
  }

  async handleTextCommand(text, socket, requestId = null) {
    const socketId = socket.id;

    const effectiveRequestId =
      typeof requestId === "string" && requestId.trim()
        ? requestId.trim()
        : `req_${socketId}_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;

    try {
      logger.info(`处理文本命令: ${socketId}`, { text, requestId: effectiveRequestId });

      // 重置TTS停止标记，开始新的对话
      this.resetTTSStop(socketId);

      // 记录当前请求ID，方便后续工具结果等关联
      const activeSession = this.sessionStore.getOrCreateSession(socketId);
      activeSession.currentRequestId = effectiveRequestId;

      // 添加用户消息到会话
      this.sessionStore.addMessage(socketId, {
        type: "user",
        content: text,
        metadata: { requestId: effectiveRequestId },
      });

      // 获取会话历史
      const history = this.sessionStore.getHistory(socketId, 10);
      const messages = history
        .filter((msg) => msg.type === "user" || msg.type === "assistant")
        .map((msg) => ({
          role: msg.type === "user" ? "user" : "assistant",
          content: msg.content,
        }));

      // 对话模式：优先使用低延迟流式回复（不启用工具调用），尽快开始播报
      // 工具模式：当用户明显在请求本地操作时，再启用工具调用
      if (!this.shouldUseToolsForText(text)) {
        return await this.handleTextCommandLowLatency(text, messages, socket, effectiveRequestId);
      }

      // 调用LLM
      const llmResponse = await this.llmService.invokeLLM(messages);

      // 不再强制截断 LLM 回复，让 prompt 控制回复长度
      // 如果 LLM 返回较长内容（如用户要求朗读诗歌），应该完整播放
      const responseText = llmResponse.text || "";

      logger.info(`LLM响应: ${socketId}`, {
        hasToolCalls: llmResponse.toolCalls.length > 0,
        toolCallNames: llmResponse.toolCalls.map((tc) => tc.function?.name || tc.name),
        responseLength: responseText.length,
      });

      // 添加助手消息到会话
      this.sessionStore.addMessage(socketId, {
        type: "assistant",
        content: responseText,
        metadata: {
          toolCalls: llmResponse.toolCalls,
          model: llmResponse.model,
          usage: llmResponse.usage,
        },
      });

      // 处理工具调用
      if (llmResponse.toolCalls.length > 0) {
        return await this.handleToolCalls(llmResponse.toolCalls, socket);
      }

      // 没有工具调用，直接返回文本响应
      socket.emit("assistant-message", {
        type: "text",
        content: responseText,
        requestId: effectiveRequestId,
        timestamp: new Date().toISOString(),
      });

      // 语音合成 - 使用千问TTS并支持用户设置
      try {
        // 获取用户TTS设置（如果有的话）
        const session = this.sessionStore.getOrCreateSession(socketId);
        const voiceSettings = session.ttsSettings || {
          gender: "female",
          rate: 1.0,
          pitch: 1.0,
        };

        logger.info(`使用TTS设置:`, voiceSettings);

        let audioData = null;
        let streamingSuccess = false; // 流式播放是否完整成功
        let streamingAborted = false; // 是否被停止中断

        // 首先尝试流式TTS - 使用缓冲策略
        try {
          logger.info("尝试流式TTS合成...");
          let firstChunkSent = false;
          let totalChunksSent = 0;
          let expectedChunks = 0;
          let bytesSentPcm = 0;

          const onAudioChunk = (chunk) => {
            // 检查TTS是否被停止
            if (this.isTTSStopped(socketId)) {
              streamingAborted = true;
              logger.info("TTS已被停止，跳过音频块发送");
              return;
            }

            logger.info(`收到音频块，大小: ${chunk.length} bytes`);

            // 确保音频块是有效的WAV格式
            if (chunk && chunk.length > 44) {
              // WAV头至少44字节
              totalChunksSent++;

              // 验证WAV格式
              const hasValidHeader =
                chunk.slice(0, 4).toString() === "RIFF" && chunk.slice(8, 12).toString() === "WAVE";

              if (hasValidHeader) {
                // 统计有效PCM字节数（去掉WAV头）
                const pcmLength = Math.max(chunk.length - 44, 0);
                bytesSentPcm += pcmLength;

                // 再次检查TTS是否被停止
                if (this.isTTSStopped(socketId)) {
                  streamingAborted = true;
                  logger.info("TTS已被停止，跳过音频块发送");
                  return;
                }

                logger.info(`发送音频块 ${totalChunksSent} 到前端，大小: ${chunk.length} bytes`);

                // 将Buffer转换为ArrayBuffer发送给前端
                const arrayBuffer = chunk.buffer.slice(
                  chunk.byteOffset,
                  chunk.byteOffset + chunk.byteLength
                );

                socket.emit("audio-chunk", {
                  audioData: arrayBuffer,
                  text: responseText,
                  requestId: effectiveRequestId,
                  isComplete: false,
                });

                if (!firstChunkSent) {
                  firstChunkSent = true;
                  logger.info("首个音频块已发送");
                }
              } else {
                logger.warn("音频块格式无效，跳过该块");
              }
            } else {
              logger.warn("音频块太小或为空，跳过该块");
            }
          };

          const result = await this.ttsService.streamTextToSpeech(
            responseText,
            voiceSettings,
            onAudioChunk
          );

          expectedChunks = result.chunkCount || 0;
          const totalPcmBytes =
            typeof result.fullAudioPcmLength === "number"
              ? result.fullAudioPcmLength
              : Math.max((result.fullAudio?.length || 0) - 44, 0);
          const coverage = totalPcmBytes > 0 ? bytesSentPcm / totalPcmBytes : 0;

          logger.info(
            `流式TTS合成完成，总音频长度(带头): ${result.fullAudio.length} bytes, PCM长度: ${totalPcmBytes} bytes, 音频块数: ${expectedChunks}, 实际发送块数: ${totalChunksSent}, 已发送PCM: ${bytesSentPcm} bytes, 覆盖率: ${(coverage * 100).toFixed(2)}%`
          );

          const sentAllBytes = !streamingAborted && totalPcmBytes > 0 && bytesSentPcm >= totalPcmBytes;

          logger.info(`流式TTS发送统计`, {
            expectedChunks,
            totalChunksSent,
            bytesSentPcm,
            totalPcmBytes,
            coverage,
            sentAllBytes,
            streamingAborted,
          });

          // 只有在发送的PCM字节覆盖了完整音频且未被中断时，才算成功
          if (sentAllBytes) {
            streamingSuccess = true;
            logger.info(`流式TTS已发送完整音频（覆盖率 ${(coverage * 100).toFixed(2)}%），不再发送完整音频`);
          } else if (result.fullAudio.length > 0) {
            audioData = result.fullAudio;
            logger.warn(
              `流式TTS未完整发送（已发送 ${bytesSentPcm}/${totalPcmBytes} PCM 字节），使用完整音频兜底`
            );
          } else {
            logger.warn("流式TTS没有有效音频块，准备进入兜底");
          }
        } catch (streamError) {
          logger.warn("流式TTS失败，尝试非流式TTS:", streamError.message);
        }

        // ✅ 修复：只有当流式TTS完全失败时才尝试非流式TTS
        if (!streamingSuccess) {
          try {
            logger.info("尝试非流式TTS合成...");
            audioData = await this.ttsService.textToSpeech(
              responseText,
              "zh-CN",
              voiceSettings
            );
            logger.info(`非流式TTS合成成功，音频长度: ${audioData.length} bytes`);
          } catch (syncError) {
            logger.error("非流式TTS也失败了:", syncError);
          }
        }

        // ✅ 修复：只有当流式TTS完全失败时才发送完整音频
        if (!streamingSuccess && audioData && audioData.length > 0) {
          logger.info(`流式TTS失败，使用完整音频作为后备方案`);
          // 验证音频数据格式
          const hasValidWavHeader =
            audioData.length >= 44 &&
            audioData.slice(0, 4).toString() === "RIFF" &&
            audioData.slice(8, 12).toString() === "WAVE";

          if (hasValidWavHeader) {
            // 将Buffer转换为ArrayBuffer
            const arrayBuffer = audioData.buffer.slice(
              audioData.byteOffset,
              audioData.byteOffset + audioData.byteLength
            );

            logger.info(
              `流式TTS失败，发送完整音频到前端，ArrayBuffer大小: ${arrayBuffer.byteLength} bytes`
            );

            socket.emit("audio-response", {
              audioData: arrayBuffer,
              text: responseText,
              requestId: effectiveRequestId,
              settings: voiceSettings,
            });
          } else {
            logger.warn("音频数据格式无效，发送空音频让前端使用Web Speech API");
            socket.emit("audio-response", {
              audioData: new ArrayBuffer(0),
              text: responseText,
              requestId: effectiveRequestId,
              settings: voiceSettings,
            });
          }
        } else if (!streamingSuccess) {
          logger.warn("没有音频数据，发送空音频让前端使用Web Speech API");
          socket.emit("audio-response", {
            audioData: new ArrayBuffer(0),
            text: responseText,
            requestId: effectiveRequestId,
            settings: voiceSettings,
          });
        } else {
          logger.info("✅ 流式TTS播放成功，不再发送完整音频");
        }

        // 标记音频流完成（即便成功也发送完成信号）
        socket.emit("audio-chunk", {
          audioData: new ArrayBuffer(0),
          text: responseText,
          requestId: effectiveRequestId,
          isComplete: true,
        });
      } catch (ttsError) {
        logger.error("千问TTS语音合成失败:", ttsError);
        // 即使TTS失败，也发送文本响应让前端处理
        socket.emit("audio-response", {
          audioData: new ArrayBuffer(0),
          text: responseText,
          requestId: effectiveRequestId,
        });
      }

      return {
        success: true,
        response: responseText,
        toolCalls: [],
      };
    } catch (error) {
      logger.error(`文本命令处理失败: ${socketId}`, error);

      const errorMessage = "抱歉，处理您的请求时出现错误。请重试。";

      socket.emit("assistant-message", {
        type: "error",
        content: errorMessage,
        timestamp: new Date().toISOString(),
      });

      return {
        success: false,
        error: error.message,
        response: errorMessage,
      };
    }
  }

  async handleToolCalls(toolCalls, socket) {
    const socketId = socket.id;
    const results = [];

    try {
      for (const toolCall of toolCalls) {
        const parsedToolCall = {
          id: toolCall.id,
          name: toolCall.function.name,
          arguments: JSON.parse(toolCall.function.arguments),
        };

        // 安全校验
        const session = this.sessionStore.getOrCreateSession(socketId);
        const safetyCheck = await this.safetyService.validateToolCall(parsedToolCall, {
          allowLocalControl: session.allowLocalControl ?? session.ttsSettings?.allowLocalControl ?? true,
        });

        if (!safetyCheck.allowed) {
          logger.warn(`工具调用被安全检查阻止: ${socketId}`, {
            toolName: parsedToolCall.name,
            reason: safetyCheck.reason,
          });

          // 发送安全警告
          socket.emit("assistant-message", {
            type: "safety_warning",
            content: `操作被阻止：${safetyCheck.reason}`,
            toolCall: parsedToolCall,
            timestamp: new Date().toISOString(),
          });

          results.push({
            toolCallId: toolCall.id,
            name: parsedToolCall.name,
            success: false,
            error: safetyCheck.reason,
          });

          continue;
        }

        // 如果需要确认
        if (safetyCheck.requiresConfirmation) {
          const confirmationRequest = this.safetyService.generateConfirmationRequest(
            parsedToolCall,
            safetyCheck
          );

          // 设置待确认请求
          this.sessionStore.setPendingConfirmation(socketId, confirmationRequest);
          this.sessionStore.setPendingToolCall(socketId, parsedToolCall, safetyCheck);

          // 发送确认请求到前端
          socket.emit("request-confirmation", confirmationRequest);

          logger.info(`发送确认请求: ${socketId}`, {
            confirmationId: confirmationRequest.id,
            toolName: parsedToolCall.name,
          });

          results.push({
            toolCallId: toolCall.id,
            name: parsedToolCall.name,
            status: "pending_confirmation",
            confirmationId: confirmationRequest.id,
          });

          continue;
        }

        // 直接执行工具调用
        const result = await this.executeToolCall(parsedToolCall, socket);
        results.push(result);
      }

      return {
        success: true,
        toolCalls: results,
        requiresConfirmation: results.some((r) => r.status === "pending_confirmation"),
      };
    } catch (error) {
      logger.error(`工具调用处理失败: ${socketId}`, error);

      socket.emit("assistant-message", {
        type: "error",
        content: "工具执行过程中出现错误",
        timestamp: new Date().toISOString(),
      });

      return {
        success: false,
        error: error.message,
        toolCalls: results,
      };
    }
  }

  async executeToolCall(toolCall, socket) {
    const socketId = socket.id;

    try {
      logger.info(`执行工具调用: ${socketId}`, {
        toolName: toolCall.name,
        arguments: toolCall.arguments,
      });

      // 添加工具调用消息到会话
      this.sessionStore.addMessage(socketId, {
        type: "tool_call",
        content: `调用工具: ${toolCall.name}`,
        metadata: { toolCall },
      });

      // 执行工具
      const session = this.sessionStore.getOrCreateSession(socketId);
      const result = await this.toolRouter.routeAndExecute(toolCall, {
        socketId: socketId,
        session,
        allowLocalControl: session.allowLocalControl ?? session.ttsSettings?.allowLocalControl ?? true,
      });

      // 添加工具结果到会话
      this.sessionStore.addMessage(socketId, {
        type: "tool_result",
        content: result.success ? result.result.message || "操作完成" : result.error,
        metadata: { result },
      });

      // 发送结果到前端
      socket.emit("tool-result", {
        type: result.success ? "success" : "error",
        toolCall: {
          id: result.toolCallId,
          name: result.name,
        },
        result: result.success ? result.result : null,
        error: result.success ? null : result.error,
        timestamp: result.timestamp,
      });

      // 如果有文本消息，也进行语音合成
      if (result.success && result.result.message) {
        try {
          // 获取用户TTS设置
          const session = this.sessionStore.getOrCreateSession(socketId);
          const voiceSettings = session.ttsSettings || {
            gender: "female",
            rate: 1.0,
            pitch: 1.0,
          };

          const audioResponse = await this.ttsService.textToSpeech(
            result.result.message,
            "zh-CN",
            voiceSettings
          );
          socket.emit("audio-response", {
            audioData: audioResponse,
            text: result.result.message,
            requestId: session.currentRequestId,
            settings: voiceSettings,
          });
        } catch (ttsError) {
          logger.error("工具结果语音合成失败:", ttsError);
        }
      }

      logger.info(`工具调用完成: ${socketId}`, {
        toolName: toolCall.name,
        success: result.success,
      });

      return result;
    } catch (error) {
      logger.error(`工具调用执行失败: ${socketId}`, error);

      const errorResult = {
        toolCallId: toolCall.id,
        name: toolCall.name,
        success: false,
        error: error.message,
        timestamp: new Date().toISOString(),
      };

      // 发送错误结果
      socket.emit("tool-result", {
        type: "error",
        toolCall: {
          id: toolCall.id,
          name: toolCall.name,
        },
        error: error.message,
        timestamp: errorResult.timestamp,
      });

      return errorResult;
    }
  }

  // 处理用户确认
  async handleConfirmation(socketId, confirmationId, approved) {
    try {
      logger.info(`处理用户确认: ${socketId}`, {
        confirmationId,
        approved,
      });

      const pendingConfirmation = this.sessionStore.getPendingConfirmation(socketId);

      if (!pendingConfirmation || pendingConfirmation.id !== confirmationId) {
        throw new Error("确认请求不存在或已过期");
      }

      const pendingToolCall = this.sessionStore.getOrCreateSession(socketId).pendingToolCall;

      if (!pendingToolCall) {
        throw new Error("待处理的工具调用不存在");
      }

      // 清除待确认状态
      this.sessionStore.clearPendingConfirmation(socketId);

      if (approved) {
        // 用户批准，执行工具调用
        const result = await this.executeToolCall(pendingToolCall, { id: socketId });
        this.sessionStore.clearPendingToolCall(socketId);

        return {
          success: true,
          action: "executed",
          result: result,
        };
      } else {
        // 用户拒绝
        this.sessionStore.markCanceled(socketId, "user_rejected");
        this.sessionStore.clearPendingToolCall(socketId);

        // 发送取消消息
        const socket = this.getSocketById(socketId); // 需要从外部传入socket实例
        if (socket) {
          socket.emit("assistant-message", {
            type: "canceled",
            content: "操作已取消",
            timestamp: new Date().toISOString(),
          });
        }

        return {
          success: true,
          action: "canceled",
          message: "用户取消了操作",
        };
      }
    } catch (error) {
      logger.error(`处理确认失败: ${socketId}`, error);

      return {
        success: false,
        error: error.message,
      };
    }
  }

  // 处理用户取消
  async handleCancel(socketId, silent = false) {
    try {
      logger.info(`处理用户取消: ${socketId}`, { silent });

      const _session = this.sessionStore.getOrCreateSession(socketId);

      this.sessionStore.clearPendingConfirmation(socketId);
      this.sessionStore.clearPendingToolCall(socketId);
      this.sessionStore.markCanceled(socketId, "user_canceled");

      // 同时停止TTS
      this.stopTTS(socketId);

      const socket = this.getSocketById(socketId);
      if (socket && !silent) {
        socket.emit("assistant-message", {
          type: "canceled",
          content: "当前操作已取消",
          timestamp: new Date().toISOString(),
        });
      }

      return {
        success: true,
        message: "操作已取消",
      };
    } catch (error) {
      logger.error(`处理取消失败: ${socketId}`, error);

      return {
        success: false,
        error: error.message,
      };
    }
  }

  /**
   * 停止TTS播放
   * 设置会话的TTS停止标记，阻止后续音频块发送
   * @param {string} socketId - Socket连接ID
   */
  stopTTS(socketId) {
    try {
      logger.info(`停止TTS: ${socketId}`);

      const session = this.sessionStore.getOrCreateSession(socketId);
      // 设置TTS停止标记
      session.ttsStopped = true;

      // 关闭TTS WebSocket连接（如果有的话）
      if (this.ttsService && this.ttsService.qwenTTS) {
        try {
          this.ttsService.qwenTTS.close();
        } catch (closeError) {
          logger.warn("关闭TTS WebSocket时出错:", closeError.message);
        }
      }

      logger.info(`TTS已停止: ${socketId}`);
    } catch (error) {
      logger.error(`停止TTS失败: ${socketId}`, error);
    }
  }

  /**
   * 重置TTS停止标记
   * 在开始新的TTS任务前调用
   * @param {string} socketId - Socket连接ID
   */
  resetTTSStop(socketId) {
    const session = this.sessionStore.getOrCreateSession(socketId);
    session.ttsStopped = false;
  }

  /**
   * 检查TTS是否被停止
   * @param {string} socketId - Socket连接ID
   * @returns {boolean} 是否被停止
   */
  isTTSStopped(socketId) {
    const session = this.sessionStore.sessions?.get(socketId);
    return session?.ttsStopped === true;
  }

  // 获取会话状态
  getSessionStatus(socketId) {
    return this.sessionStore.getSessionStatus(socketId);
  }

  // 获取会话历史
  getSessionHistory(socketId, limit = 50) {
    return this.sessionStore.getHistory(socketId, limit);
  }

  // 清理会话
  clearSession(socketId) {
    return this.sessionStore.clearSession(socketId);
  }

  // 获取统计信息
  getStats() {
    return this.sessionStore.getStats();
  }

  // 更新TTS设置
  updateTTSSettings(socketId, settings) {
    try {
      logger.info(`更新TTS设置: ${socketId}`, settings);

      const session = this.sessionStore.getOrCreateSession(socketId);
      session.ttsSettings = {
        gender: settings.gender || "female",
        rate: parseFloat(settings.rate) || 1.0,
        pitch: parseFloat(settings.pitch) || 1.0,
        model: settings.model || session.ttsSettings?.model,
        allowLocalControl: settings.allowLocalControl ?? (session.ttsSettings?.allowLocalControl ?? true),
      };
      if (typeof settings.allowLocalControl === "boolean") {
        session.allowLocalControl = settings.allowLocalControl;
      }
      session.ttsSettings.rate = Math.max(0.5, Math.min(2.0, session.ttsSettings.rate));
      session.ttsSettings.pitch = Math.max(0.5, Math.min(2.0, session.ttsSettings.pitch));

      logger.info(`TTS设置已更新: ${socketId}`, session.ttsSettings);

      return {
        success: true,
        settings: session.ttsSettings,
      };
    } catch (error) {
      logger.error(`更新TTS设置失败: ${socketId}`, error);

      return {
        success: false,
        error: error.message,
      };
    }
  }

  // 获取TTS设置
  getTTSSettings(socketId) {
    try {
      const session = this.sessionStore.getOrCreateSession(socketId);
      return {
        success: true,
        settings: session.ttsSettings || {
          gender: "female",
          rate: 1.0,
          pitch: 1.0,
          model: undefined,
        },
      };
    } catch (error) {
      logger.error(`获取TTS设置失败: ${socketId}`, error);

      return {
        success: false,
        error: error.message,
      };
    }
  }

  // 获取可用的TTS音色
  getAvailableVoices() {
    try {
      const voices = this.ttsService.getAvailableVoices();
      return {
        success: true,
        voices: voices,
      };
    } catch (error) {
      logger.error("获取可用音色失败:", error);

      return {
        success: false,
        error: error.message,
      };
    }
  }

  /**
   * 是否允许本地操控（用于前端显式 stop-music 等指令）
   * @param {string} socketId - Socket连接ID
   * @returns {boolean}
   */
  isLocalControlAllowed(socketId) {
    const session = this.sessionStore.getOrCreateSession(socketId);
    return session.allowLocalControl ?? session.ttsSettings?.allowLocalControl ?? true;
  }

  /**
   * 判断用户文本是否更可能触发本地工具调用。
   * 在工具模式下，我们保持原有“先拿完整 LLM 回复再处理工具调用”的行为。
   */
  shouldUseToolsForText(text) {
    const toolIntentKeywords = [
      // 音乐
      "播放音乐",
      "停止音乐",
      "关掉音乐",
      "暂停音乐",
      "听音乐",
      "想听音乐",
      "来点音乐",
      "放点音乐",
      "听歌",
      "来首歌",
      "放首歌",
      "播放一首",
      "播一首",

      // 应用
      "打开",
      "启动",

      // 文件
      "写文件",
      "创建文件",
      "写入文件",
      "保存到",
    ];

    return toolIntentKeywords.some((keyword) => text.includes(keyword));
  }

  /**
   * 从缓冲区中提取可合成的分段，尽量在句号/问号/叹号处切分。
   * @param {string} buffer - 文本缓冲
   * @param {boolean} force - 是否强制把剩余内容也作为分段返回
   */
  extractTtsSegments(buffer, force = false) {
    const segments = [];
    const text = String(buffer || "");

    const boundaryRegex = /[。！？!?]/g;
    let startIndex = 0;
    let match;

    while ((match = boundaryRegex.exec(text)) !== null) {
      const endIndex = match.index + 1;
      const candidate = text.slice(startIndex, endIndex).trim();
      if (candidate.length >= 8) {
        segments.push(candidate);
        startIndex = endIndex;
      }
    }

    let rest = text.slice(startIndex);

    // 如果没有句末标点，但文本太长，为了降低延迟强制切分
    const maxSegmentLength = 60;
    if (rest.length >= maxSegmentLength) {
      const head = rest.slice(0, maxSegmentLength);
      const splitIndex = Math.max(head.lastIndexOf("，"), head.lastIndexOf(","));
      const cutAt = splitIndex >= 12 ? splitIndex + 1 : maxSegmentLength;

      const forced = rest.slice(0, cutAt).trim();
      if (forced.length >= 8) {
        segments.push(forced);
        rest = rest.slice(cutAt);
      }
    }

    if (force) {
      const remaining = rest.trim();
      if (remaining) {
        segments.push(remaining);
        rest = "";
      }
    }

    return { segments, rest };
  }

  async synthesizeSegmentToSocket(segmentText, voiceSettings, socket, socketId, requestId) {
    if (this.isTTSStopped(socketId)) {
      return;
    }

    const onAudioChunk = (chunk) => {
      if (this.isTTSStopped(socketId)) {
        return;
      }

      if (!chunk || chunk.length <= 44) {
        return;
      }

      const hasValidHeader =
        chunk.slice(0, 4).toString() === "RIFF" && chunk.slice(8, 12).toString() === "WAVE";

      if (!hasValidHeader) {
        return;
      }

      const arrayBuffer = chunk.buffer.slice(chunk.byteOffset, chunk.byteOffset + chunk.byteLength);

      socket.emit("audio-chunk", {
        audioData: arrayBuffer,
        text: segmentText,
        requestId: requestId,
        isComplete: false,
      });
    };

    try {
      await this.ttsService.streamTextToSpeech(segmentText, voiceSettings, onAudioChunk);
    } catch (error) {
      logger.warn("分段流式TTS失败，忽略该段:", error.message);
    }
  }

  /**
   * 低延迟流式：LLM 边输出 → 文本分段 → TTS 边合成边推送 → 前端边收边播
   */
  async handleTextCommandLowLatency(text, messages, socket, requestId) {
    const socketId = socket.id;

    // 获取用户TTS设置（如果有的话）
    const session = this.sessionStore.getOrCreateSession(socketId);
    const voiceSettings = session.ttsSettings || {
      gender: "female",
      rate: 1.0,
      pitch: 1.0,
    };

    let responseText = "";
    let buffer = "";

    let ttsChain = Promise.resolve();

    const enqueueSegment = (segment) => {
      if (this.isTTSStopped(socketId)) {
        return;
      }
      const segmentText = String(segment || "").trim();
      if (!segmentText) {
        return;
      }

      // 串行化 TTS，避免并发导致音频交错
      ttsChain = ttsChain.then(() =>
        this.synthesizeSegmentToSocket(segmentText, voiceSettings, socket, socketId, requestId)
      );
    };

    const ttsAvailable = this.ttsService && typeof this.ttsService.isAvailable === "function" && this.ttsService.isAvailable();

    logger.info(`低延迟流式模式: ${socketId}`, {
      ttsAvailable,
      inputLength: text.length,
    });

    // 如果TTS不可用（比如未配置 key），依旧使用流式拿到文本，但只能在末尾触发 Web Speech 兜底
    const onDelta = (delta) => {
      responseText += delta;

      if (!ttsAvailable || this.isTTSStopped(socketId)) {
        return;
      }

      buffer += delta;
      const { segments, rest } = this.extractTtsSegments(buffer, false);
      buffer = rest;

      for (const seg of segments) {
        enqueueSegment(seg);
      }
    };

    const llmResult = await this.llmService.streamText(messages, { onDelta });

    // 兜底：如果 onDelta 没有累计到（理论不会），用最终文本补齐
    if (!responseText) {
      responseText = llmResult.text || "";
    }

    // 结束时把剩余缓冲也合成
    if (ttsAvailable && !this.isTTSStopped(socketId)) {
      const { segments } = this.extractTtsSegments(buffer, true);
      buffer = "";
      for (const seg of segments) {
        enqueueSegment(seg);
      }
    }

    // 添加助手消息到会话
    this.sessionStore.addMessage(socketId, {
      type: "assistant",
      content: responseText,
      metadata: {
        toolCalls: [],
        model: llmResult.model,
      },
    });

    // 返回文本响应（UI展示）
    socket.emit("assistant-message", {
      type: "text",
      content: responseText,
      requestId: requestId,
      timestamp: new Date().toISOString(),
    });

    if (ttsAvailable) {
      try {
        await ttsChain;
      } finally {
        // 标记音频流完成（即便被停止也发送完成信号，让前端收尾）
        socket.emit("audio-chunk", {
          audioData: new ArrayBuffer(0),
          text: responseText,
          requestId: effectiveRequestId,
          isComplete: true,
        });
      }
    } else {
      // 触发前端 Web Speech API 兜底
      socket.emit("audio-response", {
        audioData: new ArrayBuffer(0),
        text: responseText,
        requestId: requestId,
        settings: voiceSettings,
      });
    }

    return {
      success: true,
      response: responseText,
      toolCalls: [],
    };
  }

  // 设置socket实例的引用（需要在使用时注入）
  setSocketInstance(socketGetter) {
    this.getSocketById = socketGetter;
  }
}

module.exports = ConversationController;
