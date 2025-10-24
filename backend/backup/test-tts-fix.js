const TTSService = require("./services/ttsService");
const _logger = require("./utils/logger");

async function testTTS() {
  console.log("开始测试TTS服务...");

  try {
    const ttsService = new TTSService();

    // 检查TTS服务是否可用
    if (!ttsService.isAvailable()) {
      console.error("TTS服务不可用，请检查DASHSCOPE_API_KEY环境变量");
      return;
    }

    console.log("TTS服务可用，开始测试...");

    // 测试文本
    const testText = "你好！这是测试语音合成。";

    // 测试非流式TTS
    console.log("测试非流式TTS...");
    const audioData = await ttsService.textToSpeech(testText, "zh-CN", {
      gender: "female",
      rate: 1.0,
      pitch: 1.0,
    });

    console.log(`非流式TTS测试成功！音频数据长度: ${audioData.length} bytes`);

    // 测试流式TTS
    console.log("测试流式TTS...");
    let totalChunks = 0;
    let _totalBytes = 0;

    const streamResult = await ttsService.streamTextToSpeech(
      testText,
      {
        gender: "female",
        rate: 1.0,
        pitch: 1.0,
      },
      (chunk) => {
        totalChunks++;
        _totalBytes += chunk.length;
        console.log(`收到音频块 ${totalChunks}, 大小: ${chunk.length} bytes`);
      }
    );

    console.log(
      `流式TTS测试成功！总块数: ${streamResult.chunkCount}, 总字节: ${streamResult.fullAudio.length}`
    );

    // 验证音频数据格式
    console.log("验证音频数据格式...");
    console.log(`非流式音频数据类型: ${typeof audioData}`);
    console.log(`非流式音频数据长度: ${audioData.length}`);
    console.log(`流式音频数据类型: ${typeof streamResult.fullAudio}`);
    console.log(`流式音频数据长度: ${streamResult.fullAudio.length}`);

    // 检查音频数据是否有效（非空）
    if (audioData.length > 0 && streamResult.fullAudio.length > 0) {
      console.log("✅ TTS测试全部通过！音频数据有效。");

      // 保存测试音频文件（可选）
      const fs = require("node:fs");
      fs.writeFileSync("/tmp/test-tts-non-stream.pcm", audioData);
      fs.writeFileSync("/tmp/test-tts-stream.pcm", streamResult.fullAudio);
      console.log("测试音频文件已保存到 /tmp/test-tts-*.pcm");
    } else {
      console.error("❌ TTS测试失败：音频数据为空");
    }
  } catch (error) {
    console.error("TTS测试失败:", error);
    console.error("错误堆栈:", error.stack);
  }
}

// 运行测试
testTTS();
