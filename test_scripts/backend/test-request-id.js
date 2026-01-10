const path = require("node:path");

// 复用前端依赖，避免在后端/根目录额外新增依赖
const socketClientPath = path.join(
  __dirname,
  "..",
  "..",
  "..",
  "frontend",
  "node_modules",
  "socket.io-client"
);

// eslint-disable-next-line import/no-dynamic-require, global-require
const { io } = require(socketClientPath);

function generateRequestId(prefix) {
  return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
}

async function main() {
  const serverUrl = process.env.VOICE_ASSISTANT_SERVER_URL || "http://localhost:3001";

  const req1 = generateRequestId("req1");
  const req2 = generateRequestId("req2");

  const seen = {
    assistant: new Set(),
    audioChunk: new Set(),
    audioResponse: new Set(),
    missingRequestId: 0,
  };

  const socket = io(serverUrl, {
    transports: ["websocket", "polling"],
    timeout: 20000,
  });

  const assertHasRequestId = (payload, eventName) => {
    const requestId = payload?.requestId;
    if (!requestId) {
      seen.missingRequestId++;
      throw new Error(`${eventName} 缺少 requestId: ${JSON.stringify(payload)}`);
    }

    if (requestId === req1 || requestId === req2) {
      return requestId;
    }

    throw new Error(
      `${eventName} requestId 不在预期范围: ${requestId}, expected one of: ${req1}, ${req2}`
    );
  };

  socket.on("connect", () => {
    console.log("connected:", socket.id);

    socket.emit("text-command", { text: "你是谁", requestId: req1 });

    // 紧接着发送第二个请求，模拟并发/抢占
    socket.emit("text-command", { text: "写一首关于星星的诗歌", requestId: req2 });
  });

  socket.on("assistant-message", (payload) => {
    if (payload?.type !== "text") {
      return;
    }
    const requestId = assertHasRequestId(payload, "assistant-message");
    seen.assistant.add(requestId);
  });

  socket.on("audio-chunk", (payload) => {
    const requestId = assertHasRequestId(payload, "audio-chunk");
    seen.audioChunk.add(requestId);
  });

  socket.on("audio-response", (payload) => {
    const requestId = assertHasRequestId(payload, "audio-response");
    seen.audioResponse.add(requestId);
  });

  socket.on("error", (err) => {
    console.error("socket error:", err);
  });

  socket.on("connect_error", (err) => {
    throw err;
  });

  await new Promise((resolve) => setTimeout(resolve, 8000));

  socket.disconnect();

  const summary = {
    req1,
    req2,
    assistant: Array.from(seen.assistant),
    audioChunk: Array.from(seen.audioChunk),
    audioResponse: Array.from(seen.audioResponse),
  };

  console.log("summary:", summary);

  if (!seen.assistant.has(req1) || !seen.assistant.has(req2)) {
    throw new Error("未同时观察到两个 requestId 的 assistant-message 文本响应");
  }

  // audio-chunk / audio-response 视 TTS 是否可用而定，不强制必须都有
  console.log("✅ requestId 贯穿后端事件载荷：校验通过");
}

main().catch((err) => {
  console.error("❌ test-request-id failed:", err);
  process.exit(1);
});
