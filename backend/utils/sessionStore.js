const logger = require("./logger");

class SessionStore {
  constructor() {
    this.sessions = new Map();
    this.cleanupInterval = 30 * 60 * 1000; // 30分钟清理一次过期会话
    this.sessionTimeout = 2 * 60 * 60 * 1000; // 2小时会话超时
    this.confirmationTimeout = 5 * 60 * 1000; // 5分钟确认超时

    this.startCleanupTimer();
  }

  // 创建或获取会话
  getOrCreateSession(socketId) {
    if (!this.sessions.has(socketId)) {
      this.sessions.set(socketId, {
        sessionId: socketId,
        history: [],
        pendingToolCall: null,
        pendingConfirmation: null,
        canceled: false,
        createdAt: new Date(),
        lastActivity: new Date(),
        context: {},
      });

      logger.info(`创建新会话: ${socketId}`);
    }

    const session = this.sessions.get(socketId);
    session.lastActivity = new Date();

    return session;
  }

  // 添加消息到历史记录
  addMessage(socketId, message) {
    const session = this.getOrCreateSession(socketId);

    const messageRecord = {
      id: `msg_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
      timestamp: new Date(),
      type: message.type, // 'user', 'assistant', 'system', 'tool_call', 'tool_result'
      content: message.content,
      metadata: message.metadata || {},
    };

    session.history.push(messageRecord);

    // 限制历史记录长度，保留最近100条
    if (session.history.length > 100) {
      session.history = session.history.slice(-100);
    }

    logger.debug(`添加消息到会话 ${socketId}`, {
      messageId: messageRecord.id,
      type: messageRecord.type,
    });

    return messageRecord;
  }

  // 设置待处理的工具调用
  setPendingToolCall(socketId, toolCall, riskAssessment = null) {
    const session = this.getOrCreateSession(socketId);

    session.pendingToolCall = {
      ...toolCall,
      riskAssessment: riskAssessment,
      createdAt: new Date(),
      expiresAt: new Date(Date.now() + this.confirmationTimeout),
    };

    session.canceled = false;

    logger.info(`设置待处理工具调用: ${socketId}`, {
      toolName: toolCall.name,
      requiresConfirmation: riskAssessment?.requiresConfirmation || false,
    });

    return session.pendingToolCall;
  }

  // 清除待处理的工具调用
  clearPendingToolCall(socketId) {
    const session = this.sessions.get(socketId);
    if (session) {
      const pendingCall = session.pendingToolCall;
      session.pendingToolCall = null;
      session.canceled = false;

      if (pendingCall) {
        logger.info(`清除待处理工具调用: ${socketId}`, {
          toolName: pendingCall.name,
        });
      }

      return pendingCall;
    }

    return null;
  }

  // 标记工具调用为已取消
  markCanceled(socketId, reason = "user_canceled") {
    const session = this.sessions.get(socketId);
    if (session) {
      session.canceled = true;
      session.cancelReason = reason;
      session.canceledAt = new Date();

      logger.info(`标记工具调用已取消: ${socketId}`, { reason });

      return true;
    }

    return false;
  }

  // 设置待确认的请求
  setPendingConfirmation(socketId, confirmationRequest) {
    const session = this.getOrCreateSession(socketId);

    session.pendingConfirmation = {
      ...confirmationRequest,
      createdAt: new Date(),
      expiresAt: new Date(Date.now() + this.confirmationTimeout),
    };

    logger.info(`设置待确认请求: ${socketId}`, {
      confirmationId: confirmationRequest.id,
      toolName: confirmationRequest.toolCall.name,
    });

    return session.pendingConfirmation;
  }

  // 获取待确认的请求
  getPendingConfirmation(socketId) {
    const session = this.sessions.get(socketId);
    if (session?.pendingConfirmation) {
      // 检查是否过期
      if (new Date() > session.pendingConfirmation.expiresAt) {
        this.clearPendingConfirmation(socketId);
        return null;
      }

      return session.pendingConfirmation;
    }

    return null;
  }

  // 清除待确认的请求
  clearPendingConfirmation(socketId) {
    const session = this.sessions.get(socketId);
    if (session) {
      const confirmation = session.pendingConfirmation;
      session.pendingConfirmation = null;

      if (confirmation) {
        logger.info(`清除待确认请求: ${socketId}`, {
          confirmationId: confirmation.id,
        });
      }

      return confirmation;
    }

    return null;
  }

  // 获取会话历史记录
  getHistory(socketId, limit = 50) {
    const session = this.sessions.get(socketId);
    if (session) {
      return session.history.slice(-limit);
    }

    return [];
  }

  // 获取会话上下文
  getContext(socketId) {
    const session = this.sessions.get(socketId);
    return session ? session.context : {};
  }

  // 更新会话上下文
  updateContext(socketId, contextUpdate) {
    const session = this.getOrCreateSession(socketId);
    session.context = { ...session.context, ...contextUpdate };

    logger.debug(`更新会话上下文: ${socketId}`, contextUpdate);

    return session.context;
  }

  // 获取会话状态
  getSessionStatus(socketId) {
    const session = this.sessions.get(socketId);
    if (!session) {
      return {
        exists: false,
        active: false,
      };
    }

    const now = new Date();
    const isExpired = now - session.lastActivity > this.sessionTimeout;

    return {
      exists: true,
      active: !isExpired,
      sessionId: session.sessionId,
      hasPendingToolCall: !!session.pendingToolCall,
      hasPendingConfirmation: !!session.pendingConfirmation,
      isCanceled: session.canceled,
      messageCount: session.history.length,
      lastActivity: session.lastActivity,
      createdAt: session.createdAt,
    };
  }

  // 清理过期会话
  cleanupExpiredSessions() {
    const now = new Date();
    const expiredSessions = [];

    for (const [socketId, session] of this.sessions.entries()) {
      const isExpired = now - session.lastActivity > this.sessionTimeout;
      const hasExpiredConfirmation =
        session.pendingConfirmation && now > session.pendingConfirmation.expiresAt;
      const hasExpiredToolCall = session.pendingToolCall && now > session.pendingToolCall.expiresAt;

      if (isExpired || hasExpiredConfirmation || hasExpiredToolCall) {
        expiredSessions.push(socketId);
      }
    }

    for (const socketId of expiredSessions) {
      const session = this.sessions.get(socketId);
      logger.info(`清理过期会话: ${socketId}`, {
        expiredReason: session ? "session_timeout" : "not_found",
        lastActivity: session?.lastActivity,
      });

      this.sessions.delete(socketId);
    }

    if (expiredSessions.length > 0) {
      logger.info(`清理完成，删除 ${expiredSessions.length} 个过期会话`);
    }

    return expiredSessions.length;
  }

  // 启动清理定时器
  startCleanupTimer() {
    setInterval(() => {
      this.cleanupExpiredSessions();
    }, this.cleanupInterval);

    logger.info("会话清理定时器已启动", {
      interval: this.cleanupInterval,
      timeout: this.sessionTimeout,
    });
  }

  // 获取所有活跃会话统计
  getStats() {
    const now = new Date();
    let activeCount = 0;
    let pendingToolCalls = 0;
    let pendingConfirmations = 0;
    let totalMessages = 0;

    for (const session of this.sessions.values()) {
      const isActive = now - session.lastActivity < this.sessionTimeout;
      if (isActive) activeCount++;

      if (session.pendingToolCall) pendingToolCalls++;
      if (session.pendingConfirmation) pendingConfirmations++;
      totalMessages += session.history.length;
    }

    return {
      totalSessions: this.sessions.size,
      activeSessions: activeCount,
      pendingToolCalls: pendingToolCalls,
      pendingConfirmations: pendingConfirmations,
      totalMessages: totalMessages,
      averageMessagesPerSession:
        this.sessions.size > 0 ? Math.round(totalMessages / this.sessions.size) : 0,
    };
  }

  // 手动清理指定会话
  clearSession(socketId) {
    const session = this.sessions.get(socketId);
    if (session) {
      logger.info(`手动清理会话: ${socketId}`, {
        messageCount: session.history.length,
        hasPendingToolCall: !!session.pendingToolCall,
        hasPendingConfirmation: !!session.pendingConfirmation,
      });

      this.sessions.delete(socketId);
      return true;
    }

    return false;
  }

  // 导出会话数据（用于调试）
  exportSession(socketId) {
    const session = this.sessions.get(socketId);
    if (session) {
      return {
        sessionId: session.sessionId,
        history: session.history,
        context: session.context,
        pendingToolCall: session.pendingToolCall,
        pendingConfirmation: session.pendingConfirmation,
        status: this.getSessionStatus(socketId),
      };
    }

    return null;
  }

  // 获取所有会话ID（用于管理）
  getAllSessionIds() {
    return Array.from(this.sessions.keys());
  }
}

module.exports = SessionStore;
