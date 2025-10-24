import { useEffect, useRef, useState } from "react";
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
          // 播放语音响应，传递文本给Web Speech API
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
  }, [
    addMessage,
    confirmAction, // 播放语音响应，传递文本给Web Speech API
    playAudioResponse, // 实时播放音频流块
    playAudioStreamChunk,
    setConnectionState,
    setSessionState,
    setVoiceState,
  ]);

  // 音频上下文缓存，避免重复创建
  const audioContextRef = useRef<AudioContext | null>(null);
  const audioQueueRef = useRef<ArrayBuffer[]>([]);
  const isPlayingRef = useRef(false);

  // 获取或创建音频上下文
  const getAudioContext = () => {
    if (!audioContextRef.current) {
      audioContextRef.current = new (window.AudioContext || (window as any).webkitAudioContext)();
    }
    return audioContextRef.current;
  };

  // 播放音频流块（实时流式播放）
  const playAudioStreamChunk = async (audioData: ArrayBuffer, _text?: string) => {
    try {
      if (!audioData || audioData.byteLength === 0) return;

      // 将音频数据加入队列
      audioQueueRef.current.push(audioData);

      // 如果当前没有在播放，开始播放队列
      if (!isPlayingRef.current) {
        processAudioQueue();
      }
    } catch (error) {
      console.error("播放音频流块失败:", error);
    }
  };

  // 处理音频播放队列
  const processAudioQueue = async () => {
    if (isPlayingRef.current || audioQueueRef.current.length === 0) {
      return;
    }

    isPlayingRef.current = true;
    setVoiceState({ isSpeaking: true });

    try {
      const audioContext = getAudioContext();

      while (audioQueueRef.current.length > 0) {
        const audioData = audioQueueRef.current.shift();
        if (!audioData) continue;

        try {
          // 解码音频数据
          const audioBuffer = await audioContext.decodeAudioData(audioData.slice(0));

          // 创建音频源
          const source = audioContext.createBufferSource();
          source.buffer = audioBuffer;
          source.connect(audioContext.destination);

          // 等待当前音频播放完成
          await new Promise<void>((resolve) => {
            source.onended = () => {
              resolve();
            };
            source.start();
          });
        } catch (decodeError) {
          console.error("解码音频数据失败:", decodeError);
        }
      }
    } catch (error) {
      console.error("处理音频队列失败:", error);
    } finally {
      isPlayingRef.current = false;
      setVoiceState({ isSpeaking: false });

      // 如果队列中还有数据，继续处理
      if (audioQueueRef.current.length > 0) {
        setTimeout(() => processAudioQueue(), 50); // 稍微延迟，避免重叠
      }
    }
  };

  const playAudioResponse = async (audioData: ArrayBuffer, text?: string) => {
    try {
      setVoiceState({ isSpeaking: true });

      // 优先使用后端返回的音频数据
      if (audioData && audioData.byteLength > 0) {
        console.log(`播放后端返回的音频数据，长度: ${audioData.byteLength} bytes`);
        const audioContext = new (window.AudioContext || (window as any).webkitAudioContext)();
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

      // 如果没有音频数据，尝试使用Web Speech API进行语音合成
      if (text && "speechSynthesis" in window) {
        console.log("使用Web Speech API进行语音合成");
        const settings = getSettings();
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.lang = "zh-CN";
        utterance.rate = settings.voiceRate;
        utterance.pitch = settings.voicePitch;
        utterance.volume = 1.0;

        // 设置语音音色
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

        utterance.onerror = (event) => {
          console.error("语音合成失败:", event);
          setVoiceState({ isSpeaking: false });
        };

        window.speechSynthesis.speak(utterance);
        return;
      }

      // 如果都没有，直接设置状态
      console.log("没有音频数据或文本，无法播放");
      setVoiceState({ isSpeaking: false });
    } catch (error) {
      console.error("播放音频失败:", error);
      setVoiceState({ isSpeaking: false });
    }
  };

  const sendVoiceInput = (audioData: ArrayBuffer, language?: string) => {
    if (socketRef.current) {
      socketRef.current.sendVoiceInput(audioData, language);
    }
  };

  const sendTextCommand = (text: string) => {
    if (socketRef.current) {
      // 添加用户消息到历史记录
      addMessage("user", text);
      socketRef.current.sendTextCommand(text);
    }
  };

  const confirmAction = (confirmationId: string, approved: boolean) => {
    if (socketRef.current) {
      socketRef.current.confirmAction(confirmationId, approved);
      // 清除确认状态
      setSessionState({
        hasPendingConfirmation: false,
        hasPendingToolCall: false,
        isCanceled: false,
        confirmationRequest: undefined,
      });
    }
  };

  const cancel = () => {
    if (socketRef.current) {
      socketRef.current.cancel();
      // 清除确认状态
      setSessionState({
        hasPendingConfirmation: false,
        hasPendingToolCall: false,
        isCanceled: true,
        confirmationRequest: undefined,
      });
    }
  };

  const getSystemInfo = () => {
    if (socketRef.current) {
      socketRef.current.getSystemInfo();
    }
  };

  const getSessionStatus = () => {
    if (socketRef.current) {
      socketRef.current.getSessionStatus();
    }
  };

  const getSessionHistory = (limit?: number) => {
    if (socketRef.current) {
      socketRef.current.getSessionHistory(limit);
    }
  };

  const updateTTSSettings = (settings: any) => {
    if (socketRef.current) {
      socketRef.current.updateTTSSettings(settings);
    }
  };

  const getTTSSettings = () => {
    if (socketRef.current) {
      socketRef.current.getTTSSettings();
    }
  };

  const getAvailableVoices = () => {
    if (socketRef.current) {
      socketRef.current.getAvailableVoices();
    }
  };

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
  };
}
