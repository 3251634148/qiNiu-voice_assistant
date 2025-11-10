const express = require("express");
const http = require("node:http");
const socketIo = require("socket.io");
const cors = require("cors");
const path = require("node:path");
require("dotenv").config();

const ConversationController = require("./controllers/conversationController");
const SystemController = require("./services/systemController");
const logger = require("./utils/logger");

const app = express();
const server = http.createServer(app);
const io = socketIo(server, {
  cors: {
    origin: "*",
    methods: ["GET", "POST"],
  },
});

// 中间件
app.use(cors());
app.use(express.json());
app.use(express.static(path.join(__dirname, "../frontend/dist")));

// 服务实例
const conversationController = new ConversationController();
const systemController = new SystemController();

// 存储socket连接的映射，用于conversationController
const socketMap = new Map();

// 连接频率限制器
const connectionLimiter = new Map();

// 为conversationController提供socket获取函数
conversationController.setSocketInstance((socketId) => socketMap.get(socketId));

// Socket.IO 连接处理
io.on("connection", (socket) => {
  const clientIp = socket.handshake.address || socket.request.socket.remoteAddress;
  const now = Date.now();

  // 检查连接频率
  if (connectionLimiter.has(clientIp)) {
    const lastConnection = connectionLimiter.get(clientIp);
    if (now - lastConnection < 1000) {
      // 1秒内重复连接
      logger.warn(`拒绝频繁连接: ${clientIp}, socket: ${socket.id}`);
      socket.disconnect();
      return;
    }
  }

  connectionLimiter.set(clientIp, now);
  logger.info(`客户端连接: ${socket.id}`, {
    ip: clientIp,
    userAgent: socket.handshake.headers["user-agent"],
    timestamp: new Date().toISOString(),
  });

  // 存储socket连接
  socketMap.set(socket.id, socket);

  // 语音输入处理
  socket.on("voice-input", async (data) => {
    try {
      const { audioData, language = "zh-CN" } = data;
      await conversationController.handleVoiceInput(audioData, language, socket);
    } catch (error) {
      logger.error("处理语音输入失败:", error);
      socket.emit("error", { message: error.message });
    }
  });

  // 文本命令处理
  socket.on("text-command", async (data) => {
    try {
      const { text } = data;
      await conversationController.handleTextCommand(text, socket);
    } catch (error) {
      logger.error("处理文本命令失败:", error);
      socket.emit("error", { message: error.message });
    }
  });

  // 确认操作
  socket.on("confirm-action", async (data) => {
    try {
      const { confirmationId, approved } = data;
      await conversationController.handleConfirmation(socket.id, confirmationId, approved);
    } catch (error) {
      logger.error("处理确认失败:", error);
      socket.emit("error", { message: error.message });
    }
  });

  socket.on("cancel", async (data) => {
    try {
      const silent = !!(data?.silent);
      await conversationController.handleCancel(socket.id, silent);
    } catch (error) {
      logger.error("处理取消失败:", error);
      socket.emit("error", { message: error.message });
    }
  });

  // 获取会话状态
  socket.on("get-session-status", () => {
    try {
      const status = conversationController.getSessionStatus(socket.id);
      socket.emit("session-status", status);
    } catch (error) {
      logger.error("获取会话状态失败:", error);
      socket.emit("error", { message: error.message });
    }
  });

  // 获取会话历史
  socket.on("get-session-history", (data) => {
    try {
      const { limit = 50 } = data || {};
      const history = conversationController.getSessionHistory(socket.id, limit);
      socket.emit("session-history", history);
    } catch (error) {
      logger.error("获取会话历史失败:", error);
      socket.emit("error", { message: error.message });
    }
  });

  // 清理会话
  socket.on("clear-session", () => {
    try {
      conversationController.clearSession(socket.id);
      socket.emit("session-history", []);
      const status = conversationController.getSessionStatus(socket.id);
      socket.emit("session-status", status);
    } catch (error) {
      logger.error("清理会话失败:", error);
      socket.emit("error", { message: error.message });
    }
  });

  // 获取系统信息
  socket.on("get-system-info", async () => {
    try {
      const info = await systemController.getSystemInfo();
      socket.emit("system-info", info);
    } catch (error) {
      logger.error("获取系统信息失败:", error);
      socket.emit("error", { message: error.message });
    }
  });

  // 更新TTS设置
  socket.on("update-tts-settings", (data) => {
    try {
      const result = conversationController.updateTTSSettings(socket.id, data);
      socket.emit("tts-settings-updated", result);
    } catch (error) {
      logger.error("更新TTS设置失败:", error);
      socket.emit("error", { message: error.message });
    }
  });

  // 获取TTS设置
  socket.on("get-tts-settings", () => {
    try {
      const result = conversationController.getTTSSettings(socket.id);
      socket.emit("tts-settings", result);
    } catch (error) {
      logger.error("获取TTS设置失败:", error);
      socket.emit("error", { message: error.message });
    }
  });

  // 获取可用音色
  socket.on("get-available-voices", () => {
    try {
      const result = conversationController.getAvailableVoices();
      socket.emit("available-voices", result);
    } catch (error) {
      logger.error("获取可用音色失败:", error);
      socket.emit("error", { message: error.message });
    }
  });

  socket.on("disconnect", (reason) => {
    logger.info(`客户端断开连接: ${socket.id}`, {
      reason,
      ip: socket.handshake.address || socket.request.socket.remoteAddress,
      timestamp: new Date().toISOString(),
    });
    socketMap.delete(socket.id);
    // 清理会话数据
    conversationController.clearSession(socket.id);

    // 清理连接限制器（可选，防止内存泄漏）
    const clientIp = socket.handshake.address || socket.request.socket.remoteAddress;
    if (connectionLimiter.has(clientIp)) {
      connectionLimiter.delete(clientIp);
    }
  });
});

// REST API 路由
app.get("/api/health", (_req, res) => {
  res.json({ status: "ok", timestamp: new Date().toISOString() });
});

app.get("/api/capabilities", (_req, res) => {
  res.json({
    voiceRecognition: true,
    textToSpeech: true,
    systemControl: true,
    fileOperations: true,
    applicationControl: true,
    musicControl: true,
    safetyValidation: true,
    sessionManagement: true,
    toolExecution: true,
    supportedTools: ["play_music", "stop_music", "write_article", "write_file", "open_app"],
  });
});

// 获取服务器统计信息
app.get("/api/stats", (_req, res) => {
  try {
    const stats = conversationController.getStats();
    res.json({
      ...stats,
      connectedClients: socketMap.size,
      timestamp: new Date().toISOString(),
    });
  } catch (error) {
    logger.error("获取统计信息失败:", error);
    res.status(500).json({ error: error.message });
  }
});

// 静态文件服务
app.get("*", (_req, res) => {
  res.sendFile(path.join(__dirname, "../frontend/dist/index.html"));
});

const PORT = process.env.PORT || 3001;
server.listen(PORT, () => {
  logger.info(`服务器运行在端口 ${PORT}`);
});

// 优雅关闭
process.on("SIGTERM", () => {
  logger.info("收到 SIGTERM 信号，正在关闭服务器...");
  server.close(() => {
    logger.info("服务器已关闭");
    process.exit(0);
  });
});

process.on("SIGINT", () => {
  logger.info("收到 SIGINT 信号，正在关闭服务器...");
  server.close(() => {
    logger.info("服务器已关闭");
    process.exit(0);
  });
});
