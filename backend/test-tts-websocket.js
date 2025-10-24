const TTSService = require("./services/ttsService");

async function testWebSocketTTS() {
  console.log("开始测试WebSocket TTS服务...");

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

    console.log(`非流式TTS测试完成！音频数据长度: ${audioData.length} bytes`);

    // 如果音频数据有效，保存测试文件
    if (audioData.length > 0) {
      const fs = require("node:fs");
      fs.writeFileSync("/tmp/test-tts-websocket.wav", audioData);
      console.log("测试音频文件已保存到 /tmp/test-tts-websocket.wav");
      console.log("✅ WebSocket TTS服务测试成功！");
    } else {
      console.log("❌ TTS服务返回空音频数据");
    }
  } catch (error) {
    console.error("WebSocket TTS测试失败:", error);
    console.error("错误堆栈:", error.stack);
  }
}

// 运行测试
testWebSocketTTS();
