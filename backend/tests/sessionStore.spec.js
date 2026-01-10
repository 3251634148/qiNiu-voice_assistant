const SessionStore = require("../utils/sessionStore");

describe("SessionStore", () => {
  let sessionStore;
  const testSocketId = "test-socket-1";

  beforeEach(() => {
    sessionStore = new SessionStore();
  });

  afterEach(() => {
    // 清理测试会话
    sessionStore.clearSession(testSocketId);
  });

  describe("getOrCreateSession", () => {
    test("应该创建新会话", () => {
      const session = sessionStore.getOrCreateSession(testSocketId);

      expect(session).toBeDefined();
      expect(session.sessionId).toBe(testSocketId);
      expect(session.history).toEqual([]);
      expect(session.pendingToolCall).toBeNull();
      expect(session.pendingConfirmation).toBeNull();
      expect(session.canceled).toBe(false);
    });

    test("应该返回现有会话", () => {
      const session1 = sessionStore.getOrCreateSession(testSocketId);
      const session2 = sessionStore.getOrCreateSession(testSocketId);

      expect(session1).toBe(session2);
      expect(session1.sessionId).toBe(testSocketId);
    });
  });

  describe("addMessage", () => {
    test("应该添加消息到会话", () => {
      const session = sessionStore.getOrCreateSession(testSocketId);

      const message = sessionStore.addMessage(testSocketId, {
        type: "user",
        content: "测试消息",
      });

      expect(message.id).toBeDefined();
      expect(message.type).toBe("user");
      expect(message.content).toBe("测试消息");
      expect(message.timestamp).toBeInstanceOf(Date);

      expect(session.history).toHaveLength(1);
      expect(session.history[0]).toBe(message);
    });

    test("应该限制历史记录长度", () => {
      const session = sessionStore.getOrCreateSession(testSocketId);

      // 添加超过限制的消息
      for (let i = 0; i < 150; i++) {
        sessionStore.addMessage(testSocketId, {
          type: "user",
          content: `消息 ${i}`,
        });
      }

      expect(session.history).toHaveLength(100);
    });
  });

  describe("setPendingToolCall", () => {
    test("应该设置待处理的工具调用", () => {
      const session = sessionStore.getOrCreateSession(testSocketId);

      const toolCall = {
        id: "test-tool-1",
        name: "play_music",
        arguments: { source: "spotify" },
      };

      const riskAssessment = {
        level: "low",
        requiresConfirmation: false,
      };

      sessionStore.setPendingToolCall(testSocketId, toolCall, riskAssessment);

      expect(session.pendingToolCall).toBeDefined();
      expect(session.pendingToolCall.id).toBe("test-tool-1");
      expect(session.pendingToolCall.name).toBe("play_music");
      expect(session.pendingToolCall.riskAssessment).toBe(riskAssessment);
      expect(session.canceled).toBe(false);
    });
  });

  describe("clearPendingToolCall", () => {
    test("应该清除待处理的工具调用", () => {
      const session = sessionStore.getOrCreateSession(testSocketId);

      const toolCall = {
        id: "test-tool-1",
        name: "play_music",
        arguments: { source: "spotify" },
      };

      sessionStore.setPendingToolCall(testSocketId, toolCall);
      const clearedCall = sessionStore.clearPendingToolCall(testSocketId);

      expect(clearedCall).toBeDefined();
      expect(clearedCall.id).toBe("test-tool-1");
      expect(session.pendingToolCall).toBeNull();
      expect(session.canceled).toBe(false);
    });
  });

  describe("markCanceled", () => {
    test("应该标记会话为已取消", () => {
      const session = sessionStore.getOrCreateSession(testSocketId);

      const result = sessionStore.markCanceled(testSocketId, "user_canceled");

      expect(result).toBe(true);
      expect(session.canceled).toBe(true);
      expect(session.cancelReason).toBe("user_canceled");
      expect(session.canceledAt).toBeInstanceOf(Date);
    });
  });

  describe("setPendingConfirmation", () => {
    test("应该设置待确认的请求", () => {
      const session = sessionStore.getOrCreateSession(testSocketId);

      const confirmation = {
        id: "confirm-1",
        toolCall: { name: "write_file" },
        riskLevel: "medium",
      };

      sessionStore.setPendingConfirmation(testSocketId, confirmation);

      expect(session.pendingConfirmation).toBeDefined();
      expect(session.pendingConfirmation.id).toBe("confirm-1");
      expect(session.pendingConfirmation.toolCall.name).toBe("write_file");
    });
  });

  describe("getPendingConfirmation", () => {
    test("应该获取待确认的请求", () => {
      const _session = sessionStore.getOrCreateSession(testSocketId);

      const confirmation = {
        id: "confirm-1",
        toolCall: { name: "write_file" },
        riskLevel: "medium",
        expiresAt: new Date(Date.now() + 60000), // 1分钟后过期
      };

      sessionStore.setPendingConfirmation(testSocketId, confirmation);
      const pending = sessionStore.getPendingConfirmation(testSocketId);

      expect(pending).toBeDefined();
      expect(pending.id).toBe("confirm-1");
    });

    test("应该返回null如果确认已过期", () => {
      const session = sessionStore.getOrCreateSession(testSocketId);

      const confirmation = {
        id: "confirm-1",
        toolCall: { name: "write_file" },
        riskLevel: "medium",
        expiresAt: new Date(Date.now() - 1000), // 已过期
      };

      sessionStore.setPendingConfirmation(testSocketId, confirmation);
      const pending = sessionStore.getPendingConfirmation(testSocketId);

      expect(pending).toBeNull();
      expect(session.pendingConfirmation).toBeNull();
    });
  });

  describe("getSessionStatus", () => {
    test("应该返回正确的会话状态", () => {
      const _session = sessionStore.getOrCreateSession(testSocketId);

      sessionStore.addMessage(testSocketId, { type: "user", content: "test" });
      sessionStore.setPendingToolCall(testSocketId, {
        id: "tool-1",
        name: "play_music",
        arguments: {},
      });

      const status = sessionStore.getSessionStatus(testSocketId);

      expect(status.exists).toBe(true);
      expect(status.active).toBe(true);
      expect(status.sessionId).toBe(testSocketId);
      expect(status.hasPendingToolCall).toBe(true);
      expect(status.messageCount).toBe(1);
    });

    test("应该返回不存在状态", () => {
      const status = sessionStore.getSessionStatus("non-existent");

      expect(status.exists).toBe(false);
      expect(status.active).toBe(false);
    });
  });

  describe("updateContext", () => {
    test("应该更新会话上下文", () => {
      const session = sessionStore.getOrCreateSession(testSocketId);

      const contextUpdate = {
        userId: "user-123",
        preference: "dark-mode",
      };

      sessionStore.updateContext(testSocketId, contextUpdate);

      expect(session.context.userId).toBe("user-123");
      expect(session.context.preference).toBe("dark-mode");
    });
  });

  describe("getStats", () => {
    test("应该返回正确的统计信息", () => {
      // 创建多个会话
      sessionStore.getOrCreateSession("socket-1");
      sessionStore.getOrCreateSession("socket-2");

      sessionStore.addMessage("socket-1", { type: "user", content: "message 1" });
      sessionStore.addMessage("socket-1", { type: "assistant", content: "message 2" });
      sessionStore.addMessage("socket-2", { type: "user", content: "message 3" });

      const stats = sessionStore.getStats();

      expect(stats.totalSessions).toBe(2);
      expect(stats.activeSessions).toBe(2);
      expect(stats.totalMessages).toBe(3);
      expect(stats.averageMessagesPerSession).toBe(1.5); // 3 messages / 2 sessions
    });
  });

  describe("clearSession", () => {
    test("应该清理指定会话", () => {
      sessionStore.getOrCreateSession(testSocketId);

      const result = sessionStore.clearSession(testSocketId);

      expect(result).toBe(true);
      expect(sessionStore.getSessionStatus(testSocketId).exists).toBe(false);
    });

    test("应该对不存在的会话返回false", () => {
      const result = sessionStore.clearSession("non-existent");

      expect(result).toBe(false);
    });
  });
});
