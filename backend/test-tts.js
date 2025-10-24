const TTSService = require("./services/ttsService");
require("dotenv").config();

async function testTTS() {
  console.log("开始测试TTS服务...");

  const ttsService = new TTSService();

  if (!ttsService.isAvailable()) {
    console.error("TTS服务不可用，请检查DASHSCOPE_API_KEY环境变量");
    return;
  }

  const testText = "你好，这是一个测试语音合成。";
  const voiceSettings = {
    gender: "female",
    rate: 1.0,
    pitch: 1.0,
  };

  try {
    console.log("测试文本:", testText);
    console.log("语音设置:", voiceSettings);

    let audioReceived = false;

    const result = await ttsService.streamTextToSpeech(testText, voiceSettings, (audioChunk) => {
      console.log(`收到音频块，大小: ${audioChunk.length} bytes`);
      audioReceived = true;
    });

    console.log("TTS测试完成!");
    console.log("结果:", {
      success: result.success,
      totalAudioLength: result.fullAudio.length,
      chunkCount: result.chunkCount,
      audioReceived: audioReceived,
    });

    if (result.success && result.fullAudio.length > 0) {
      console.log("✅ TTS服务工作正常");
    } else {
      console.log("❌ TTS服务未返回音频数据");
    }
  } catch (error) {
    console.error("❌ TTS测试失败:", error.message);
    console.error("详细错误:", error);
  }
}

testTTS();
