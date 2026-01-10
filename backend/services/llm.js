const OpenAI = require("openai");
const logger = require("../utils/logger");

class LLMService {
  constructor() {
    // 仅从环境变量读取千问API Key，避免把密钥硬编码进代码
    const apiKey = process.env.DASHSCOPE_API_KEY;

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

    this.systemPrompt = `你是一位友好、自然、口语化的电脑语音助手。你的目标是和用户进行顺畅的对话式交流，理解用户的意图并把它转换为具体可执行的电脑操作或直接给出有用的回复。

对话风格要求：
- 用口语化、简洁、有人情味的语气说话，就像在和用户聊天
- 禁止使用任何 Markdown 或特殊符号：不要使用 #、*、-、\`\`、列表编号等
- 不要输出与语义无关的符号或装饰，不要输出表情或表情符号
- 避免罗列"一、二、三"或"1. 2. 3."，用自然句子表达要点
- 句子尽量短而清楚，适合语音朗读，避免堆砌标点和冗长段落
- 如需要澄清，只提出一个关键问题，不要连续追问
- 回答优先直接给结论，必要时再给一句简短的下一步建议

回复长度控制（非常重要）：
- 默认尽量控制在50字以内，能更短就更短
- 不要为了追求字数而截断语义；如果必须解释清楚，可以适当变长
- 用户明确要求详细回答、朗读诗歌、讲故事等场景：可以放宽，但仍尽量精炼
- 如果用户说"简短回答"、"一句话"等，必须严格遵守

工具调用说明：
- 如确需执行操作，请先用自然语言简短确认意图，再进行工具调用
- 仅可使用以下工具：play_music、stop_music、write_article、write_file、open_app
- 危险或高风险操作应提示用户确认
- stop_music 工具仅在用户明确要求停止、暂停、关闭音乐时才使用

始终以中文为主，保留必要的英文专有名词。输出必须是纯文本、适合TTS朗读，不包含任何与朗读无关的符号。`;

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
          // source 可选：缺省时由后端自动选择可用播放器/来源
        },
      },
      {
        name: "stop_music",
        description: "停止当前正在播放的音乐。仅当用户明确要求停止音乐、暂停音乐、关闭音乐时才调用此工具。不要在用户没有提及音乐的情况下调用。",
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
        max_tokens: 512,
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

  /**
   * 纯文本流式输出（用于低延迟语音回复）。
   * 注意：此模式不启用工具调用，避免“先开口后发现需要工具调用”导致的体验问题。
   *
   * @param {Array<{role: string, content: string}>} messages - 对话消息（不含system）
   * @param {Object} options - 选项
   * @param {string|null} options.modelSelect - 指定模型
   * @param {(delta: string) => void} [options.onDelta] - 增量文本回调
   * @returns {Promise<{text: string, model: string}>}
   */
  async streamText(messages, options = {}) {
    const { modelSelect = null, onDelta = null } = options;

    try {
      const model = modelSelect || this.selectModel(messages);
      logger.info(`调用千问LLM流式服务，模型: ${model}`);

      const stream = await this.openai.chat.completions.create({
        model,
        messages: [{ role: "system", content: this.systemPrompt }, ...messages],
        temperature: 0.7,
        max_tokens: 512,
        stream: true,
      });

      let fullText = "";

      for await (const part of stream) {
        const delta = part?.choices?.[0]?.delta?.content;
        if (!delta) {
          continue;
        }

        fullText += delta;
        if (onDelta && typeof onDelta === "function") {
          onDelta(delta);
        }
      }

      return { text: fullText, model };
    } catch (error) {
      logger.error("千问LLM流式调用失败:", error);
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
