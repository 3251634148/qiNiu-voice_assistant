const logger = require("../utils/logger");

class SpeechService {
  constructor() {
    // 简化版本 - 只支持Web Speech API
    logger.info("语音识别服务初始化 - 使用Web Speech API模式");
  }

  async speechToText(audioData, language = "zh-CN", provider = "web") {
    try {
      logger.info(`使用 ${provider} 进行语音识别`);

      switch (provider) {
        case "web":
          return await this.webSpeechToText(audioData, language);
        default:
          // 默认使用Web Speech API
          return await this.webSpeechToText(audioData, language);
      }
    } catch (error) {
      logger.error("语音识别失败:", error);
      throw new Error(`语音识别失败: ${error.message}`);
    }
  }

  async webSpeechToText(_audioData, _language) {
    // Web Speech API应该在前端使用，后端不应该处理语音识别
    // 这个方法不应该被调用，如果被调用说明架构有问题
    logger.warn("webSpeechToText被调用，但Web Speech API应该在前端使用");
    throw new Error("语音识别应该在前端使用Web Speech API完成");
  }
}

module.exports = SpeechService;
