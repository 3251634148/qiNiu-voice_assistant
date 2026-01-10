import { useCallback, useEffect, useRef, useState } from "react";

import type { ActionResult, ConfirmationRequest, Message, ToolResult } from "../types";
import { getSettings } from "../utils/settings";
import type SocketService from "../utils/socket";
import { getSocketService } from "../utils/socket";
import { useApp } from "./useApp";

export function useSocket() {
  const { addMessage, setVoiceState, setConnectionState, setSessionState } = useApp();
  const socketRef = useRef<SocketService | null>(null);
  const [isInitialized, setIsInitialized] = useState(false);
  const connectionAttemptRef = useRef(false); // 防止重复连接尝试

  useEffect(() => {
    // 防止重复初始化
    if (connectionAttemptRef.current) {
      return;
    }
    connectionAttemptRef.current = true;

    const initSocket = async () => {
      try {
        setConnectionState({ connecting: true, error: undefined });

        // 使用全局单例SocketService
        const socket = getSocketService();

        // 如果已经连接，直接设置状态
        if (socket.isConnected()) {
          console.log("Socket已连接，跳过连接过程");
          socketRef.current = socket;
          setIsInitialized(true);
          setConnectionState({ connected: true, connecting: false });
          return;
        }

        // 否则进行连接
        await socket.connect();

        socketRef.current = socket;
        setIsInitialized(true);
        setConnectionState({ connected: true, connecting: false });

        // 设置事件监听器 - 只在第一次连接时设置
        socket.onSpeechRecognized((data) => {
          addMessage("user", data.text);
        });

        socket.onAssistantMessage((data) => {
          const currentRequestId = activeRequestIdRef.current;
          const incomingRequestId = (data as any)?.requestId as string | undefined;

          if (incomingRequestId && currentRequestId && incomingRequestId !== currentRequestId) {
            console.log("忽略旧 assistant-message", {
              incomingRequestId,
              currentRequestId,
              type: data.type,
            });
            return;
          }

          addMessage(data.type as any, data.content, {
            ...(data.metadata || {}),
            requestId: incomingRequestId,
          });
        });

        socket.onAudioResponse((data) => {
          if (stopAllRef.current) return;

          const currentRequestId = activeRequestIdRef.current;
          const incomingRequestId = data.requestId;

          // 方案3：丢弃旧请求的音频（避免停止后“晚到音频”重新播放）
          if (incomingRequestId && currentRequestId && incomingRequestId !== currentRequestId) {
            console.log("忽略旧 audio-response", {
              incomingRequestId,
              currentRequestId,
            });
            return;
          }

          playAudioResponse(data.audioData, data.text, incomingRequestId);
        });

        // 监听音频流块事件
        socket.onAudioChunk((data) => {
          // 如果正在停止播放，忽略所有音频块
          if (stopAllRef.current) {
            console.log("正在停止播放，忽略音频块");
            return;
          }

          const currentRequestId = activeRequestIdRef.current;
          const incomingRequestId = data.requestId;

          // 方案3：丢弃旧请求的音频流
          if (incomingRequestId && currentRequestId && incomingRequestId !== currentRequestId) {
            console.log("忽略旧 audio-chunk", {
              incomingRequestId,
              currentRequestId,
              isComplete: data.isComplete,
            });
            return;
          }

          if (data.isComplete) {
            console.log("音频流完成");
            setVoiceState({ isSpeaking: false });
          } else if (data.audioData && data.audioData.byteLength > 0) {
            console.log(`收到音频流块，长度: ${data.audioData.byteLength} bytes`);
            // 实时播放音频流块
            playAudioStreamChunk(data.audioData, data.text);
          }
        });

        socket.onToolResult((result: ToolResult) => {
          const message =
            result.type === "success"
              ? `✅ 工具执行成功: ${result.toolCall.name}`
              : `❌ 工具执行失败: ${result.error}`;
          addMessage("tool_result", message, { toolResult: result });
        });

        socket.onRequestConfirmation((confirmation: ConfirmationRequest) => {
          const settings = getSettings();

          // 如果本地应用操控被禁用且操作涉及本地应用，自动拒绝
          if (!settings.allowLocalControl && confirmation.riskLevel === "high") {
            addMessage("system", `🚫 本地应用操控已禁用，已自动拒绝操作: ${confirmation.summary}`, {
              confirmationId: confirmation.id,
              riskLevel: confirmation.riskLevel,
            });

            // 自动拒绝操作
            setTimeout(() => {
              confirmAction(confirmation.id, false);
            }, 1000);
            return;
          }

          setSessionState({
            hasPendingConfirmation: true,
            hasPendingToolCall: true,
            isCanceled: false,
            confirmationRequest: confirmation,
          });

          addMessage("system", `⚠️ 需要确认操作: ${confirmation.summary}`, {
            confirmationId: confirmation.id,
            riskLevel: confirmation.riskLevel,
          });
        });

        socket.onSessionStatus((status) => {
          setSessionState({
            hasPendingToolCall: status.hasPendingToolCall,
            hasPendingConfirmation: status.hasPendingConfirmation,
            isCanceled: status.isCanceled,
          });
        });

        socket.onSessionHistory((history: Message[]) => {
          // 可以在这里处理历史记录
          console.log("收到会话历史:", history);
        });

        socket.onActionResult((result: ActionResult) => {
          const { result: actionResult } = result;
          const message = actionResult.success
            ? `✅ ${actionResult.message || "操作完成"}`
            : `❌ 操作失败: ${actionResult.error}`;
          addMessage("system", message);
        });

        socket.onSystemInfo(() => {
          setConnectionState({ connected: true });
        });

        socket.onError((error) => {
          setConnectionState({ error: error.message });
          addMessage("system", `⚠️ ${error.message}`);
        });

        socket.onDisconnect(() => {
          setConnectionState({ connected: false, error: "与服务器断开连接" });
          addMessage("system", "⚠️ 与服务器断开连接");
        });
      } catch (error) {
        console.error("Socket初始化失败:", error);
        setConnectionState({
          connected: false,
          connecting: false,
          error: "连接服务器失败",
        });
        addMessage("system", "⚠️ 连接服务器失败");
      }
    };

    initSocket();

    return () => {
      // 不要在这里断开全局单例的连接
      // 只清理引用
      socketRef.current = null;
      connectionAttemptRef.current = false;
    };
  }, []);

  const audioContextRef = useRef<AudioContext | null>(null);
  const gainNodeRef = useRef<GainNode | null>(null);
  const audioQueueRef = useRef<ArrayBuffer[]>([]);
  const isPlayingRef = useRef(false);
  const currentSourceRef = useRef<AudioBufferSourceNode | null>(null);
  const stopAllRef = useRef(false);
  const activeRequestIdRef = useRef<string | null>(null);

  const generateRequestId = () => {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
      return crypto.randomUUID();
    }
    return `req_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
  };

  // 获取或创建音频上下文
  const getAudioContext = useCallback(() => {
    if (!audioContextRef.current) {
      audioContextRef.current = new (window.AudioContext || (window as any).webkitAudioContext)();
    }
    return audioContextRef.current;
  }, []);

  const getGainNode = useCallback(() => {
    const audioContext = getAudioContext();
    if (!gainNodeRef.current) {
      gainNodeRef.current = audioContext.createGain();
      gainNodeRef.current.gain.setValueAtTime(1, audioContext.currentTime);
      gainNodeRef.current.connect(audioContext.destination);
    }
    return gainNodeRef.current;
  }, [getAudioContext]);

  const processAudioQueue = useCallback(async () => {
    // 在函数入口立即检查停止标记
    if (stopAllRef.current) {
      console.log("processAudioQueue: 检测到停止标记，清空队列并退出");
      audioQueueRef.current.length = 0;
      isPlayingRef.current = false;
      setVoiceState({ isSpeaking: false });
      return;
    }

    if (isPlayingRef.current || audioQueueRef.current.length === 0) {
      return;
    }

    isPlayingRef.current = true;
    setVoiceState({ isSpeaking: true });

    try {
      const audioContext = getAudioContext();
      const gainNode = getGainNode();
      // 确保恢复音量
      gainNode.gain.setValueAtTime(1, audioContext.currentTime);

      while (audioQueueRef.current.length > 0) {
        // 在获取音频数据前检查停止标记（关键修复点）
        if (stopAllRef.current) {
          console.log("播放循环开始前检测到停止信号，退出循环");
          audioQueueRef.current.length = 0;
          break;
        }

        const audioData = audioQueueRef.current.shift();
        if (!audioData) continue;

        // 获取数据后再次检查停止标记
        if (stopAllRef.current) {
          console.log("获取音频数据后检测到停止信号，退出循环");
          audioQueueRef.current.length = 0;
          break;
        }

        try {
          const audioBuffer = await audioContext.decodeAudioData(audioData.slice(0));

          // 解码完成后检查停止标记
          if (stopAllRef.current) {
            console.log("解码完成后检测到停止信号，退出循环");
            audioQueueRef.current.length = 0;
            break;
          }

          const source = audioContext.createBufferSource();
          currentSourceRef.current = source;
          source.buffer = audioBuffer;
          source.connect(gainNode);

          await new Promise<void>((resolve) => {
            source.onended = () => {
              if (currentSourceRef.current === source) currentSourceRef.current = null;
              resolve();
            };
            source.start();
          });

          // 播放完成后检查是否需要停止
          if (stopAllRef.current) {
            console.log("播放完成后收到停止信号");
            if (currentSourceRef.current) {
              try {
                currentSourceRef.current.stop();
              } catch (error) {
                // 忽略Already stopped错误
              }
              currentSourceRef.current = null;
            }
            audioQueueRef.current.length = 0;
            break;
          }
        } catch (decodeError) {
          console.error("解码音频数据失败:", decodeError);
        }
      }
    } catch (error) {
      console.error("处理音频队列失败:", error);
    } finally {
      isPlayingRef.current = false;
      setVoiceState({ isSpeaking: false });
      // 只有在未停止且队列不为空时才继续处理
      if (audioQueueRef.current.length > 0 && !stopAllRef.current) {
        setTimeout(() => processAudioQueue(), 50);
      }
    }
  }, [getAudioContext, setVoiceState]);

  // 播放音频流块（实时流式播放）
  const playAudioStreamChunk = useCallback(
    async (audioData: ArrayBuffer, _text?: string) => {
      try {
        // 如果正在停止，不接收新的音频块
        if (stopAllRef.current) {
          console.log("正在停止，忽略新的音频块");
          return;
        }

        if (!audioData || audioData.byteLength === 0) return;
        audioQueueRef.current.push(audioData);
        if (!isPlayingRef.current) {
          processAudioQueue();
        }
      } catch (error) {
        console.error("播放音频流块失败:", error);
      }
    },
    [processAudioQueue]
  );

  const playAudioResponse = useCallback(
    async (audioData: ArrayBuffer, text?: string, requestId?: string) => {
      try {
        // 方案3：如果这个音频不是当前活跃请求的，直接丢弃
        const currentRequestId = activeRequestIdRef.current;
        if (requestId && currentRequestId && requestId !== currentRequestId) {
          return;
        }

        // stopSpeaking 可能在 decode 过程中触发，因此这里和关键步骤都要检查
        if (stopAllRef.current) {
          return;
        }

        setVoiceState({ isSpeaking: true });

        if (audioData && audioData.byteLength > 0) {
          const audioContext = getAudioContext();
          const gainNode = getGainNode();

          if (stopAllRef.current) {
            return;
          }

          const audioBuffer = await audioContext.decodeAudioData(audioData.slice(0));

          if (stopAllRef.current) {
            return;
          }

          // 只有在确认要播放时，才恢复音量
          gainNode.gain.setValueAtTime(1, audioContext.currentTime);

          const source = audioContext.createBufferSource();
          currentSourceRef.current = source;
          source.buffer = audioBuffer;
          source.connect(gainNode);
          source.onended = () => {
            if (currentSourceRef.current === source) {
              currentSourceRef.current = null;
            }
            setVoiceState({ isSpeaking: false });
          };

          if (stopAllRef.current) {
            try {
              source.disconnect();
            } catch (_e) {
              // ignore
            }
            currentSourceRef.current = null;
            return;
          }

          source.start();
          return;
        }

        if (text && "speechSynthesis" in window) {
          if (stopAllRef.current) {
            return;
          }

          const settings = getSettings();
          const utterance = new SpeechSynthesisUtterance(text);
          utterance.lang = "zh-CN";
          utterance.rate = settings.voiceRate;
          utterance.pitch = settings.voicePitch;
          utterance.volume = 1.0;

          const voices = window.speechSynthesis.getVoices();
          const preferredVoice = voices.find(
            (voice) =>
              voice.lang.includes("zh") &&
              voice.name.includes(settings.voiceGender === "female" ? "Female" : "Male")
          );
          if (preferredVoice) {
            utterance.voice = preferredVoice;
          }

          utterance.onend = () => {
            setVoiceState({ isSpeaking: false });
          };
          utterance.onerror = () => {
            setVoiceState({ isSpeaking: false });
          };

          window.speechSynthesis.speak(utterance);
          return;
        }

        setVoiceState({ isSpeaking: false });
      } catch (error) {
        console.error("播放音频失败:", error);
        setVoiceState({ isSpeaking: false });
      }
    },
    [getAudioContext, getGainNode, setVoiceState]
  );

  const sendVoiceInput = useCallback((audioData: ArrayBuffer, language?: string) => {
    if (socketRef.current) {
      socketRef.current.sendVoiceInput(audioData, language);
    }
  }, []);

  const sendTextCommand = useCallback(
    (text: string) => {
      if (socketRef.current) {
        const requestId = generateRequestId();
        activeRequestIdRef.current = requestId;

        addMessage("user", text, { requestId });
        socketRef.current.sendTextCommand(text, requestId);
      }
    },
    [addMessage]
  );

  const confirmAction = useCallback(
    (confirmationId: string, approved: boolean) => {
      if (socketRef.current) {
        socketRef.current.confirmAction(confirmationId, approved);
        setSessionState({
          hasPendingConfirmation: false,
          hasPendingToolCall: false,
          isCanceled: false,
          confirmationRequest: undefined,
        });
      }
    },
    [setSessionState]
  );

  const cancel = useCallback(
    (silent: boolean = false) => {
      if (socketRef.current) {
        socketRef.current.cancel(silent);
        setSessionState({
          hasPendingConfirmation: false,
          hasPendingToolCall: false,
          isCanceled: true,
          confirmationRequest: undefined,
        });
      }
    },
    [setSessionState]
  );

  const getSystemInfo = useCallback(() => {
    socketRef.current?.getSystemInfo();
  }, []);

  const getSessionStatus = useCallback(() => {
    socketRef.current?.getSessionStatus();
  }, []);

  const getSessionHistory = useCallback((limit?: number) => {
    socketRef.current?.getSessionHistory(limit);
  }, []);

  const updateTTSSettings = useCallback((settings: any) => {
    socketRef.current?.updateTTSSettings(settings);
  }, []);

  const getTTSSettings = useCallback(() => {
    socketRef.current?.getTTSSettings();
  }, []);

  const getAvailableVoices = useCallback(() => {
    socketRef.current?.getAvailableVoices();
  }, []);
  const stopSpeaking = useCallback(() => {
    console.log("执行 stopSpeaking 操作");
    try {
      // 1. 首先设置停止标记，阻止后续播放
      stopAllRef.current = true;

      // 方案3：立刻切换到一个新的 requestId，让旧的音频/文本事件全部失效
      activeRequestIdRef.current = `canceled_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;

      // 2. 清空音频队列
      audioQueueRef.current.length = 0;
      console.log("已清空音频队列");

      // 3. 立即将音量闸降为0实现瞬时静音
      if (gainNodeRef.current) {
        try {
          const ctx = gainNodeRef.current.context;
          gainNodeRef.current.gain.setValueAtTime(0, ctx.currentTime);
          console.log("已拉低音量闸至0");
        } catch (error) {
          console.log("调整音量闸失败:", (error as any)?.message);
        }
      }

      // 4. 立即暂停音频上下文（这是最快的静音方式）
      if (audioContextRef.current && audioContextRef.current.state === "running") {
        console.log("立即暂停音频上下文");
        audioContextRef.current
          .suspend()
          .then(() => {
            console.log("音频上下文已暂停");
          })
          .catch((err) => {
            console.log("暂停音频上下文失败:", err);
          });
      }

      // 5. 停止当前播放的音频源
      if (currentSourceRef.current) {
        console.log("尝试停止当前音频源");
        try {
          // 先断开连接实现立即静音
          currentSourceRef.current.disconnect();
          currentSourceRef.current.stop(0);
          console.log("已停止音频源");
        } catch (error: any) {
          // 忽略Already stopped错误
          console.log("音频源停止错误:", error?.message);
        }
        currentSourceRef.current = null;
      }

      // 5. 关闭并重置音频上下文（确保下次能正常使用）
      if (audioContextRef.current) {
        console.log("关闭音频上下文");
        try {
          audioContextRef.current.close();
        } catch (error) {
          console.log("关闭音频上下文错误:", error);
        }
        audioContextRef.current = null;
        gainNodeRef.current = null;
        console.log("音频上下文已置空");
      }

      // 6. 停止Web Speech API
      if (typeof window !== "undefined" && "speechSynthesis" in window) {
        console.log("停止Web Speech API");
        window.speechSynthesis.cancel();
      }

      // 7. 通知后端停止TTS
      if (socketRef.current) {
        console.log("通知后端停止TTS");
        socketRef.current.stopTTS();
      }

      // 8. 重置播放状态
      isPlayingRef.current = false;
      setVoiceState({ isSpeaking: false });
      console.log("已重置播放状态");

      // 9. 延迟重置停止标记，确保后端停止发送音频
      setTimeout(() => {
        stopAllRef.current = false;
        console.log("已重置停止标记");
      }, 1500);
    } catch (error) {
      console.error("停止播放时发生错误:", error);
      // 即使出错也要重置状态
      isPlayingRef.current = false;
      setVoiceState({ isSpeaking: false });
      setTimeout(() => {
        stopAllRef.current = false;
      }, 500);
    }
  }, [setVoiceState]);

  const stopMusic = useCallback(() => {
    if (socketRef.current?.stopMusic) {
      socketRef.current.stopMusic();
    }
  }, []);

  return {
    isInitialized,
    sendVoiceInput,
    sendTextCommand,
    confirmAction,
    cancel,
    getSystemInfo,
    getSessionStatus,
    getSessionHistory,
    updateTTSSettings,
    getTTSSettings,
    getAvailableVoices,
    stopSpeaking,
    stopMusic,
  };
}
