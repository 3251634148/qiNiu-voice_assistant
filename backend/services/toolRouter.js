const logger = require("../utils/logger");
const SystemController = require("./systemController");
const FileWriter = require("./fileWriter");
const MusicController = require("./musicController");
const LLMService = require("./llm");

class ToolRouter {
  constructor() {
    this.systemController = new SystemController();
    this.fileWriter = new FileWriter();
    this.musicController = new MusicController();
    this.llmService = new LLMService();
  }

  async routeAndExecute(toolCall, context = {}) {
    try {
      const { name, arguments: args, id } = toolCall;
      logger.info(`执行工具调用: ${name}`, { args, context });

      let result;

      switch (name) {
        case "play_music":
          result = await this.handlePlayMusic(args, context);
          break;

        case "stop_music":
          result = await this.handleStopMusic(args, context);
          break;

        case "write_article":
          result = await this.handleWriteArticle(args, context);
          break;

        case "write_file":
          result = await this.handleWriteFile(args, context);
          break;

        case "open_app":
          result = await this.handleOpenApp(args, context);
          break;

        default:
          throw new Error(`未知的工具: ${name}`);
      }

      return {
        toolCallId: id,
        name: name,
        success: true,
        result: result,
        timestamp: new Date().toISOString(),
      };
    } catch (error) {
      logger.error(`工具执行失败: ${toolCall.name}`, error);

      return {
        toolCallId: toolCall.id,
        name: toolCall.name,
        success: false,
        error: error.message,
        timestamp: new Date().toISOString(),
      };
    }
  }

  async handlePlayMusic(args, _context) {
    const { source, query } = args;

    try {
      logger.info(`播放音乐 - 来源: ${source}, 查询: ${query}`);

      const result = await this.musicController.playMusic(source, query);

      return {
        action: "play_music",
        message: result.message,
        details: result,
      };
    } catch (error) {
      throw new Error(`播放音乐失败: ${error.message}`);
    }
  }

  async handleStopMusic(_args, _context) {
    try {
      logger.info("停止音乐播放");

      const result = await this.musicController.stopMusic();

      return {
        action: "stop_music",
        message: result.message,
        details: result,
      };
    } catch (error) {
      throw new Error(`停止音乐失败: ${error.message}`);
    }
  }

  async handleWriteArticle(args, _context) {
    const { topic, style = "casual", length = "medium" } = args;

    try {
      logger.info(`写文章 - 主题: ${topic}, 风格: ${style}, 长度: ${length}`);

      const articlePrompt = `请写一篇关于"${topic}"的文章。
风格: ${style}
长度: ${length}

请直接返回文章内容，不需要额外的说明。`;

      const response = await this.llmService.invokeLLM([{ role: "user", content: articlePrompt }]);

      // 保存草稿
      const draftResult = await this.fileWriter.saveDraft(
        response.text,
        `article_${topic}_${Date.now()}.txt`
      );

      return {
        action: "write_article",
        topic: topic,
        style: style,
        length: length,
        content: response.text,
        draftPath: draftResult.filePath,
        message: `已完成关于"${topic}"的文章写作，已保存为草稿`,
      };
    } catch (error) {
      throw new Error(`写文章失败: ${error.message}`);
    }
  }

  async handleWriteFile(args, _context) {
    const { path: filePath, content, mode = "create" } = args;

    try {
      logger.info(`写文件 - 路径: ${filePath}, 模式: ${mode}`);

      const result = await this.fileWriter.writeFile(filePath, content, mode);

      return {
        action: "write_file",
        path: result.filePath,
        mode: mode,
        message: `已${mode === "append" ? "追加" : "写入"}文件: ${filePath}`,
        details: result,
      };
    } catch (error) {
      throw new Error(`写文件失败: ${error.message}`);
    }
  }

  async handleOpenApp(args, _context) {
    const { name } = args;

    try {
      logger.info(`打开应用: ${name}`);

      const result = await this.systemController.openApplication(name);

      return {
        action: "open_app",
        name: name,
        message: `已打开应用程序: ${name}`,
        details: result,
      };
    } catch (error) {
      throw new Error(`打开应用失败: ${error.message}`);
    }
  }

  // 获取支持的工具列表
  getSupportedTools() {
    return [
      {
        name: "play_music",
        description: "播放音乐",
        parameters: ["source", "query"],
      },
      {
        name: "stop_music",
        description: "停止音乐播放",
        parameters: [],
      },
      {
        name: "write_article",
        description: "写文章",
        parameters: ["topic", "style", "length"],
      },
      {
        name: "write_file",
        description: "写文件",
        parameters: ["path", "content", "mode"],
      },
      {
        name: "open_app",
        description: "打开应用程序",
        parameters: ["name"],
      },
    ];
  }
}

module.exports = ToolRouter;
