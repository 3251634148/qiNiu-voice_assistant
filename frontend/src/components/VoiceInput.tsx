import { Mic, MicOff, Send } from "lucide-react";
import React from "react";
import { useSocket } from "../hooks/useSocket";
import { useVoice } from "../hooks/useVoice";

interface VoiceInputProps {
  disabled?: boolean;
}

export function VoiceInput({ disabled = false }: VoiceInputProps) {
  const { sendTextCommand, sendVoiceInput, cancel, stopSpeaking } = useSocket();
  const { isRecording, toggleRecording } = useVoice(async (text) => {
    await cancel(true);
    await stopSpeaking();
    sendTextCommand(text);
  }, sendVoiceInput);
  const [textInput, setTextInput] = React.useState("");
  const [isSubmitting, setIsSubmitting] = React.useState(false);

  const handleTextSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!textInput.trim() || isSubmitting) return;

    setIsSubmitting(true);
    try {
      await cancel(true);
      await stopSpeaking();
      sendTextCommand(textInput);
      setTextInput("");
    } catch (error) {
      console.error("发送文本命令失败:", error);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleVoiceToggle = async () => {
    try {
      if (!isRecording) {
        await cancel(true);
        await stopSpeaking();
      }
      await toggleRecording();
    } catch (error) {
      console.error("语音录制失败:", error);
    }
  };

  return (
    <div className="bg-white border-t border-gray-200 p-4">
      <form onSubmit={handleTextSubmit} className="flex gap-3">
        <button
          type="button"
          onClick={handleVoiceToggle}
          disabled={disabled}
          className={`
            p-3 rounded-full transition-all duration-200 flex-shrink-0
            ${
              isRecording
                ? "bg-red-500 hover:bg-red-600 text-white animate-pulse"
                : "bg-gray-100 hover:bg-gray-200 text-gray-700"
            }
            ${disabled ? "opacity-50 cursor-not-allowed" : "cursor-pointer"}
          `}
          title={isRecording ? "停止录音" : "开始录音"}
        >
          {isRecording ? <MicOff size={20} /> : <Mic size={20} />}
        </button>

        <input
          type="text"
          value={textInput}
          onChange={(e) => setTextInput(e.target.value)}
          placeholder="输入指令或点击麦克风说话..."
          disabled={disabled || isSubmitting}
          className="flex-1 px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent disabled:opacity-50"
        />

        <button
          type="submit"
          disabled={!textInput.trim() || disabled || isSubmitting}
          className="btn-primary flex-shrink-0 disabled:opacity-50 disabled:cursor-not-allowed"
          title="发送指令"
        >
          <Send size={20} />
        </button>
      </form>

      {isRecording && (
        <div className="mt-2 text-sm text-red-600 flex items-center gap-2">
          <div className="w-2 h-2 bg-red-500 rounded-full animate-pulse"></div>
          正在录音...
        </div>
      )}
    </div>
  );
}
