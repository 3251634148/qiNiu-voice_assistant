const logger = require("../utils/logger");
const QwenTTSWebSocket = require("./qwenTTSWebSocket");

class TTSService {
  constructor() {
    // 只使用千问TTS
    this.qwenApiKey = process.env.DASHSCOPE_API_KEY;

    if (!this.qwenApiKey) {
      logger.warn("未设置DASHSCOPE_API_KEY环境变量，TTS功能将不可用");
    }

    this.qwenTTS = new QwenTTSWebSocket(this.qwenApiKey);
  }

  /**
   * 语音合成主方法
   * @param {string} text - 要合成的文本
   * @param {string} language - 语言（暂时只支持中文）
   * @param {Object} voiceSettings - 语音设置
   * @param {string} voiceSettings.gender - 音色性别
   * @param {number} voiceSettings.rate - 语速
   * @param {number} voiceSettings.pitch - 音调
   * @param {function} onAudioChunk - 流式音频回调（可选）
   * @returns {Promise<Buffer>} 音频数据
   */
  async textToSpeech(text, voiceSettings = {}, onAudioChunk = null) {
    try {
      if (!this.qwenApiKey) {
        logger.warn("千问TTS未配置，返回空音频");
        return Buffer.from("");
      }

      // 映射语音设置
      const settings = {
        voice: this.mapVoiceFromGender(voiceSettings.gender || "female"),
        rate: voiceSettings.rate || 1.0,
        pitch: voiceSettings.pitch || 1.0,
      };

      logger.info(`使用千问TTS进行语音合成: ${text.substring(0, 50)}...`);

      if (onAudioChunk) {
        // 流式合成
        const result = await this.qwenTTS.streamSynthesize(text, settings, onAudioChunk);
        return result.fullAudio;
      } else {
        // 非流式合成
        return await this.qwenTTS.synthesize(text, settings);
      }
    } catch (error) {
      logger.error("千问TTS语音合成失败:", error);
      // 返回空音频而不是抛出错误，保证系统稳定性
      logger.info("返回空音频，前端将使用Web Speech API");
      return Buffer.from("");
    }
  }

  /**
   * 流式语音合成
   * @param {string} text - 要合成的文本
   * @param {Object} voiceSettings - 语音设置
   * @param {function} onAudioChunk - 音频片段回调函数
   * @returns {Promise<Object>} 合成结果
   */
  async streamTextToSpeech(text, voiceSettings = {}, onAudioChunk) {
    try {
      if (!this.qwenApiKey) {
        throw new Error("千问TTS未配置");
      }

      const settings = {
        voice: this.mapVoiceFromGender(voiceSettings.gender || "female"),
        rate: voiceSettings.rate || 1.0,
        pitch: voiceSettings.pitch || 1.0,
      };

      return await this.qwenTTS.streamSynthesize(text, settings, onAudioChunk);
    } catch (error) {
      logger.error("千问流式TTS失败:", error);
      throw error;
    }
  }

  /**
   * 获取可用音色列表
   * @returns {Array<Object>} 音色列表
   */
  getAvailableVoices() {
    return [
      { id: "female", name: "女声", provider: "qwen" },
      { id: "male", name: "男声", provider: "qwen" },
    ];
  }

  /**
   * 检查TTS服务是否可用
   * @returns {boolean} 是否可用
   */
  isAvailable() {
    return !!this.qwenApiKey;
  }

  /**
   * 根据前端设置映射音色
   * @param {string} gender - 性别 ('male' | 'female')
   * @returns {string} 千问音色名称
   */
  mapVoiceFromGender(gender) {
    // 阿里云DashScope支持的音色映射 - 使用官方音色名称
    const voiceMap = {
      female: "zhizhe_emo", // 知性女声 - 官方推荐
      male: "zhishuo_emo", // 知说男声 - 官方推荐
    };
    return voiceMap[gender] || "zhizhe_emo";
  }
}

module.exports = TTSService;
