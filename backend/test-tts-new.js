const TTSService = require("./services/ttsService-new");

async function testNewTTS() {
  console.log("开始测试新的TTS服务...");

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
      fs.writeFileSync("/tmp/test-tts-new.mp3", audioData);
      console.log("测试音频文件已保存到 /tmp/test-tts-new.mp3");
      console.log("✅ 新的TTS服务测试成功！");
    } else {
      console.log("❌ TTS服务返回空音频数据");
    }
  } catch (error) {
    console.error("新的TTS测试失败:", error);
    console.error("错误堆栈:", error.stack);
  }
}

// 运行测试
testNewTTS();
