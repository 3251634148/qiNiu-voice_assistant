const LLMService = require("../../services/llm");

describe("LLMService", () => {
  let llmService;

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
    test("应该为复杂任务选择GPT-4", () => {
      const messages = [{ role: "user", content: "请写一篇关于人工智能的文章" }];

      const model = llmService.selectModel(messages);

      expect(model).toBe("gpt-4");
    });

    test("应该为简单任务选择GPT-3.5", () => {
      const messages = [{ role: "user", content: "你好" }];

      const model = llmService.selectModel(messages);

      expect(model).toBe("gpt-3.5-turbo");
    });

    test("应该为长文本选择GPT-4", () => {
      const longText = "a".repeat(200);
      const messages = [{ role: "user", content: longText }];

      const model = llmService.selectModel(messages);

      expect(model).toBe("gpt-4");
    });
  });

  describe("shouldUseGPT4", () => {
    test("应该对写作关键词返回true", () => {
      expect(llmService.shouldUseGPT4("写一篇文章")).toBe(true);
      expect(llmService.shouldUseGPT4("创建一个文件")).toBe(true);
      expect(llmService.shouldUseGPT4("生成报告")).toBe(true);
      expect(llmService.shouldUseGPT4("分析数据")).toBe(true);
    });

    test("应该对简单查询返回false", () => {
      expect(llmService.shouldUseGPT4("你好")).toBe(false);
      expect(llmService.shouldUseGPT4("今天天气怎么样")).toBe(false);
      expect(llmService.shouldUseGPT4("播放音乐")).toBe(false);
    });

    test("应该对英文关键词返回true", () => {
      expect(llmService.shouldUseGPT4("write an article")).toBe(true);
      expect(llmService.shouldUseGPT4("create a document")).toBe(true);
      expect(llmService.shouldUseGPT4("analyze this")).toBe(true);
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

    test("函数定义应该包含正确的参数", () => {
      const functions = llmService.getFunctionDefinitions();
      const playMusicFunc = functions.find((f) => f.name === "play_music");

      expect(playMusicFunc.parameters.properties).toHaveProperty("source");
      expect(playMusicFunc.parameters.properties).toHaveProperty("query");
      expect(playMusicFunc.parameters.required).toContain("source");
    });
  });

  describe("getAvailableModels", () => {
    test("应该返回可用的模型列表", () => {
      const models = llmService.getAvailableModels();

      expect(models).toHaveProperty("openai");
      expect(models).toHaveProperty("qwen");
      expect(models.openai).toContain("gpt-3.5-turbo");
      expect(models.openai).toContain("gpt-4");
    });
  });
});
