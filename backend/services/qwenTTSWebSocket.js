const WebSocket = require("ws");
const logger = require("../utils/logger");

class QwenTTSWebSocket {
  constructor(apiKey) {
    this.apiKey = apiKey;
    this.wsUrl = "wss://dashscope.aliyuncs.com/api-ws/v1/inference/";
    this.ws = null;
    this.isConnected = false;
    this.taskId = null;
    this.audioBuffer = Buffer.alloc(0);
    this.onAudioChunk = null;
    this.taskCompletePromise = null;
    this.taskCompleteResolve = null;
    this.chunkCount = 0;

    // 音频格式参数
    this.sampleRate = 16000;
    this.bitsPerSample = 16;
    this.channels = 1;
    this.audioChunks = []; // 存储音频块
    this.isFirstChunk = true; // 标记首块
    this.wavHeader = null; // WAV头信息
  }

  /**
   * 建立WebSocket连接
   */
  async connect() {
    return new Promise((resolve, reject) => {
      try {
        logger.info("正在建立WebSocket连接...", { url: this.wsUrl });

        this.ws = new WebSocket(this.wsUrl, {
          headers: {
            Authorization: `Bearer ${this.apiKey}`,
            "X-DashScope-WorkMode": "async",
          },
        });

        this.ws.on("open", () => {
          logger.info("WebSocket连接已建立");
          this.isConnected = true;
          resolve();
        });

        this.ws.on("message", (data) => {
          this.handleMessage(data);
        });

        this.ws.on("error", (error) => {
          logger.error("WebSocket连接错误:", error);
          reject(error);
        });

        this.ws.on("close", (code, reason) => {
          logger.info("WebSocket连接已关闭", { code, reason: reason.toString() });
          this.isConnected = false;
        });
      } catch (error) {
        logger.error("建立WebSocket连接失败:", error);
        reject(error);
      }
    });
  }

  /**
   * 处理WebSocket消息
   */
  handleMessage(data) {
    try {
      // 首先尝试解析为JSON（文本消息）
      let message;
      try {
        message = JSON.parse(data.toString());
        this.handleTextMessage(message);
      } catch (_e) {
        // 如果不是JSON，则认为是二进制音频数据
        this.handleBinaryMessage(data);
      }
    } catch (error) {
      logger.error("处理WebSocket消息失败:", error);
    }
  }

  /**
   * 处理文本消息
   */
  handleTextMessage(message) {
    logger.info("收到文本消息:", message);

    if (message.header) {
      const { event, task_id } = message.header;

      switch (event) {
        case "task-started":
          logger.info("任务已开始:", task_id);
          this.taskId = task_id;
          break;

        case "result-generated":
          logger.info("收到结果事件");
          break;

        case "task-finished":
          logger.info("任务已完成");
          if (this.taskCompleteResolve) {
            this.taskCompleteResolve({
              success: true,
              taskId: task_id,
            });
            this.taskCompleteResolve = null;
          }
          break;

        case "task-failed":
          logger.error("任务失败:", message);
          if (this.taskCompleteResolve) {
            this.taskCompleteResolve({
              success: false,
              error: message,
              taskId: task_id,
            });
            this.taskCompleteResolve = null;
          }
          break;

        default:
          logger.info("收到未知事件:", event);
      }
    }
  }

  /**
   * 创建WAV文件头
   */
  createWavHeader(dataLength) {
    const header = Buffer.alloc(44);
    const fileSize = dataLength + 36;
    const byteRate = (this.sampleRate * this.channels * this.bitsPerSample) / 8;

    // RIFF header
    header.write("RIFF", 0);
    header.writeUInt32LE(fileSize, 4);
    header.write("WAVE", 8);

    // fmt chunk
    header.write("fmt ", 12);
    header.writeUInt32LE(16, 16); // Subchunk1Size
    header.writeUInt16LE(1, 20); // AudioFormat (PCM)
    header.writeUInt16LE(this.channels, 22);
    header.writeUInt32LE(this.sampleRate, 24);
    header.writeUInt32LE(byteRate, 28);
    header.writeUInt16LE((this.channels * this.bitsPerSample) / 8, 32);
    header.writeUInt16LE(this.bitsPerSample, 34);

    // data chunk
    header.write("data", 36);
    header.writeUInt32LE(dataLength, 40);

    return header;
  }

  /**
   * 获取总音频大小
   */
  getTotalAudioSize() {
    return this.audioChunks.reduce((total, chunk) => total + chunk.length, 0);
  }

  /**
   * 合并音频块为完整格式
   */
  mergeAudioChunks() {
    if (this.audioChunks.length === 0) return null;

    // 简单的PCM数据合并（假设千问返回的是PCM数据）
    return Buffer.concat(this.audioChunks);
  }

  /**
   * 处理二进制音频数据 - 修复版本
   */
  handleBinaryMessage(data) {
    logger.info("收到音频数据，大小:", data.length);

    // 累积音频数据到缓冲区
    this.audioBuffer = Buffer.concat([this.audioBuffer, data]);
    this.audioChunks.push(data);
    this.chunkCount++;

    // 策略：累积足够的数据再发送，确保有完整的音频格式
    const ACCUMULATE_THRESHOLD = 16000; // 约1秒的16kHz音频数据

    if (
      this.onAudioChunk &&
      typeof this.onAudioChunk === "function" &&
      this.getTotalAudioSize() >= ACCUMULATE_THRESHOLD
    ) {
      // 创建完整的WAV格式音频
      const pcmData = this.mergeAudioChunks();
      if (pcmData) {
        const wavHeader = this.createWavHeader(pcmData.length);
        const completeWav = Buffer.concat([wavHeader, pcmData]);

        logger.info(`发送完整WAV音频，大小: ${completeWav.length} bytes`);
        this.onAudioChunk(completeWav);

        // 重置累积器
        this.audioChunks = [];
      }
    }
  }

  /**
   * 发送语音合成任务
   */
  async sendTTSRequest(text, settings = {}) {
    if (!this.isConnected) {
      throw new Error("WebSocket连接未建立");
    }

    const { voice = "zhizhe_emo", rate = 1.0, pitch = 1.0, model } = settings;

    // 生成唯一的task_id
    const taskId = `task_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;

    const request = {
      header: {
        action: "run-task",
        task_id: taskId,
        // 使用 out 流模式，服务端会以二进制块推送音频
        streaming: "out",
      },
      payload: {
        model: model || "sambert-zhichu-v1",
        task_group: "audio",
        task: "tts",
        function: "SpeechSynthesizer",
        input: {
          text: text,
        },
        parameters: {
          text_type: "PlainText",
          format: "wav",
          sample_rate: 16000,
          volume: 50,
          rate: Math.max(0.5, Math.min(2.0, rate)),
          pitch: Math.max(0.5, Math.min(2.0, pitch)),
          word_timestamp_enabled: false,
          phoneme_timestamp_enabled: false,
          voice: voice,
        },
      },
    };

    logger.info("发送TTS请求:", JSON.stringify(request, null, 2));

    this.ws.send(JSON.stringify(request));

    return taskId;
  }

  /**
   * 流式语音合成 - 修复版本
   */
  async streamSynthesize(text, settings = {}, onAudioChunk) {
    this.onAudioChunk = onAudioChunk;
    this.audioBuffer = Buffer.alloc(0);
    this.audioChunks = []; // 重置音频块数组
    this.chunkCount = 0;

    try {
      if (!this.isConnected) {
        await this.connect();
      }

      // 创建Promise来等待任务完成
      this.taskCompletePromise = new Promise((resolve) => {
        this.taskCompleteResolve = resolve;
      });

      const taskId = await this.sendTTSRequest(text, settings);

      // 等待任务完成或超时
      const result = await Promise.race([
        this.taskCompletePromise,
        new Promise((_, reject) => setTimeout(() => reject(new Error("TTS任务超时")), 30000)),
      ]);

      if (!result.success) {
        throw new Error(result.error?.message || "TTS任务失败");
      }

      // 任务完成后，处理剩余音频数据
      if (this.audioChunks.length > 0 && onAudioChunk && typeof onAudioChunk === "function") {
        // 创建剩余音频的完整WAV格式
        const remainingPcm = this.mergeAudioChunks();
        if (remainingPcm && remainingPcm.length > 0) {
          const wavHeader = this.createWavHeader(remainingPcm.length);
          const finalWav = Buffer.concat([wavHeader, remainingPcm]);

          logger.info(`发送最终WAV音频，大小: ${finalWav.length} bytes`);
          onAudioChunk(finalWav);
        }
      }

      return {
        success: true,
        fullAudio: this.audioBuffer,
        finalResult: { status: "completed" },
        chunkCount: this.chunkCount,
        taskId: taskId,
      };
    } catch (error) {
      logger.error("流式TTS合成失败:", error);
      throw error;
    } finally {
      // 清理Promise引用
      this.taskCompleteResolve = null;
      this.taskCompletePromise = null;
      this.audioChunks = []; // 清理音频块数组
    }
  }

  /**
   * 非流式语音合成
   */
  async synthesize(text, settings = {}) {
    try {
      const result = await this.streamSynthesize(text, settings);

      // 确保返回的是完整的WAV格式音频
      if (result.fullAudio && result.fullAudio.length > 0) {
        // 如果累积的音频数据没有WAV头，添加WAV头
        const audioData = result.fullAudio;

        // 检查是否已经有WAV头
        const hasWavHeader =
          audioData.length >= 44 &&
          audioData.slice(0, 4).toString() === "RIFF" &&
          audioData.slice(8, 12).toString() === "WAVE";

        if (!hasWavHeader) {
          // 添加WAV头
          const wavHeader = this.createWavHeader(audioData.length);
          return Buffer.concat([wavHeader, audioData]);
        }
      }

      return result.fullAudio;
    } catch (error) {
      logger.error("非流式TTS合成失败:", error);
      throw error;
    }
  }

  /**
   * 关闭WebSocket连接
   */
  close() {
    if (this.ws && this.isConnected) {
      this.ws.close();
      this.isConnected = false;
    }
  }
}

module.exports = QwenTTSWebSocket;
