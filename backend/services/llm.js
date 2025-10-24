const OpenAI = require("openai");
const logger = require("../utils/logger");

class LLMService {
  constructor() {
    // 确保只使用千问API，不依赖任何OpenAI配置
    const apiKey = process.env.DASHSCOPE_API_KEY || "sk-846133080d6247e6a6ae8d2cd44e8d02";

    if (!apiKey) {
      throw new Error("千问API密钥未配置，请设置DASHSCOPE_API_KEY环境变量");
    }

    this.openai = new OpenAI({
      apiKey: apiKey,
      baseURL: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    });

    logger.info("千问LLM服务初始化成功", {
      baseURL: "https://dashscope.aliyuncs.com/compatible-mode/v1",
      hasApiKey: !!apiKey,
    });

    this.systemPrompt = `你是一个智能语音助手，专门帮助用户通过语音控制电脑。你的任务是理解用户的自然语言指令，并将其转换为具体的电脑操作。

你可以调用以下工具来帮助用户：
- play_music: 播放音乐（支持音乐源：spotify、apple、local）
- stop_music: 停止音乐播放
- write_article: 写文章（需要主题、风格、长度参数）
- write_file: 写文件（需要路径、内容、模式参数）
- open_app: 打开应用程序（需要应用名称）

请根据用户的指令选择合适的工具调用。如果不需要执行任何操作，直接回复用户即可。

重要规则：
1. 只能调用上述定义的工具
2. 如果用户指令不明确，请询问澄清
3. 对于危险操作，需要提醒用户确认
4. 始终保持友好和专业的语调`;

    this.functionDefinitions = [
      {
        name: "play_music",
        description: "播放音乐",
        parameters: {
          type: "object",
          properties: {
            source: {
              type: "string",
              enum: ["spotify", "apple", "local"],
              description: "音乐来源",
            },
            query: {
              type: "string",
              description: "搜索的歌曲或艺术家名称（可选）",
            },
          },
          required: ["source"],
        },
      },
      {
        name: "stop_music",
        description: "停止音乐播放",
        parameters: {
          type: "object",
          properties: {},
        },
      },
      {
        name: "write_article",
        description: "写文章",
        parameters: {
          type: "object",
          properties: {
            topic: {
              type: "string",
              description: "文章主题",
            },
            style: {
              type: "string",
              enum: ["formal", "casual", "professional", "creative"],
              description: "写作风格",
            },
            length: {
              type: "string",
              enum: ["short", "medium", "long"],
              description: "文章长度",
            },
          },
          required: ["topic"],
        },
      },
      {
        name: "write_file",
        description: "写文件",
        parameters: {
          type: "object",
          properties: {
            path: {
              type: "string",
              description: "文件路径",
            },
            content: {
              type: "string",
              description: "文件内容",
            },
            mode: {
              type: "string",
              enum: ["create", "append", "overwrite"],
              description: "写入模式",
            },
          },
          required: ["path", "content"],
        },
      },
      {
        name: "open_app",
        description: "打开应用程序",
        parameters: {
          type: "object",
          properties: {
            name: {
              type: "string",
              description: "应用程序名称",
            },
          },
          required: ["name"],
        },
      },
    ];
  }

  async invokeLLM(messages, functions = null, modelSelect = null) {
    try {
      logger.info(`调用千问LLM服务，模型: ${modelSelect || "qwen-plus"}`);

      const model = modelSelect || this.selectModel(messages);
      const tools = functions || this.functionDefinitions;

      const completion = await this.openai.chat.completions.create({
        model: model,
        messages: [{ role: "system", content: this.systemPrompt }, ...messages],
        temperature: 0.7,
        max_tokens: 1000,
        tools: tools.map((tool) => ({
          type: "function",
          function: tool,
        })),
        tool_choice: "auto",
      });

      const responseMessage = completion.choices[0].message;

      return {
        text: responseMessage.content,
        toolCalls: responseMessage.tool_calls || [],
        model: model,
        usage: completion.usage,
      };
    } catch (error) {
      logger.error("千问LLM调用失败:", error);
      throw new Error(`千问LLM服务调用失败: ${error.message}`);
    }
  }

  parseToolCalls(response) {
    if (!response.toolCalls || response.toolCalls.length === 0) {
      return [];
    }

    return response.toolCalls.map((toolCall) => ({
      id: toolCall.id,
      name: toolCall.function.name,
      arguments: JSON.parse(toolCall.function.arguments),
      type: "function",
    }));
  }

  selectModel(messages) {
    const lastMessage = messages[messages.length - 1]?.content || "";

    // 千问模型选择策略
    if (this.shouldUseAdvancedModel(lastMessage)) {
      return "qwen-turbo"; // 更强大的模型
    }

    return "qwen-plus"; // 默认使用qwen-plus
  }

  shouldUseAdvancedModel(text) {
    const complexKeywords = [
      "写",
      "创建",
      "生成",
      "分析",
      "总结",
      "翻译",
      "解释",
      "compose",
      "create",
      "generate",
      "analyze",
      "summarize",
      "article",
      "document",
      "report",
      "email",
    ];

    return complexKeywords.some((keyword) => text.includes(keyword)) || text.length > 100;
  }

  async invokeQwen(messages, functions = null) {
    // 直接使用千问API，此方法现在与invokeLLM相同
    return await this.invokeLLM(messages, functions, "qwen-plus");
  }

  getAvailableModels() {
    return {
      qwen: ["qwen-plus", "qwen-turbo", "qwen-max"],
    };
  }

  getFunctionDefinitions() {
    return this.functionDefinitions;
  }
}

module.exports = LLMService;
