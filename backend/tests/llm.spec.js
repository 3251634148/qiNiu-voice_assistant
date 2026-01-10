const LLMService = require("../services/llm");

describe("LLMService", () => {
  let llmService;

  beforeAll(() => {
    // 测试环境注入一个假的 key，避免构造函数直接抛错
    process.env.DASHSCOPE_API_KEY = process.env.DASHSCOPE_API_KEY || "test_key";
  });

  beforeEach(() => {
    llmService = new LLMService();
  });

  describe("parseToolCalls", () => {
    test("应该正确解析工具调用", () => {
      const response = {
        toolCalls: [
          {
            id: "call_1",
            function: {
              name: "play_music",
              arguments: '{"source": "spotify", "query": "test song"}',
            },
          },
          {
            id: "call_2",
            function: {
              name: "write_file",
              arguments: '{"path": "test.txt", "content": "hello world", "mode": "create"}',
            },
          },
        ],
      };

      const result = llmService.parseToolCalls(response);

      expect(result).toHaveLength(2);
      expect(result[0]).toEqual({
        id: "call_1",
        name: "play_music",
        arguments: {
          source: "spotify",
          query: "test song",
        },
        type: "function",
      });
      expect(result[1]).toEqual({
        id: "call_2",
        name: "write_file",
        arguments: {
          path: "test.txt",
          content: "hello world",
          mode: "create",
        },
        type: "function",
      });
    });

    test("应该处理空的工具调用", () => {
      const response = { toolCalls: [] };

      const result = llmService.parseToolCalls(response);

      expect(result).toHaveLength(0);
    });

    test("应该处理未定义的工具调用", () => {
      const response = {};

      const result = llmService.parseToolCalls(response);

      expect(result).toHaveLength(0);
    });
  });

  describe("selectModel", () => {
    test("应该为复杂任务选择 qwen-turbo", () => {
      const messages = [{ role: "user", content: "请写一篇关于人工智能的文章" }];

      const model = llmService.selectModel(messages);

      expect(model).toBe("qwen-turbo");
    });

    test("应该为简单任务选择 qwen-plus", () => {
      const messages = [{ role: "user", content: "你好" }];

      const model = llmService.selectModel(messages);

      expect(model).toBe("qwen-plus");
    });

    test("应该为长文本选择 qwen-turbo", () => {
      const longText = "a".repeat(200);
      const messages = [{ role: "user", content: longText }];

      const model = llmService.selectModel(messages);

      expect(model).toBe("qwen-turbo");
    });
  });

  describe("shouldUseAdvancedModel", () => {
    test("应该对复杂关键词返回 true", () => {
      expect(llmService.shouldUseAdvancedModel("写一篇文章")).toBe(true);
      expect(llmService.shouldUseAdvancedModel("创建一个文件")).toBe(true);
      expect(llmService.shouldUseAdvancedModel("生成报告")).toBe(true);
      expect(llmService.shouldUseAdvancedModel("分析数据")).toBe(true);
    });

    test("应该对简单对话返回 false", () => {
      expect(llmService.shouldUseAdvancedModel("你好")).toBe(false);
      expect(llmService.shouldUseAdvancedModel("今天天气怎么样")).toBe(false);
      expect(llmService.shouldUseAdvancedModel("播放音乐")).toBe(false);
    });

    test("应该对英文关键词返回 true", () => {
      expect(llmService.shouldUseAdvancedModel("write an article")).toBe(true);
      expect(llmService.shouldUseAdvancedModel("create a document")).toBe(true);
      expect(llmService.shouldUseAdvancedModel("analyze this")).toBe(true);
    });

    test("应该对很长的文本返回 true", () => {
      expect(llmService.shouldUseAdvancedModel("a".repeat(200))).toBe(true);
    });
  });

  describe("getFunctionDefinitions", () => {
    test("应该返回所有函数定义", () => {
      const functions = llmService.getFunctionDefinitions();

      expect(functions).toHaveLength(5);
      expect(functions.map((f) => f.name)).toContain("play_music");
      expect(functions.map((f) => f.name)).toContain("stop_music");
      expect(functions.map((f) => f.name)).toContain("write_article");
      expect(functions.map((f) => f.name)).toContain("write_file");
      expect(functions.map((f) => f.name)).toContain("open_app");
    });

    test("play_music 参数应包含 source/query，且 source 可选", () => {
      const functions = llmService.getFunctionDefinitions();
      const playMusicFunc = functions.find((f) => f.name === "play_music");

      expect(playMusicFunc.parameters.properties).toHaveProperty("source");
      expect(playMusicFunc.parameters.properties).toHaveProperty("query");

      const required = playMusicFunc.parameters.required || [];
      expect(required).not.toContain("source");
    });
  });

  describe("getAvailableModels", () => {
    test("应该返回可用的模型列表", () => {
      const models = llmService.getAvailableModels();

      expect(models).toHaveProperty("qwen");
      expect(models.qwen).toContain("qwen-plus");
      expect(models.qwen).toContain("qwen-turbo");
      expect(models.qwen).toContain("qwen-max");
    });
  });
});
