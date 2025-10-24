// 此文件已被弃用，请使用 conversationController.js 和 llm.js
// 保留此文件仅为向后兼容

const logger = require("../utils/logger");

class VoiceAssistant {
  constructor() {
    logger.warn("VoiceAssistant类已弃用，请使用ConversationController和LLMService");
  }

  async speechToText(_audioData, _language = "zh-CN", _provider = "openai") {
    throw new Error("VoiceAssistant已弃用，请使用新的架构");
  }

  async processCommand(_text) {
    throw new Error("VoiceAssistant已弃用，请使用新的架构");
  }

  async textToSpeech(_text, _language = "zh-CN", _provider = "openai") {
    throw new Error("VoiceAssistant已弃用，请使用新的架构");
  }

  shouldUseGPT4(_text) {
    throw new Error("VoiceAssistant已弃用，请使用新的架构");
  }

  async getCapabilities() {
    throw new Error("VoiceAssistant已弃用，请使用新的架构");
  }
}

module.exports = VoiceAssistant;
