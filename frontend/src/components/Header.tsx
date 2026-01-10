import { Settings, Square, Trash2, Wifi, WifiOff } from "lucide-react";
import { useState } from "react";
import { useSocketContext } from "../hooks/SocketProvider";
import { useApp } from "../hooks/useApp";
import { SettingsModal } from "./SettingsModal";

export function Header() {
  const { state, clearMessages } = useApp();
  const { cancel, stopSpeaking } = useSocketContext();
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);

  const handleClearMessages = () => {
    clearMessages();
  };

  // 刷新系统信息与新建对话功能暂时不需要，先保留代码入口占位
  // const handleRefreshSystem = () => {
  //   getSystemInfo();
  // };

  const handleOpenSettings = () => {
    setIsSettingsOpen(true);
  };

  const handleCloseSettings = () => {
    setIsSettingsOpen(false);
  };

  const handleStopAll = async () => {
    await cancel(true);
    await stopSpeaking();
  };

  // const handleNewSession = () => {
  //   clearSession();
  //   clearMessages();
  //   setTimeout(() => getSessionHistory(50), 200);
  // };

  return (
    <header className="sticky top-0 z-50 bg-white/90 backdrop-blur border-b border-gray-200 px-4 py-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-bold text-gray-900">智能语音助手</h1>

          {/* 连接状态指示器 */}
          <div className="flex items-center gap-2">
            {state.connectionState.connected ? (
              <div className="flex items-center gap-1 text-green-600">
                <Wifi size={16} />
                <span className="text-xs font-medium">已连接</span>
              </div>
            ) : (
              <div className="flex items-center gap-1 text-red-600">
                <WifiOff size={16} />
                <span className="text-xs font-medium">
                  {state.connectionState.connecting ? "连接中..." : "未连接"}
                </span>
              </div>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2">
          {/* 语音状态指示器 */}
          <div className="flex items-center gap-2 mr-4">
            {state.voiceState.isListening && (
              <div className="flex items-center gap-1 text-red-600">
                <div className="w-2 h-2 bg-red-500 rounded-full animate-pulse"></div>
                <span className="text-xs">录音中</span>
              </div>
            )}

            {state.voiceState.isSpeaking && (
              <div className="flex items-center gap-1 text-blue-600">
                <div className="w-2 h-2 bg-blue-500 rounded-full animate-pulse"></div>
                <span className="text-xs">播放中</span>
              </div>
            )}
          </div>

          {/* 停止按钮 */}
          <button
            type="button"
            onClick={handleStopAll}
            className="p-2 text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded-lg transition-colors"
            title="停止"
          >
            <Square size={18} />
          </button>

          {/* 设置 */}
          <button
            type="button"
            onClick={handleOpenSettings}
            className="p-2 text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded-lg transition-colors"
            title="设置"
          >
            <Settings size={18} />
          </button>

          {/* 刷新系统信息（暂时注释掉） */}
          {/*
          <button
            type="button"
            onClick={handleRefreshSystem}
            className="p-2 text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded-lg transition-colors"
            title="刷新系统信息"
          >
            <RefreshCw size={18} />
          </button>
          */}

          {/* 新建对话（暂时注释掉） */}
          {/*
          <button
            type="button"
            onClick={handleNewSession}
            className="p-2 text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded-lg transition-colors"
            title="新建对话"
          >
            新建
          </button>
          */}

          {/* 清空对话 */}
          <button
            type="button"
            onClick={handleClearMessages}
            className="p-2 text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded-lg transition-colors"
            title="清空对话"
          >
            <Trash2 size={18} />
          </button>
        </div>
      </div>

      {/* 错误提示 */}
      {state.connectionState.error && (
        <div className="mt-2 text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
          {state.connectionState.error}
        </div>
      )}

      {/* 设置模态框 */}
      <SettingsModal isOpen={isSettingsOpen} onClose={handleCloseSettings} />
    </header>
  );
}
