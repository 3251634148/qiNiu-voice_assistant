const logger = require("../utils/logger");
const QwenTTSWebSocket = require("./qwenTTSWebSocket");

class QwenTTSStream {
  constructor(apiKey) {
    this.apiKey = apiKey;
    this.websocket = new QwenTTSWebSocket(apiKey);
  }

  /**
   * 流式语音合成（使用WebSocket方式）
   * @param {string} text - 要合成的文本
   * @param {Object} settings - TTS设置
   * @param {string} settings.voice - 音色名称
   * @param {number} settings.rate - 语速 (0.5-2.0)
   * @param {number} settings.pitch - 音调 (0.5-2.0)
   * @param {function} onAudioChunk - 音频片段回调函数
   * @returns {Promise<Object>} 合成结果
   */
  async streamSynthesize(text, settings = {}, onAudioChunk) {
    return new Promise((resolve, reject) => {
      const {
        voice = "zhizhe_emo", // 使用官方推荐的音色
        rate = 1.0,
        pitch = 1.0,
      } = settings;

      logger.info(`开始千问TTS流式合成: ${text.substring(0, 50)}...`);
      logger.info(`TTS设置: voice=${voice}, rate=${rate}, pitch=${pitch}`);

      // 根据阿里云官方文档构建请求体 - 使用正确的TTS格式
      const requestPayload = {
        model: "sambert-zhichu-v1", // 使用官方TTS模型
        input: {
          text: text,
        },
        parameters: {
          voice: voice, // 音色名称
          volume: 50, // 音量 0-100
          speech_rate: Math.round((rate - 1) * 100), // 语速 -500 到 500
          pitch_rate: Math.round((pitch - 1) * 100), // 音调 -500 到 500
        },
      };

      logger.info("千问TTS请求参数:", JSON.stringify(requestPayload, null, 2));

      // 使用HTTPS模块发送请求以支持流式响应
      const postData = JSON.stringify(requestPayload);

      const options = {
        hostname: "dashscope.aliyuncs.com",
        port: 443,
        path: "/api/v1/services/aigc/tts?task=tts", // 添加task查询参数
        method: "POST",
        headers: {
          Authorization: `Bearer ${this.apiKey}`,
          "Content-Type": "application/json",
          "X-DashScope-SSE": "enable", // 启用SSE流式响应
          "Content-Length": Buffer.byteLength(postData),
        },
      };

      const req = https.request(options, (res) => {
        logger.info(`响应状态码: ${res.statusCode}`);
        logger.info(`响应头:`, res.headers);

        if (res.statusCode !== 200) {
          let errorData = "";
          res.on("data", (chunk) => {
            errorData += chunk.toString();
          });
          res.on("end", () => {
            logger.error(`TTS HTTP错误响应: ${errorData}`);
            reject(new Error(`HTTP ${res.statusCode}: ${res.statusMessage} - ${errorData}`));
          });
          return;
        }

        let audioBuffer = Buffer.alloc(0);
        let chunkCount = 0;
        let buffer = ""; // 用于累积不完整的JSON数据
        const _isFirstChunk = true;

        // 处理流式数据 - 实时处理SSE事件
        res.on("data", (chunk) => {
          try {
            buffer += chunk.toString();
            logger.info(`收到数据块，长度: ${chunk.length}`);

            // 处理累积的缓冲区数据
            const lines = buffer.split("\n");
            // 保留最后一行，因为它可能不完整
            buffer = lines.pop() || "";

            for (let line of lines) {
              line = line.trim();
              if (!line) continue;

              logger.info(`处理SSE行: "${line.substring(0, 100)}..."`);

              // 处理SSE格式的数据行
              if (line.startsWith("data: ")) {
                const data = line.substring(6).trim();
                if (data === "[DONE]" || data === "") {
                  logger.info("收到SSE结束标记");
                  continue;
                }

                try {
                  const parsed = JSON.parse(data);
                  logger.info(`解析SSE JSON数据:`, {
                    hasOutput: !!parsed.output,
                    hasAudio: !!parsed.output?.audio,
                    hasData: !!parsed.output?.audio?.data,
                    dataLength: parsed.output?.audio?.data?.length || 0,
                  });

                  // 检查是否有音频数据
                  if (parsed.output?.audio?.data && parsed.output.audio.data !== "") {
                    // 解码base64音频数据
                    const audioChunk = Buffer.from(parsed.output.audio.data, "base64");
                    audioBuffer = Buffer.concat([audioBuffer, audioChunk]);
                    chunkCount++;

                    logger.info(
                      `收到音频块 ${chunkCount}, 长度: ${audioChunk.length} bytes, 总长度: ${audioBuffer.length} bytes`
                    );

                    // 立即回调音频块，实现真正的流式播放
                    if (onAudioChunk) {
                      try {
                        onAudioChunk(audioChunk);
                      } catch (callbackError) {
                        logger.error("音频块回调失败:", callbackError);
                      }
                    }
                  } else if (parsed.output?.sentence) {
                    // 有些响应可能包含句子信息
                    logger.info(`收到句子信息: ${parsed.output.sentence}`);
                  }

                  // 检查是否完成
                  if (parsed.output && parsed.output.finish_reason === "stop") {
                    logger.info("TTS合成完成标记收到");
                  }
                } catch (parseError) {
                  logger.error(`解析JSON失败: ${parseError.message}, 数据: "${line}"`);
                }
              }
            }
          } catch (error) {
            logger.error("处理数据块失败:", error);
          }
        });

        res.on("end", () => {
          try {
            // 处理最后剩余的数据
            if (buffer.trim()) {
              logger.info(`处理最后剩余数据: "${buffer}"`);
              if (buffer.startsWith("data: ")) {
                const data = buffer.substring(6).trim();
                if (data !== "[DONE]" && data !== "") {
                  try {
                    const parsed = JSON.parse(data);
                    if (parsed.output?.audio?.data && parsed.output.audio.data !== "") {
                      const audioChunk = Buffer.from(parsed.output.audio.data, "base64");
                      audioBuffer = Buffer.concat([audioBuffer, audioChunk]);
                      chunkCount++;
                      logger.info(`最后音频块 ${chunkCount}, 长度: ${audioChunk.length} bytes`);
                      if (onAudioChunk) {
                        onAudioChunk(audioChunk);
                      }
                    }
                  } catch (parseError) {
                    logger.error(`最后数据解析失败: ${parseError.message}`);
                  }
                }
              }
            }

            logger.info(
              `千问TTS流式合成完成，总音频长度: ${audioBuffer.length} bytes, 总块数: ${chunkCount}`
            );

            resolve({
              success: true,
              fullAudio: audioBuffer,
              finalResult: { status: "completed" },
              chunkCount: chunkCount,
            });
          } catch (error) {
            logger.error("处理TTS响应结束失败:", error);
            reject(error);
          }
        });

        res.on("error", (error) => {
          logger.error("响应流错误:", error);
          reject(error);
        });
      });

      req.on("error", (error) => {
        logger.error("请求错误:", error);
        reject(error);
      });

      // 发送请求数据
      req.write(postData);
      req.end();
    });
  }

  /**
   * 非流式语音合成（备用方法）
   * @param {string} text - 要合成的文本
   * @param {Object} settings - TTS设置
   * @returns {Promise<Buffer>} 音频数据
   */
  async synthesize(text, settings = {}) {
    try {
      logger.info(`使用非流式千问TTS合成: ${text.substring(0, 50)}...`);

      const { voice = "zhizhe_emo", rate = 1.0, pitch = 1.0 } = settings;

      // 使用同步TTS API - 使用正确的阿里云DashScope TTS API
      const requestPayload = {
        model: "sambert-zhichu", // 使用官方文档推荐的具体TTS模型，不带版本号
        input: {
          text: text,
        },
        parameters: {
          voice: voice, // 使用原始音色名称，不需要转小写
          volume: 50,
          speech_rate: Math.round((rate - 1) * 100), // -500 到 500
          pitch_rate: Math.round((pitch - 1) * 100), // -500 到 500
        },
      };

      const postData = JSON.stringify(requestPayload);

      const options = {
        hostname: "dashscope.aliyuncs.com",
        port: 443,
        path: "/api/v1/services/aigc/tts?task=tts", // 添加task查询参数
        method: "POST",
        headers: {
          Authorization: `Bearer ${this.apiKey}`,
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(postData),
        },
      };

      return new Promise((resolve, reject) => {
        const req = https.request(options, (res) => {
          let responseData = "";

          res.on("data", (chunk) => {
            responseData += chunk.toString();
          });

          res.on("end", () => {
            try {
              logger.info(`非流式TTS响应状态: ${res.statusCode}, 数据长度: ${responseData.length}`);
              logger.info(`非流式TTS原始响应: ${responseData.substring(0, 500)}`);

              if (res.statusCode === 200) {
                const parsed = JSON.parse(responseData);

                if (parsed.output?.audio?.data) {
                  const audioData = Buffer.from(parsed.output.audio.data, "base64");
                  logger.info(`非流式TTS合成成功，音频长度: ${audioData.length} bytes`);
                  resolve(audioData);
                } else {
                  logger.error("非流式TTS响应格式错误:", parsed);
                  reject(new Error("TTS响应格式错误"));
                }
              } else {
                logger.error(`非流式TTS HTTP错误: ${res.statusCode}, 响应: ${responseData}`);
                reject(new Error(`HTTP ${res.statusCode}: ${responseData}`));
              }
            } catch (error) {
              logger.error("非流式TTS处理失败:", error);
              reject(error);
            }
          });
        });

        req.on("error", (error) => {
          logger.error("非流式TTS请求失败:", error);
          reject(error);
        });

        req.write(postData);
        req.end();
      });
    } catch (error) {
      logger.error("千问TTS合成失败:", error);
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
