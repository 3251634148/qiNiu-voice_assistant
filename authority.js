const WebSocket = require("ws");
const fs = require("node:fs");
const { v4: uuidv4 } = require("uuid");

// 若没有将API Key配置到环境变量，可将下行替换为：apiKey = 'your_api_key'。不建议在生产环境中直接将API Key硬编码到代码中，以减少API Key泄露风险。
const apiKey = process.env.DASHSCOPE_API_KEY;
const wsUrl = "wss://dashscope.aliyuncs.com/api-ws/v1/inference/"; // WebSocket服务器地址
const outputFilePath = "output.mp3"; // 替换为您的音频文件路径

async function main() {
  await checkAndClearOutputFile(outputFilePath);
  createWebSocketConnection();
}

const fileStream = fs.createWriteStream(outputFilePath, { flags: "a" });
function createWebSocketConnection() {
  const ws = new WebSocket(wsUrl, {
    headers: {
      Authorization: `bearer ${apiKey}`,
      "X-DashScope-DataInspection": "enable",
    },
  });

  ws.on("open", () => {
    console.log("已连接到WebSocket服务器");
    sendRunTaskMessage(ws);
  });

  ws.on("message", (data, isBinary) => handleWebSocketMessage(data, isBinary, ws));
  ws.on("error", (error) => console.error("WebSocket错误:", error));
  ws.on("close", () => console.log("WebSocket连接已关闭"));

  return ws;
}

function sendRunTaskMessage(ws) {
  const taskId = uuidv4();
  const runTaskMessage = {
    header: {
      action: "run-task",
      task_id: taskId,
      streaming: "out",
    },
    payload: {
      model: "sambert-zhichu-v1",
      task_group: "audio",
      task: "tts",
      function: "SpeechSynthesizer",
      input: {
        text: "白日依山尽，黄河入海流。欲穷千里目，更上一层楼。",
      },
      parameters: {
        text_type: "PlainText",
        format: "mp3",
        sample_rate: 16000,
        volume: 50,
        rate: 1,
        pitch: 1,
        word_timestamp_enabled: true,
        phoneme_timestamp_enabled: true,
      },
    },
  };
  ws.send(JSON.stringify(runTaskMessage));
  console.log("run-task指令已发送");
}

function handleWebSocketMessage(data, isBinary, ws) {
  if (isBinary) {
    fileStream.write(data);
  } else {
    const message = JSON.parse(data);
    handleWebSocketEvent(message, ws);
  }
}

function handleWebSocketEvent(message, ws) {
  switch (message.header.event) {
    case "task-started":
      console.log("任务已启动");
      break;
    case "result-generated":
      console.log("结果已生成");
      break;
    case "task-finished":
      console.log("任务已完成");
      ws.close();
      fileStream.end(() => {
        console.log("文件流已关闭");
      });
      break;
    case "task-failed":
      console.error("任务失败：", message.header.error_message);
      ws.close();
      fileStream.end(() => {
        console.log("文件流已关闭");
      });
      break;
    default:
      console.log("未知事件：", message.header.event);
  }
}

function checkAndClearOutputFile(filePath) {
  return new Promise((resolve, reject) => {
    fs.access(filePath, fs.F_OK, (err) => {
      if (!err) {
        fs.truncate(filePath, 0, (truncateErr) => {
          if (truncateErr) return reject(truncateErr);
          console.log("文件已清空");
          resolve();
        });
      } else {
        fs.open(filePath, "w", (openErr) => {
          if (openErr) return reject(openErr);
          console.log("文件已创建");
          resolve();
        });
      }
    });
  });
}

main().catch(console.error);
