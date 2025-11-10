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

  async handleTextCommand(text, socket) {
    const socketId = socket.id;

    try {
      logger.info(`处理文本命令: ${socketId}`, { text });

      // 添加用户消息到会话
      this.sessionStore.addMessage(socketId, {
        type: "user",
        content: text,
        metadata: {},
      });

      // 获取会话历史
      const history = this.sessionStore.getHistory(socketId, 10);
      const messages = history
        .filter((msg) => msg.type === "user" || msg.type === "assistant")
        .map((msg) => ({
          role: msg.type === "user" ? "user" : "assistant",
          content: msg.content,
        }));

      // 调用LLM
      const llmResponse = await this.llmService.invokeLLM(messages);

      logger.info(`LLM响应: ${socketId}`, {
        hasToolCalls: llmResponse.toolCalls.length > 0,
        responseLength: llmResponse.text?.length || 0,
      });

      // 添加助手消息到会话
      this.sessionStore.addMessage(socketId, {
        type: "assistant",
        content: llmResponse.text || "",
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
        content: llmResponse.text,
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
        let streamingSuccess = false; // ✅ 添加流式播放成功标记

        // 首先尝试流式TTS - 使用缓冲策略
        try {
          logger.info("尝试流式TTS合成...");
          let firstChunkSent = false;
          let totalChunksSent = 0;

          const onAudioChunk = (chunk) => {
            logger.info(`收到音频块，大小: ${chunk.length} bytes`);

            // 确保音频块是有效的WAV格式
            if (chunk && chunk.length > 44) {
              // WAV头至少44字节
              totalChunksSent++;

              // 验证WAV格式
              const hasValidHeader =
                chunk.slice(0, 4).toString() === "RIFF" && chunk.slice(8, 12).toString() === "WAVE";

              if (hasValidHeader) {
                logger.info(`发送音频块 ${totalChunksSent} 到前端，大小: ${chunk.length} bytes`);

                // 将Buffer转换为ArrayBuffer发送给前端
                const arrayBuffer = chunk.buffer.slice(
                  chunk.byteOffset,
                  chunk.byteOffset + chunk.byteLength
                );

                socket.emit("audio-chunk", {
                  audioData: arrayBuffer,
                  text: llmResponse.text,
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
            llmResponse.text,
            voiceSettings,
            onAudioChunk
          );

          logger.info(
            `流式TTS合成完成，总音频长度: ${result.fullAudio.length} bytes, 音频块数: ${result.chunkCount}, 实际发送块数: ${totalChunksSent}`
          );

          // ✅ 修复：只有在流式播放失败时才保存音频数据用于后备
          if (result.fullAudio.length > 0 && totalChunksSent === 0) {
            audioData = result.fullAudio;
            logger.warn("流式TTS没有发送任何音频块，将使用完整音频作为后备");
          } else if (totalChunksSent > 0) {
            // ✅ 流式播放成功，标记成功状态，不再发送完整音频
            streamingSuccess = true;
            logger.info(`流式TTS成功播放 ${totalChunksSent} 个音频块，不再发送完整音频`);
            return; // ✅ 重要：流式播放成功，直接返回，不再执行后续逻辑
          }
        } catch (streamError) {
          logger.warn("流式TTS失败，尝试非流式TTS:", streamError.message);
        }

        // ✅ 修复：只有当流式TTS完全失败时才尝试非流式TTS
        if (!streamingSuccess) {
          try {
            logger.info("尝试非流式TTS合成...");
            audioData = await this.ttsService.textToSpeech(
              llmResponse.text,
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
              text: llmResponse.text,
              settings: voiceSettings,
            });
          } else {
            logger.warn("音频数据格式无效，发送空音频让前端使用Web Speech API");
            socket.emit("audio-response", {
              audioData: new ArrayBuffer(0),
              text: llmResponse.text,
              settings: voiceSettings,
            });
          }
        } else if (!streamingSuccess) {
          logger.warn("没有音频数据，发送空音频让前端使用Web Speech API");
          socket.emit("audio-response", {
            audioData: new ArrayBuffer(0),
            text: llmResponse.text,
            settings: voiceSettings,
          });
        } else {
          logger.info("✅ 流式TTS播放成功，不再发送完整音频");
        }

        // 标记音频流完成
        socket.emit("audio-chunk", {
          audioData: new ArrayBuffer(0),
          text: llmResponse.text,
          isComplete: true,
        });
      } catch (ttsError) {
        logger.error("千问TTS语音合成失败:", ttsError);
        // 即使TTS失败，也发送文本响应让前端处理
        socket.emit("audio-response", {
          audioData: new ArrayBuffer(0),
          text: llmResponse.text,
        });
      }

      return {
        success: true,
        response: llmResponse.text,
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

  // 设置socket实例的引用（需要在使用时注入）
  setSocketInstance(socketGetter) {
    this.getSocketById = socketGetter;
  }
}

module.exports = ConversationController;
