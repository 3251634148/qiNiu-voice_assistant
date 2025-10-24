import { io, type Socket } from "socket.io-client";
import type { ActionResult, ConfirmationRequest, Message, SystemInfo, ToolResult } from "../types";

class SocketService {
  private socket: Socket | null = null;
  private serverUrl: string;
  private isConnecting: boolean = false;
  private connectionPromise: Promise<void> | null = null;

  constructor(serverUrl: string = "http://localhost:3001") {
    this.serverUrl = serverUrl;
  }

  connect(): Promise<void> {
    // 如果已经连接，直接返回成功
    if (this.socket?.connected) {
      console.log("Socket已连接，跳过重复连接");
      return Promise.resolve();
    }

    // 如果正在连接，返回现有的连接Promise
    if (this.isConnecting && this.connectionPromise) {
      console.log("Socket正在连接中，等待现有连接");
      return this.connectionPromise;
    }

    // 如果socket已存在但未连接，先断开清理
    if (this.socket && !this.socket.connected) {
      this.socket.disconnect();
      this.socket = null;
    }

    this.isConnecting = true;

    this.connectionPromise = new Promise((resolve, reject) => {
      this.socket = io(this.serverUrl, {
        reconnection: true,
        reconnectionAttempts: 3, // 限制重连次数
        reconnectionDelay: 1000, // 重连延迟
        reconnectionDelayMax: 5000, // 最大重连延迟
        timeout: 10000, // 连接超时时间
      });

      this.socket.on("connect", () => {
        console.log("已连接到服务器");
        this.isConnecting = false;
        resolve();
      });

      this.socket.on("connect_error", (error) => {
        console.error("连接失败:", error);
        this.isConnecting = false;
        this.connectionPromise = null;
        reject(error);
      });

      this.socket.on("disconnect", (reason) => {
        console.log("与服务器断开连接，原因:", reason);
        if (reason === "io server disconnect") {
          // 服务器主动断开，需要手动重连
          this.socket?.connect();
        }
      });

      this.socket.on("reconnect_attempt", (attemptNumber) => {
        console.log(`第 ${attemptNumber} 次重连尝试`);
      });

      this.socket.on("reconnect_failed", () => {
        console.error("重连失败，请检查服务器状态");
      });
    });

    return this.connectionPromise;
  }

  disconnect() {
    if (this.socket) {
      this.socket.disconnect();
      this.socket = null;
    }
    this.isConnecting = false;
    this.connectionPromise = null;
  }

  // 发送语音数据
  sendVoiceInput(audioData: ArrayBuffer, language: string = "zh-CN") {
    if (!this.socket) {
      throw new Error("未连接到服务器");
    }
    this.socket.emit("voice-input", { audioData, language });
  }

  // 发送文本命令
  sendTextCommand(text: string) {
    if (!this.socket) {
      throw new Error("未连接到服务器");
    }
    this.socket.emit("text-command", { text });
  }

  // 获取系统信息
  getSystemInfo() {
    if (!this.socket) {
      throw new Error("未连接到服务器");
    }
    this.socket.emit("get-system-info");
  }

  // 确认操作
  confirmAction(confirmationId: string, approved: boolean) {
    if (!this.socket) {
      throw new Error("未连接到服务器");
    }
    this.socket.emit("confirm-action", { confirmationId, approved });
  }

  // 取消操作
  cancel() {
    if (!this.socket) {
      throw new Error("未连接到服务器");
    }
    this.socket.emit("cancel");
  }

  // 获取会话状态
  getSessionStatus() {
    if (!this.socket) {
      throw new Error("未连接到服务器");
    }
    this.socket.emit("get-session-status");
  }

  // 获取会话历史
  getSessionHistory(limit?: number) {
    if (!this.socket) {
      throw new Error("未连接到服务器");
    }
    this.socket.emit("get-session-history", { limit });
  }

  // 更新TTS设置
  updateTTSSettings(settings: any) {
    if (!this.socket) {
      throw new Error("未连接到服务器");
    }
    this.socket.emit("update-tts-settings", settings);
  }

  // 获取TTS设置
  getTTSSettings() {
    if (!this.socket) {
      throw new Error("未连接到服务器");
    }
    this.socket.emit("get-tts-settings");
  }

  // 获取可用音色
  getAvailableVoices() {
    if (!this.socket) {
      throw new Error("未连接到服务器");
    }
    this.socket.emit("get-available-voices");
  }

  // 事件监听器
  onSpeechRecognized(callback: (data: { text: string }) => void) {
    this.socket?.on("speech-recognized", callback);
  }

  onAssistantMessage(
    callback: (data: {
      type: string;
      content: string;
      timestamp?: string;
      toolCall?: any;
      metadata?: any;
    }) => void
  ) {
    this.socket?.on("assistant-message", callback);
  }

  onAudioResponse(callback: (data: { audioData: ArrayBuffer; text?: string }) => void) {
    this.socket?.on("audio-response", callback);
  }

  onToolResult(callback: (result: ToolResult) => void) {
    this.socket?.on("tool-result", callback);
  }

  onRequestConfirmation(callback: (confirmation: ConfirmationRequest) => void) {
    this.socket?.on("request-confirmation", callback);
  }

  onSessionStatus(callback: (status: any) => void) {
    this.socket?.on("session-status", callback);
  }

  onSessionHistory(callback: (history: Message[]) => void) {
    this.socket?.on("session-history", callback);
  }

  onActionResult(callback: (result: ActionResult) => void) {
    this.socket?.on("action-result", callback);
  }

  onSystemInfo(callback: (info: SystemInfo) => void) {
    this.socket?.on("system-info", callback);
  }

  onError(callback: (error: { message: string }) => void) {
    this.socket?.on("error", callback);
  }

  onDisconnect(callback: () => void) {
    this.socket?.on("disconnect", callback);
  }

  // TTS相关事件监听器
  onTTSSettingsUpdated(
    callback: (result: { success: boolean; settings?: any; error?: string }) => void
  ) {
    this.socket?.on("tts-settings-updated", callback);
  }

  onTTSSettings(callback: (result: { success: boolean; settings?: any; error?: string }) => void) {
    this.socket?.on("tts-settings", callback);
  }

  onAvailableVoices(
    callback: (result: { success: boolean; voices?: any[]; error?: string }) => void
  ) {
    this.socket?.on("available-voices", callback);
  }

  onAudioChunk(
    callback: (data: { audioData: ArrayBuffer; text: string; isComplete: boolean }) => void
  ) {
    this.socket?.on("audio-chunk", callback);
  }

  // 移除事件监听器
  off(event: string, callback?: (...args: any[]) => void) {
    if (callback) {
      this.socket?.off(event, callback);
    } else {
      this.socket?.off(event);
    }
  }

  isConnected(): boolean {
    return this.socket?.connected || false;
  }
}

// 全局单例模式
let globalSocketService: SocketService | null = null;
let isCreating = false;

export function getSocketService(): SocketService {
  if (!globalSocketService && !isCreating) {
    isCreating = true;
    globalSocketService = new SocketService();
    isCreating = false;
  }

  if (!globalSocketService) {
    throw new Error("无法创建SocketService");
  }

  return globalSocketService;
}

export function resetSocketService(): void {
  if (globalSocketService) {
    globalSocketService.disconnect();
    globalSocketService = null;
  }
}

export default SocketService;
