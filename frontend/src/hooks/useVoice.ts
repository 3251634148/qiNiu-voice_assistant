import { useCallback, useRef, useState } from "react";
import { useApp } from "./useApp";

interface UseVoiceReturn {
  isRecording: boolean;
  mediaRecorder: MediaRecorder | null;
  startRecording: () => Promise<void>;
  stopRecording: () => void;
  toggleRecording: () => Promise<void>;
}

export function useVoice(
  onTextCommand?: (text: string) => void,
  _sendVoiceInput?: (audioData: ArrayBuffer, language?: string) => void
): UseVoiceReturn {
  const { state } = useApp();
  const [isRecording, setIsRecording] = useState(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const _audioChunksRef = useRef<Blob[]>([]);
  const recognitionCallbackRef = useRef<((text: string) => void) | null>(null);
  const finalTranscriptRef = useRef<string>("");

  // Web Speech API识别函数
  const useWebSpeechRecognition = async (callback?: (text: string) => void) => {
    return new Promise<void>((resolve, reject) => {
      if ("webkitSpeechRecognition" in window || "SpeechRecognition" in window) {
        const SpeechRecognition =
          (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
        const recognition = new SpeechRecognition();

        recognition.lang = "zh-CN";
        recognition.continuous = true; // 启用持续监听
        recognition.interimResults = true; // 启用中间结果
        recognition.maxAlternatives = 1;

        let hasRecognized = false;
        finalTranscriptRef.current = "";
        recognitionCallbackRef.current = callback || null;

        recognition.onresult = (event: any) => {
          let interimTranscript = "";

          for (let i = event.resultIndex; i < event.results.length; i++) {
            const result = event.results[i];
            if (result.isFinal) {
              finalTranscriptRef.current += result[0].transcript;
            } else {
              interimTranscript += result[0].transcript;
            }
          }

          // 如果有中间结果，显示出来（可选）
          if (interimTranscript) {
            console.log("中间识别结果:", interimTranscript);
          }

          // 如果有最终结果，保存起来
          if (finalTranscriptRef.current) {
            console.log("累积识别结果:", finalTranscriptRef.current);
          }
        };

        recognition.onerror = (event: any) => {
          console.error("Web Speech API识别失败:", event.error);
          let fallbackText = "语音识别失败，请使用文本输入";

          switch (event.error) {
            case "no-speech":
              fallbackText = "未检测到语音，请重试";
              break;
            case "audio-capture":
              fallbackText = "无法访问麦克风，请检查权限设置";
              break;
            case "not-allowed":
              fallbackText = "麦克风权限被拒绝，请允许访问";
              break;
            case "network":
              fallbackText = "网络连接错误，请检查网络";
              break;
            default:
              fallbackText = "语音识别失败，请使用文本输入";
          }

          if (recognitionCallbackRef.current && !hasRecognized) {
            hasRecognized = true;
            recognitionCallbackRef.current(fallbackText);
          }
          reject(new Error(`Speech recognition error: ${event.error}`));
        };

        recognition.onend = () => {
          console.log("Web Speech API识别结束");

          // 当识别结束时，如果有累积的文本，发送它
          if (finalTranscriptRef.current.trim() && !hasRecognized) {
            hasRecognized = true;
            if (recognitionCallbackRef.current) {
              recognitionCallbackRef.current(finalTranscriptRef.current.trim());
            }
            resolve();
            return;
          }

          // 如果还没有识别到结果且仍在录音状态，继续监听
          if (!hasRecognized && isRecording) {
            try {
              setTimeout(() => {
                if (isRecording && !hasRecognized) {
                  recognition.start();
                }
              }, 100);
            } catch (error) {
              console.error("重新启动语音识别失败:", error);
              if (!hasRecognized) {
                // 如果重新启动失败且有中间结果，使用中间结果
                if (finalTranscriptRef.current.trim() && recognitionCallbackRef.current) {
                  hasRecognized = true;
                  recognitionCallbackRef.current(finalTranscriptRef.current.trim());
                } else if (recognitionCallbackRef.current) {
                  recognitionCallbackRef.current("未识别到有效语音，请重试");
                }
                resolve();
              }
            }
          } else {
            // 如果手动停止但没有最终结果，尝试使用中间结果
            if (
              !hasRecognized &&
              finalTranscriptRef.current.trim() &&
              recognitionCallbackRef.current
            ) {
              hasRecognized = true;
              recognitionCallbackRef.current(finalTranscriptRef.current.trim());
            } else if (!hasRecognized && recognitionCallbackRef.current) {
              recognitionCallbackRef.current("未识别到有效语音，请重试");
            }
            resolve();
          }
        };

        // 保存recognition实例以便手动停止
        (window as any).currentRecognition = recognition;
        recognition.start();
      } else {
        console.warn("浏览器不支持Web Speech API");
        const fallbackText = "浏览器不支持语音识别，请使用文本输入";
        if (callback) {
          callback(fallbackText);
        }
        reject(new Error("Web Speech API not supported"));
      }
    });
  };

  const startRecording = useCallback(async () => {
    try {
      // 直接使用Web Speech API，无需录音
      console.log("开始语音识别...");
      setIsRecording(true);

      try {
        await useWebSpeechRecognition((recognizedText) => {
          console.log("语音识别结果:", recognizedText);
          // 将识别的文本发送到后端处理
          if (onTextCommand && typeof onTextCommand === "function") {
            onTextCommand(recognizedText);
          }
          // 识别完成后自动停止录音
          setIsRecording(false);
        });
      } catch (error) {
        console.error("Web Speech API识别失败:", error);
        // 如果Web Speech API失败，提示用户使用文本输入
        if (onTextCommand && typeof onTextCommand === "function") {
          const fallbackText = "语音识别失败，请使用文本输入";
          onTextCommand(fallbackText);
        }
        setIsRecording(false);
      }
    } catch (error) {
      console.error("开始语音识别失败:", error);
      setIsRecording(false);
      throw new Error("无法启动语音识别");
    }
  }, [onTextCommand, useWebSpeechRecognition]);

  const stopRecording = useCallback(() => {
    console.log("手动停止语音识别");

    // 停止Web Speech API
    const currentRecognition = (window as any).currentRecognition;
    if (currentRecognition) {
      try {
        // 在停止前，如果有累积的文本，先发送它
        if (finalTranscriptRef.current.trim() && recognitionCallbackRef.current) {
          console.log("手动停止时发送累积的识别结果:", finalTranscriptRef.current.trim());
          recognitionCallbackRef.current(finalTranscriptRef.current.trim());
          finalTranscriptRef.current = "";
          recognitionCallbackRef.current = null;
        }

        currentRecognition.stop();

        // 清理引用
        setTimeout(() => {
          (window as any).currentRecognition = null;
        }, 500);
      } catch (error) {
        console.error("停止语音识别失败:", error);
        (window as any).currentRecognition = null;
      }
    }
    setIsRecording(false);
  }, []);

  const toggleRecording = useCallback(async () => {
    if (isRecording) {
      stopRecording();
    } else {
      await startRecording();
    }
  }, [isRecording, startRecording, stopRecording]);

  return {
    isRecording,
    mediaRecorder: mediaRecorderRef.current,
    startRecording,
    stopRecording,
    toggleRecording,
  };
}
