## 2026-01-11

### ✨ 功能增强
- 增加 LLM 单次返回的结构化意图 `INTENT_JSON` 协议，支持提问/操作/混合三态，并预留 `send_message`/`write_run_code`/`file_control` 等扩展动作。
- 后端将意图透传给前端，用于本地展示与调试。

### 🔧 问题修复
- 修复新请求开始时因前端 `stopAllRef` 短暂置位导致的音频块丢弃（表现为“开头内容没念”）。

### 📝 修改的文件
- `backend/services/llm.js`
- `backend/controllers/conversationController.js`
- `backend/services/toolRouter.js`
- `backend/utils/safety.js`
- `frontend/src/hooks/useSocket.ts`
- `frontend/src/components/MessageList.tsx`
