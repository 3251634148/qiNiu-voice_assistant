import { useEffect, useRef, useState, useCallback } from "react";
import { getSettings } from "../components/SettingsModal";
import type { ActionResult, ConfirmationRequest, Message, ToolResult } from "../types";
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
          addMessage(data.type as any, data.content, data.metadata);
        });

        socket.onAudioResponse((data) => {
          if (stopAllRef.current) return;
          playAudioResponse(data.audioData, data.text);
        });

        // 监听音频流块事件
        socket.onAudioChunk((data) => {
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
  const audioQueueRef = useRef<ArrayBuffer[]>([]);
  const isPlayingRef = useRef(false);
  const currentSourceRef = useRef<AudioBufferSourceNode | null>(null);
  const stopAllRef = useRef(false);

  // 获取或创建音频上下文
  const getAudioContext = useCallback(() => {
    if (!audioContextRef.current) {
      audioContextRef.current = new (window.AudioContext || (window as any).webkitAudioContext)();
    }
    return audioContextRef.current;
  }, []);

  const processAudioQueue = useCallback(async () => {
    if (isPlayingRef.current || audioQueueRef.current.length === 0) {
      return;
    }
    if (stopAllRef.current) {
      audioQueueRef.current.length = 0;
      return;
    }

    isPlayingRef.current = true;
    setVoiceState({ isSpeaking: true });

    try {
      const audioContext = getAudioContext();

      while (audioQueueRef.current.length > 0 && !stopAllRef.current) {
        const audioData = audioQueueRef.current.shift();
        if (!audioData) continue;

        try {
          const audioBuffer = await audioContext.decodeAudioData(audioData.slice(0));
          const source = audioContext.createBufferSource();
          currentSourceRef.current = source;
          source.buffer = audioBuffer;
          source.connect(audioContext.destination);

          await new Promise<void>((resolve) => {
            source.onended = () => {
              if (currentSourceRef.current === source) currentSourceRef.current = null;
              resolve();
            };
            source.start();
          });

          // 在每次播放开始后检查是否需要停止
          if (stopAllRef.current) {
            console.log("播放期间收到停止信号");
            if (currentSourceRef.current) {
              try {
                currentSourceRef.current.stop();
              } catch (error) {
                // 忽略Already stopped错误
              }
              currentSourceRef.current = null;
            }
            // 清空队列
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
    async (audioData: ArrayBuffer, text?: string) => {
      try {
        setVoiceState({ isSpeaking: true });
        if (audioData && audioData.byteLength > 0) {
          const audioContext = getAudioContext();
          const audioBuffer = await audioContext.decodeAudioData(audioData.slice(0));
          const source = audioContext.createBufferSource();
          source.buffer = audioBuffer;
          source.connect(audioContext.destination);
          source.onended = () => {
            setVoiceState({ isSpeaking: false });
          };
          source.start();
          return;
        }
        if (text && "speechSynthesis" in window) {
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
    [getAudioContext, setVoiceState]
  );

  const sendVoiceInput = useCallback((audioData: ArrayBuffer, language?: string) => {
    if (socketRef.current) {
      socketRef.current.sendVoiceInput(audioData, language);
    }
  }, []);

  const sendTextCommand = useCallback(
    (text: string) => {
      if (socketRef.current) {
        addMessage("user", text);
        socketRef.current.sendTextCommand(text);
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
      // 设置停止标记，阻止后续播放
      stopAllRef.current = true;

      // 清空音频队列
      audioQueueRef.current.length = 0;
      console.log("已清空音频队列");

      // 强制停止当前播放的音频源
      if (currentSourceRef.current) {
        console.log("尝试停止当前音频源");
        try {
          // 使用立即停止的方式
          currentSourceRef.current.stop(0);
          console.log("已停止音频源");
        } catch (error) {
          // 忽略Already stopped错误
          console.log("音频源停止错误:", error.message);
        }
        currentSourceRef.current = null;
      }

      // 关闭整个音频上下文
      if (audioContextRef.current && audioContextRef.current.state === "running") {
        console.log("关闭音频上下文");
        try {
          // 不等待close操作完成，直接置空
          audioContextRef.current.close();
        } catch (error) {
          console.log("关闭音频上下文错误:", error);
        }
        audioContextRef.current = null;
        console.log("音频上下文已置空");
      }

      // 停止Web Speech API
      if (typeof window !== "undefined" && "speechSynthesis" in window) {
        console.log("停止Web Speech API");
        window.speechSynthesis.cancel();
      }

      // 重置播放状态
      isPlayingRef.current = false;
      setVoiceState({ isSpeaking: false });
      console.log("已重置播放状态");

      // 重置停止标记，延迟重置以防止立即重启
      setTimeout(() => {
        stopAllRef.current = false;
        console.log("已重置停止标记");
      }, 200);
    } catch (error) {
      console.error("停止播放时发生错误:", error);
      // 即使出错也要重置状态
      isPlayingRef.current = false;
      setVoiceState({ isSpeaking: false });
      stopAllRef.current = false;
    }
  }, [setVoiceState]);

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
  };
}
