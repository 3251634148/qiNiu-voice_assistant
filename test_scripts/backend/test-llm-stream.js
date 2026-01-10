const LLMService = require("../../backend/services/llm");
require("dotenv").config({ path: "../../backend/.env" });

async function testLLMStream() {
  const llm = new LLMService();

  const messages = [{ role: "user", content: "用一句话介绍你自己，越短越好。" }];

  let firstDeltaAt = null;
  const startAt = Date.now();

  const result = await llm.streamText(messages, {
    onDelta: (delta) => {
      if (firstDeltaAt === null) {
        firstDeltaAt = Date.now();
      }
      process.stdout.write(delta);
    },
  });

  console.log("\n\n---");
  console.log("模型:", result.model);
  console.log("总耗时(ms):", Date.now() - startAt);
  console.log("首token延迟(ms):", firstDeltaAt ? firstDeltaAt - startAt : null);
}

testLLMStream().catch((err) => {
  console.error("testLLMStream failed:", err);
  process.exit(1);
});
