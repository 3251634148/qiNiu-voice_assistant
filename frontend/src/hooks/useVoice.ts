import { useCallback, useRef, useState } from "react";

interface UseVoiceReturn {
  isRecording: boolean;
  mediaRecorder: MediaRecorder | null;
  startRecording: () => Promise<void>;
  stopRecording: () => void;
  toggleRecording: () => Promise<void>;
}

const DEFAULT_LANGUAGE = "zh-CN";
const STOP_BUFFER_MS = 600;

export function useVoice(
  onTextCommand?: (text: string) => void,
  sendVoiceInput?: (audioData: ArrayBuffer, language?: string) => void
): UseVoiceReturn {
  const [isRecording, setIsRecording] = useState(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const stopTimerRef = useRef<number | null>(null);

  const cleanupMedia = useCallback(() => {
    if (stopTimerRef.current) {
      window.clearTimeout(stopTimerRef.current);
      stopTimerRef.current = null;
    }

    if (mediaRecorderRef.current) {
      mediaRecorderRef.current = null;
    }

    if (streamRef.current) {
      for (const track of streamRef.current.getTracks()) {
        track.stop();
      }
      streamRef.current = null;
    }

    audioChunksRef.current = [];
  }, []);

  const pickSupportedMimeType = () => {
    const candidates = [
      "audio/webm;codecs=opus",
      "audio/webm",
      // Safari may support mp4
      "audio/mp4",
    ];

    if (typeof MediaRecorder === "undefined" || typeof MediaRecorder.isTypeSupported !== "function") {
      return "";
    }

    for (const t of candidates) {
      if (MediaRecorder.isTypeSupported(t)) {
        return t;
      }
    }

    return "";
  };

  const startBackendRecording = useCallback(async () => {
    if (!sendVoiceInput) {
      throw new Error("sendVoiceInput 未提供");
    }

    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error("浏览器不支持麦克风录音");
    }

    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    streamRef.current = stream;

    const mimeType = pickSupportedMimeType();
    const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);

    audioChunksRef.current = [];
    mediaRecorderRef.current = recorder;

    recorder.ondataavailable = (event: BlobEvent) => {
      if (event.data && event.data.size > 0) {
        audioChunksRef.current.push(event.data);
      }
    };

    recorder.onerror = (event: Event) => {
      console.error("录音失败:", event);
    };

    recorder.onstop = async () => {
      try {
        const blobType = recorder.mimeType || mimeType || "audio/webm";
        const audioBlob = new Blob(audioChunksRef.current, { type: blobType });
        const buffer = await audioBlob.arrayBuffer();

        // 交给后端做 ASR：后端会发回 speech-recognized，并继续走 text-command
        sendVoiceInput(buffer, DEFAULT_LANGUAGE);
      } catch (error) {
        console.error("发送语音到后端失败:", error);

        // 后端模式失败时，尽量回退到文本提示，不阻塞用户
        if (onTextCommand) {
          onTextCommand("语音识别失败，请使用文本输入");
        }
      } finally {
        cleanupMedia();
        setIsRecording(false);
      }
    };

    // timeslice 让浏览器持续产出数据，减少 stop 时丢尾巴的概率
    recorder.start(250);
    setIsRecording(true);
  }, [cleanupMedia, onTextCommand, sendVoiceInput]);

  // Web Speech API 识别（兜底）
  const startWebSpeechRecognition = useCallback(async () => {
    return new Promise<void>((resolve, reject) => {
      if (!("webkitSpeechRecognition" in window) && !("SpeechRecognition" in window)) {
        reject(new Error("Web Speech API not supported"));
        return;
      }

      const SpeechRecognition =
        (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
      const recognition = new SpeechRecognition();

      recognition.lang = DEFAULT_LANGUAGE;
      recognition.continuous = true;
      recognition.interimResults = true;
      recognition.maxAlternatives = 1;

      let finalTranscript = "";
      let hasRecognized = false;

      recognition.onresult = (event: any) => {
        for (let i = event.resultIndex; i < event.results.length; i++) {
          const result = event.results[i];
          if (result.isFinal) {
            finalTranscript += result[0].transcript;
          }
        }
      };

      recognition.onerror = (event: any) => {
        console.error("Web Speech API识别失败:", event.error);
        if (!hasRecognized && onTextCommand) {
          hasRecognized = true;
          onTextCommand("语音识别失败，请使用文本输入");
        }
        reject(new Error(`Speech recognition error: ${event.error}`));
      };

      recognition.onend = () => {
        if (!hasRecognized && onTextCommand) {
          hasRecognized = true;
          const text = finalTranscript.trim();
          onTextCommand(text || "未识别到有效语音，请重试");
        }
        resolve();
      };

      (window as any).currentRecognition = recognition;
      recognition.start();
      setIsRecording(true);
    });
  }, [onTextCommand]);

  const startRecording = useCallback(async () => {
    try {
      // 默认优先走后端 ASR（千问 Audio），失败再回退 Web Speech
      if (sendVoiceInput) {
        await startBackendRecording();
        return;
      }

      await startWebSpeechRecognition();
    } catch (error) {
      console.error("开始语音输入失败:", error);
      setIsRecording(false);
      cleanupMedia();
      throw error;
    }
  }, [cleanupMedia, sendVoiceInput, startBackendRecording, startWebSpeechRecognition]);

  const stopRecording = useCallback(() => {
    // 后端录音模式：给 stop 留一点缓冲，减少截断
    const recorder = mediaRecorderRef.current;
    if (recorder && recorder.state !== "inactive") {
      if (stopTimerRef.current) {
        window.clearTimeout(stopTimerRef.current);
      }

      stopTimerRef.current = window.setTimeout(() => {
        try {
          recorder.requestData();
          recorder.stop();
        } catch (error) {
          console.error("停止录音失败:", error);
          cleanupMedia();
          setIsRecording(false);
        }
      }, STOP_BUFFER_MS);

      return;
    }

    // Web Speech API 模式：直接 stop，让浏览器触发 onend 发送最终结果
    const currentRecognition = (window as any).currentRecognition;
    if (currentRecognition) {
      try {
        currentRecognition.stop();
      } catch (error) {
        console.error("停止语音识别失败:", error);
      } finally {
        (window as any).currentRecognition = null;
      }
    }

    setIsRecording(false);
  }, [cleanupMedia]);

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
