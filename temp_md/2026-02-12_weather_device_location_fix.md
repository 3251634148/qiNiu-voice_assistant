## 2026-02-12 - 天气稳定性增强、设备定位、SAY 输出清洗与音乐兜底收紧

### 背景
用户反馈两类问题：
1. “讲故事/听故事”被误判为播放音乐，说明后端音乐兜底识别过于宽松。
2. 询问“当前位置天气”时：
   - 回复内容包含 `SAY:` 前缀（协议字段泄漏到用户可见文本）；
   - IP 定位城市出现偏差（例如深圳误判为广州）；
   - 回复缺少未来趋势与贴心建议。

### 目标
- **输出纯净**：对用户展示/朗读的文本必须是纯文本，禁止出现 `INTENT_JSON:`/`SAY:` 等协议字段。
- **意图更稳**：后端兜底必须更保守，避免把对话请求误判为音乐操作。
- **天气更完整**：在现有 `get_weather_now` 基础上，新增未来 12 小时预报 `get_weather_12h`，用于生成趋势与温度区间。
- **定位更准确**：新增“设备定位”开关（默认关闭），用户授权后前端获取 `lon,lat` 同步到后端会话态；模型在“当前位置天气”优先使用设备定位而非公网 IP。

### 关键改动（摘要）

#### 1) 后端：工具调用稳定性与输出清洗
- `backend_py/controllers/conversation_controller.py`
  - 增加 `_sanitize_say_text()`：兼容中英文冒号、空格变体（如 `SAY :`），并强制剥离协议行。
  - `handle_text_command` 在最终发消息前对 `response_text` 做强制清洗，避免协议字段泄漏。
  - 自造 tool_call（从 intent 合成/兜底生成）统一补齐 `type: "function"`，降低 OpenAI 兼容格式校验导致的偶发断链。
  - 收紧 `_is_music_request()`：移除单字“听”触发，加入“故事/解释/朗读”等强排除词。

#### 2) 后端：设备定位会话态与事件
- `backend_py/session_store.py`
  - `Session` 新增：
    - `device_location_enabled: bool`
    - `device_location: { lonLat, tsMs } | None`
- `backend_py/controllers/conversation_controller.py`
  - 新增 `update_device_location()`：更新会话态。
  - 在 LLM messages 中注入“系统定位信息”（30 分钟内有效），提示模型优先用该 `lon_lat` 查天气。
- `backend_py/main.py`
  - 新增 Socket.IO 事件：`update-device-location` / `device-location-updated`。

#### 3) 联网工具：新增未来 12 小时预报 + JWT 鉴权
- `backend_py/services/network_tools_service.py`
  - 新增工具：`get_weather_12h`（QWeather `/v7/weather/12h`）。
  - 抽取 `_resolve_qweather_location()`：统一处理城市名→Geo lookup→LocationID、或 lon,lat 直通。
  - 坐标规范化精度提升至最多 4 位小数（更贴合设备定位）。
  - `get_current_time` 的 `parameters` 改为标准空对象 schema。
  - QWeather 鉴权从 `API Key` 扩展为 **EdDSA(JWT, Ed25519)**：通过 `QWEATHER_JWT_KID/QWEATHER_JWT_PRIVATE_KEY_PATH/QWEATHER_JWT_TTL_SECONDS` 配置，运行时动态签发 JWT 并写入 `Authorization: Bearer <jwt>`。

#### 4) 提示词：天气输出要求（now + 12h）
- `backend_py/services/llm_service.py`
  - 天气策略更新：若有设备定位 `lon_lat` 则优先使用；否则走 IP。
  - 明确天气回答必须包含：当前天气 + 未来 12 小时温度区间 + 趋势（如降雨/转凉）+ 暖心建议。
  - 更新可用工具白名单包含 `get_weather_12h`。

#### 5) 前端：设备定位开关与一次性授权
- `frontend/src/components/SettingsModal.tsx`
  - 新增“允许使用设备定位”开关（首次开启弹一次性授权）。
  - 使用 `navigator.geolocation.getCurrentPosition()` 获取经纬度并持久化到 localStorage。
  - 通过 socket 同步到后端会话态（`update-device-location`）。
- `frontend/src/utils/settings.ts`
  - 设置结构新增：`deviceLocationEnabled/deviceLocationGranted/deviceLocationLonLat/deviceLocationTsMs`。
- `frontend/src/utils/socket.ts`
  - 新增 `updateDeviceLocation()` 与 `onDeviceLocationUpdated()`。
- `frontend/src/hooks/useSocket.ts`
  - 连接后自动同步设备定位设置到后端；导出 `updateDeviceLocation()` 给 UI 调用。

### 验证
- `test_scripts/test_network_tools_smoke.py` 增加 `get_weather_12h` 调用。
- 注意：QWeather 返回 `401 Unauthorized` 通常意味着 JWT 配置（`QWEATHER_JWT_KID/QWEATHER_JWT_PRIVATE_KEY_PATH`）或 `QWEATHER_API_HOST` 配置错误，需要用户本机环境变量修正。

### 后续建议
- 若用户仍出现城市级偏差：建议开启设备定位；公网 IP 定位仅作为 fallback。
- 若需要“更贴近当天最高/最低温”：可后续再讨论是否引入 QWeather `3d`，本次按需求仅新增 `12h`。
