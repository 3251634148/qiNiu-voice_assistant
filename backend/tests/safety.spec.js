const SafetyService = require("../utils/safety");

describe("SafetyService", () => {
  let safetyService;

  beforeEach(() => {
    safetyService = new SafetyService();
  });

  describe("validateToolCall", () => {
    test("应该允许有效的音乐播放工具调用", async () => {
      const toolCall = {
        id: "test-1",
        name: "play_music",
        arguments: {
          source: "spotify",
          query: "test song",
        },
      };

      const result = await safetyService.validateToolCall(toolCall);

      expect(result.allowed).toBe(true);
      expect(result.riskLevel).toBe("low");
      expect(result.requiresConfirmation).toBe(false);
    });

    test("应该允许缺省音乐来源（自动选择播放器）", async () => {
      const toolCall = {
        id: "test-1-2",
        name: "play_music",
        arguments: {
          query: "test song",
        },
      };

      const result = await safetyService.validateToolCall(toolCall);

      expect(result.allowed).toBe(true);
      expect(result.riskLevel).toBe("low");
      expect(result.requiresConfirmation).toBe(false);
    });

    test("应该阻止无效的音乐来源", async () => {
      const toolCall = {
        id: "test-2",
        name: "play_music",
        arguments: {
          source: "invalid_source",
          query: "test song",
        },
      };

      const result = await safetyService.validateToolCall(toolCall);

      expect(result.allowed).toBe(false);
      expect(result.riskLevel).toBe("high");
      expect(result.reason).toContain("不支持的音乐来源");
    });

    test("应该允许有效的写文件工具调用", async () => {
      const toolCall = {
        id: "test-3",
        name: "write_file",
        arguments: {
          path: "Documents/test.txt",
          content: "test content",
          mode: "create",
        },
      };

      const result = await safetyService.validateToolCall(toolCall);

      expect(result.allowed).toBe(true);
      expect(result.riskLevel).toBe("medium");
      expect(result.requiresConfirmation).toBe(true);
    });

    test("应该阻止危险的文件路径", async () => {
      const toolCall = {
        id: "test-4",
        name: "write_file",
        arguments: {
          path: "../../../etc/passwd",
          content: "malicious content",
          mode: "create",
        },
      };

      const result = await safetyService.validateToolCall(toolCall);

      expect(result.allowed).toBe(false);
      expect(result.riskLevel).toBe("high");
      expect(result.reason).toContain("危险命令");
    });

    test("应该允许有效的应用打开", async () => {
      const toolCall = {
        id: "test-5",
        name: "open_app",
        arguments: {
          name: "spotify",
        },
      };

      const result = await safetyService.validateToolCall(toolCall);

      expect(result.allowed).toBe(true);
      expect(result.riskLevel).toBe("low");
      expect(result.requiresConfirmation).toBe(false);
    });

    test("应该允许任意应用名称（是否安装由系统层决定）", async () => {
      const toolCall = {
        id: "test-6",
        name: "open_app",
        arguments: {
          name: "malicious_app",
        },
      };

      const result = await safetyService.validateToolCall(toolCall);

      expect(result.allowed).toBe(true);
      expect(result.riskLevel).toBe("low");
      expect(result.requiresConfirmation).toBe(false);
    });

    test("应该阻止包含危险命令的参数", async () => {
      const toolCall = {
        id: "test-7",
        name: "write_file",
        arguments: {
          path: "Documents/test.txt",
          content: "rm -rf /",
          mode: "create",
        },
      };

      const result = await safetyService.validateToolCall(toolCall);

      expect(result.allowed).toBe(false);
      expect(result.riskLevel).toBe("high");
      expect(result.reason).toContain("危险命令");
    });
  });

  describe("generateConfirmationRequest", () => {
    test("应该生成正确的确认请求", () => {
      const toolCall = {
        id: "test-8",
        name: "write_file",
        arguments: {
          path: "Documents/important.txt",
          content: "important content",
          mode: "overwrite",
        },
      };

      const riskAssessment = {
        level: "high",
        requiresConfirmation: true,
        reason: "高风险操作",
        suggestions: ["此操作将覆盖现有文件"],
      };

      const confirmation = safetyService.generateConfirmationRequest(toolCall, riskAssessment);

      expect(confirmation.id).toBeDefined();
      expect(confirmation.toolCall).toEqual(toolCall);
      expect(confirmation.riskLevel).toBe("high");
      expect(confirmation.summary).toContain("覆盖文件");
      expect(confirmation.reason).toBe("高风险操作");
      expect(confirmation.suggestions).toContain("此操作将覆盖现有文件");
      expect(confirmation.expiresAt).toBeDefined();
    });
  });

  describe("assessRisk", () => {
    test("应该正确评估文件覆盖风险", () => {
      const toolCall = {
        name: "write_file",
        arguments: {
          mode: "overwrite",
        },
      };

      const risk = safetyService.assessRisk(toolCall.name, toolCall.arguments);

      expect(risk.level).toBe("high");
      expect(risk.requiresConfirmation).toBe(true);
    });

    test("应该正确评估系统应用风险", () => {
      const toolCall = {
        name: "open_app",
        arguments: {
          name: "terminal",
        },
      };

      const risk = safetyService.assessRisk(toolCall.name, toolCall.arguments);

      expect(risk.level).toBe("medium");
      expect(risk.requiresConfirmation).toBe(true);
    });
  });
});
