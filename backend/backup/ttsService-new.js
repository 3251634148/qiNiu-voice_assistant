const OpenAI = require("openai");
const logger = require("../utils/logger");

class QwenTTSStream {
  constructor(apiKey) {
    this.apiKey = apiKey;
    // 使用OpenAI兼容模式
    this.openai = new OpenAI({
      apiKey: apiKey,
      baseURL: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    });
  }

  /**
   * 流式语音合成（使用OpenAI兼容模式）
   * @param {string} text - 要合成的文本
   * @param {Object} settings - TTS设置
   * @param {string} settings.voice - 音色名称
   * @param {number} settings.rate - 语速 (0.5-2.0)
   * @param {number} settings.pitch - 音调 (0.5-2.0)
   * @param {function} onAudioChunk - 音频片段回调函数
   * @returns {Promise<Object>} 合成结果
   */
  async streamSynthesize(text, settings = {}, onAudioChunk) {
    const {
      voice = "zhizhe_emo", // 使用官方推荐的音色
      rate = 1.0,
      pitch = 1.0,
    } = settings;

    logger.info(`开始千问TTS流式合成: ${text.substring(0, 50)}...`);
    logger.info(`TTS设置: voice=${voice}, rate=${rate}, pitch=${pitch}`);

    try {
      // 使用OpenAI兼容的TTS API
      const mp3 = await this.openai.audio.speech.create({
        model: "tts-1",
        voice: "alloy", // OpenAI兼容的音色
        input: text,
        speed: rate,
      });

      logger.info(`TTS合成成功，音频数据大小: ${mp3.body.size} bytes`);

      // 将音频数据转换为Buffer
      const arrayBuffer = await mp3.arrayBuffer();
      const audioBuffer = Buffer.from(arrayBuffer);

      logger.info(`音频数据转换成功，Buffer大小: ${audioBuffer.length} bytes`);

      // 回调音频数据
      if (onAudioChunk) {
        onAudioChunk(audioBuffer);
      }

      return {
        success: true,
        fullAudio: audioBuffer,
        finalResult: { status: "completed" },
        chunkCount: 1,
      };
    } catch (error) {
      logger.error("千问TTS流式合成失败:", error);
      throw error;
    }
  }

  /**
   * 非流式语音合成（使用OpenAI兼容模式）
   * @param {string} text - 要合成的文本
   * @param {Object} settings - TTS设置
   * @returns {Promise<Buffer>} 音频数据
   */
  async synthesize(text, settings = {}) {
    try {
      logger.info(`使用非流式千问TTS合成: ${text.substring(0, 50)}...`);

      const { rate = 1.0 } = settings;

      // 使用OpenAI兼容的TTS API
      const mp3 = await this.openai.audio.speech.create({
        model: "tts-1",
        voice: "alloy", // OpenAI兼容的音色
        input: text,
        speed: rate,
      });

      logger.info(`非流式TTS合成成功，音频数据大小: ${mp3.body.size} bytes`);

      // 将音频数据转换为Buffer
      const arrayBuffer = await mp3.arrayBuffer();
      const audioBuffer = Buffer.from(arrayBuffer);

      logger.info(`音频数据转换成功，Buffer大小: ${audioBuffer.length} bytes`);

      return audioBuffer;
    } catch (error) {
      logger.error("非流式千问TTS合成失败:", error);
      throw error;
    }
  }

  /**
   * 获取可用的音色列表
   * @returns {Array<string>} 音色列表
   */
  getAvailableVoices() {
    return [
      "zhizhe_emo", // 知性女声 - 官方推荐
      "zhishuo_emo", // 知说女声
      "zhibei_emo", // 知北女声
      "zhixia_emo", // 知夏女声
      "zhiyun_emo", // 知云女声
      "zhigui_emo", // 知桂女声
      "zhiya_emo", // 知雅女声
      "zhishuang_emo", // 知爽女声
    ];
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

class TTSService {
  constructor() {
    // 只使用千问TTS
    this.qwenApiKey = process.env.DASHSCOPE_API_KEY;

    if (!this.qwenApiKey) {
      logger.warn("未设置DASHSCOPE_API_KEY环境变量，TTS功能将不可用");
    }

    this.qwenTTS = new QwenTTSStream(this.qwenApiKey);
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
        voice: this.qwenTTS.mapVoiceFromGender(voiceSettings.gender || "female"),
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
        voice: this.qwenTTS.mapVoiceFromGender(voiceSettings.gender || "female"),
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
}

module.exports = TTSService;
