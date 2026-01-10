const ToolRouter = require("../services/toolRouter");

describe("ToolRouter", () => {
  let toolRouter;

  beforeEach(() => {
    toolRouter = new ToolRouter();
  });

  describe("routeAndExecute", () => {
    test("应该成功路由音乐播放工具调用", async () => {
      const toolCall = {
        id: "test-music-1",
        name: "play_music",
        arguments: {
          source: "local",
        },
      };

      const context = { socketId: "test-socket" };

      // Mock the music controller
      toolRouter.musicController.playMusic = jest.fn().mockResolvedValue({
        success: true,
        message: "开始播放本地音乐",
      });

      const result = await toolRouter.routeAndExecute(toolCall, context);

      expect(result.success).toBe(true);
      expect(result.name).toBe("play_music");
      expect(result.result.action).toBe("play_music");
      expect(result.result.message).toContain("播放");
    });

    test("应该成功路由停止音乐工具调用", async () => {
      const toolCall = {
        id: "test-music-2",
        name: "stop_music",
        arguments: {},
      };

      const context = { socketId: "test-socket" };

      // Mock the music controller
      toolRouter.musicController.stopMusic = jest.fn().mockResolvedValue({
        success: true,
        message: "已停止音乐播放",
      });

      const result = await toolRouter.routeAndExecute(toolCall, context);

      expect(result.success).toBe(true);
      expect(result.name).toBe("stop_music");
      expect(result.result.action).toBe("stop_music");
    });

    test("应该成功路由写文章工具调用", async () => {
      const toolCall = {
        id: "test-article-1",
        name: "write_article",
        arguments: {
          topic: "测试主题",
          style: "casual",
          length: "short",
        },
      };

      const context = { socketId: "test-socket" };

      // Mock the LLM service
      toolRouter.llmService.invokeLLM = jest.fn().mockResolvedValue({
        text: "这是一篇关于测试主题的文章",
      });

      // Mock the file writer
      toolRouter.fileWriter.saveDraft = jest.fn().mockResolvedValue({
        success: true,
        filePath: "/path/to/draft.txt",
      });

      const result = await toolRouter.routeAndExecute(toolCall, context);

      expect(result.success).toBe(true);
      expect(result.name).toBe("write_article");
      expect(result.result.topic).toBe("测试主题");
      expect(result.result.content).toBe("这是一篇关于测试主题的文章");
    });

    test("应该成功路由写文件工具调用", async () => {
      const toolCall = {
        id: "test-file-1",
        name: "write_file",
        arguments: {
          path: "Documents/test.txt",
          content: "测试内容",
          mode: "create",
        },
      };

      const context = { socketId: "test-socket" };

      // Mock the file writer
      toolRouter.fileWriter.writeFile = jest.fn().mockResolvedValue({
        success: true,
        filePath: "/path/to/test.txt",
        mode: "create",
      });

      const result = await toolRouter.routeAndExecute(toolCall, context);

      expect(result.success).toBe(true);
      expect(result.name).toBe("write_file");
      expect(result.result.path).toBe("/path/to/test.txt");
      expect(result.result.mode).toBe("create");
    });

    test("应该成功路由打开应用工具调用", async () => {
      const toolCall = {
        id: "test-app-1",
        name: "open_app",
        arguments: {
          name: "spotify",
        },
      };

      const context = { socketId: "test-socket" };

      // Mock the system controller
      toolRouter.systemController.openApplication = jest.fn().mockResolvedValue({
        success: true,
        message: "已打开 Spotify",
      });

      const result = await toolRouter.routeAndExecute(toolCall, context);

      expect(result.success).toBe(true);
      expect(result.name).toBe("open_app");
      expect(result.result.message).toContain("打开");
    });

    test("应该处理未知的工具名称", async () => {
      const toolCall = {
        id: "test-unknown-1",
        name: "unknown_tool",
        arguments: {},
      };

      const context = { socketId: "test-socket" };

      const result = await toolRouter.routeAndExecute(toolCall, context);

      expect(result.success).toBe(false);
      expect(result.name).toBe("unknown_tool");
      expect(result.error).toContain("未知的工具");
    });

    test("应该处理工具执行错误", async () => {
      const toolCall = {
        id: "test-error-1",
        name: "play_music",
        arguments: {
          source: "invalid_source",
        },
      };

      const context = { socketId: "test-socket" };

      // Mock the music controller to throw an error
      toolRouter.musicController.playMusic = jest
        .fn()
        .mockRejectedValue(new Error("不支持的音乐来源"));

      const result = await toolRouter.routeAndExecute(toolCall, context);

      expect(result.success).toBe(false);
      expect(result.name).toBe("play_music");
      expect(result.error).toContain("播放音乐失败");
    });
  });

  describe("getSupportedTools", () => {
    test("应该返回支持的工具列表", () => {
      const tools = toolRouter.getSupportedTools();

      expect(tools).toHaveLength(5);
      expect(tools.map((t) => t.name)).toContain("play_music");
      expect(tools.map((t) => t.name)).toContain("stop_music");
      expect(tools.map((t) => t.name)).toContain("write_article");
      expect(tools.map((t) => t.name)).toContain("write_file");
      expect(tools.map((t) => t.name)).toContain("open_app");
    });
  });
});
