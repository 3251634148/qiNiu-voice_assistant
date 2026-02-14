import { Settings, Shield, Volume2, Wifi, WifiOff, X } from "lucide-react";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

import { useSocketContext } from "../hooks/SocketProvider";
import type { ConfirmationRequest } from "../types";
import type { AppSettings } from "../utils/settings";

import ConfirmationDialog from "./ConfirmationDialog";

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

type VoiceItem = {
  label: string;
  voice: string;
  gender: "male" | "female";
  desc: string;
};

// qwen3-omni-flash-2025-12-01 支持的音色（前端保存 voice 到 localStorage，并透传给后端）
const VOICES: VoiceItem[] = [
  { label: "芊悦", voice: "Cherry", gender: "female", desc: "阳光积极、亲切自然小姐姐" },
  { label: "苏瑶", voice: "Serena", gender: "female", desc: "温柔小姐姐" },
  { label: "晨煦", voice: "Ethan", gender: "male", desc: "标准普通话，阳光温暖" },
  { label: "千雪", voice: "Chelsie", gender: "female", desc: "二次元虚拟女友" },
  { label: "茉兔", voice: "Momo", gender: "female", desc: "撒娇搞怪，逗你开心" },
  { label: "十三", voice: "Vivian", gender: "female", desc: "拽拽的、可爱的小暴躁" },
  { label: "月白", voice: "Moon", gender: "male", desc: "率性帅气" },
  { label: "四月", voice: "Maia", gender: "female", desc: "知性与温柔" },
  { label: "凯", voice: "Kai", gender: "male", desc: "耳朵的一场SPA" },
  { label: "不吃鱼", voice: "Nofish", gender: "male", desc: "不会翘舌音的设计师" },
  { label: "萌宝", voice: "Bella", gender: "female", desc: "喝酒不打醉拳的小萝莉" },
  { label: "詹妮弗", voice: "Jennifer", gender: "female", desc: "电影质感般美语女声" },
  { label: "甜茶", voice: "Ryan", gender: "male", desc: "节奏拉满，戏感炸裂" },
  { label: "卡捷琳娜", voice: "Katerina", gender: "female", desc: "御姐音色" },
  { label: "艾登", voice: "Aiden", gender: "male", desc: "美语大男孩" },
  { label: "沧明子", voice: "Eldric Sage", gender: "male", desc: "沉稳睿智的老者" },
  { label: "乖小妹", voice: "Mia", gender: "female", desc: "温顺乖巧" },
  { label: "沙小弥", voice: "Mochi", gender: "female", desc: "聪明伶俐" },
  { label: "燕铮莺", voice: "Bellona", gender: "female", desc: "声音洪亮，吐字清晰" },
  { label: "田叔", voice: "Vincent", gender: "male", desc: "沙哑烟嗓" },
  { label: "萌小姬", voice: "Bunny", gender: "female", desc: "萌属性小萝莉" },
  { label: "阿闻", voice: "Neil", gender: "male", desc: "新闻主持人" },
  { label: "墨讲师", voice: "Elias", gender: "male", desc: "严谨又会讲故事" },
  { label: "徐大爷", voice: "Arthur", gender: "male", desc: "质朴嗓音" },
  { label: "邻家妹妹", voice: "Nini", gender: "female", desc: "又软又黏" },
  { label: "诡婆婆", voice: "Ebona", gender: "female", desc: "低语恐怖氛围" },
  { label: "小婉", voice: "Seren", gender: "female", desc: "助眠声线" },
  { label: "顽屁小孩", voice: "Pip", gender: "male", desc: "调皮童真" },
  { label: "少女阿月", voice: "Stella", gender: "female", desc: "迷糊少女音" },
  { label: "博德加", voice: "Bodega", gender: "male", desc: "西班牙大叔" },
  { label: "索尼莎", voice: "Sonrisa", gender: "female", desc: "拉美大姐" },
  { label: "阿列克", voice: "Alek", gender: "male", desc: "战斗民族" },
  { label: "多尔切", voice: "Dolce", gender: "male", desc: "意大利大叔" },
  { label: "素熙", voice: "Sohee", gender: "female", desc: "韩国欧尼" },
  { label: "小野杏", voice: "Ono Anna", gender: "female", desc: "青梅竹马" },
  { label: "莱恩", voice: "Lenn", gender: "male", desc: "德国青年" },
  { label: "埃米尔安", voice: "Emilien", gender: "male", desc: "法国大哥哥" },
  { label: "安德雷", voice: "Andre", gender: "male", desc: "沉稳男声" },
  { label: "拉迪奥·戈尔", voice: "Radio Gol", gender: "male", desc: "足球诗人" },
  { label: "上海-阿珍", voice: "Jada", gender: "female", desc: "沪上阿姐" },
  { label: "北京-晓东", voice: "Dylan", gender: "male", desc: "北京话少年" },
  { label: "南京-老李", voice: "Li", gender: "male", desc: "耐心老师" },
  { label: "陕西-秦川", voice: "Marcus", gender: "male", desc: "老陕味道" },
  { label: "闽南-阿杰", voice: "Roy", gender: "male", desc: "市井活泼" },
  { label: "天津-李彼得", voice: "Peter", gender: "male", desc: "相声捧哏" },
  { label: "四川-晴儿", voice: "Sunny", gender: "female", desc: "甜甜川妹子" },
  { label: "四川-程川", voice: "Eric", gender: "male", desc: "成都男子" },
  { label: "粤语-阿强", voice: "Rocky", gender: "male", desc: "幽默陪聊" },
  { label: "粤语-阿清", voice: "Kiki", gender: "female", desc: "港妹闺蜜" },
];

export function SettingsModal({ isOpen, onClose }: SettingsModalProps) {
  const { updateTTSSettings, updateNetworkSettings, updateDeviceLocation, getTTSSettings } = useSocketContext();
  const [settings, setSettings] = useState<AppSettings>({
    voiceGender: "female",
    voiceRate: 1.0,
    voicePitch: 1.0,
    allowLocalControl: true,
    networkAccessEnabled: false,
    networkAccessGranted: false,
    deviceLocationEnabled: false,
    deviceLocationGranted: false,
    deviceLocationLonLat: "",
    deviceLocationTsMs: 0,
    voiceModel: "Cherry",
  });
  const [savedSettings, setSavedSettings] = useState<AppSettings>(settings);
  const [isLoading, setIsLoading] = useState(false);
  const isOnline = settings.networkAccessEnabled;

  useEffect(() => {
    if (isOpen) {
      const loadedSettings = localStorage.getItem("appSettings");
      if (loadedSettings) {
        const parsed = JSON.parse(loadedSettings);
        const merged = {
          voiceGender: parsed.voiceGender ?? "female",
          voiceRate: parsed.voiceRate ?? 1.0,
          voicePitch: parsed.voicePitch ?? 1.0,
          allowLocalControl: parsed.allowLocalControl ?? true,
          networkAccessEnabled: parsed.networkAccessEnabled ?? false,
          networkAccessGranted: parsed.networkAccessGranted ?? false,
          deviceLocationEnabled: parsed.deviceLocationEnabled ?? false,
          deviceLocationGranted: parsed.deviceLocationGranted ?? false,
          deviceLocationLonLat: parsed.deviceLocationLonLat ?? "",
          deviceLocationTsMs: parsed.deviceLocationTsMs ?? 0,
          voiceModel: parsed.voiceModel ?? "Cherry",
        } as AppSettings;
        setSettings(merged);
        setSavedSettings(merged);
      }
      getTTSSettings();
    }
  }, [isOpen, getTTSSettings]);

  const [networkConfirm, setNetworkConfirm] = useState<ConfirmationRequest | null>(null);
  const [deviceLocationConfirm, setDeviceLocationConfirm] = useState<ConfirmationRequest | null>(null);

  const persistSettings = (next: AppSettings) => {
    localStorage.setItem("appSettings", JSON.stringify(next));
  };

  const applyNetworkAccessEnabled = (enabled: boolean, granted: boolean) => {
    const next = {
      ...settings,
      networkAccessEnabled: enabled,
      networkAccessGranted: granted,
    } as AppSettings;
    setSettings(next);

    // 安全开关：即时生效并落盘
    persistSettings(next);

    // 同步到后端会话态
    updateNetworkSettings(enabled);
  };

  const applyDeviceLocation = (
    enabled: boolean,
    granted: boolean,
    lonLat: string = "",
    tsMs: number = 0
  ) => {
    const next = {
      ...settings,
      deviceLocationEnabled: enabled,
      deviceLocationGranted: granted,
      deviceLocationLonLat: lonLat,
      deviceLocationTsMs: tsMs,
    } as AppSettings;
    setSettings(next);

    persistSettings(next);

    updateDeviceLocation({
      deviceLocationEnabled: enabled,
      lonLat,
      tsMs,
    });
  };

  const requestDeviceLocation = () => {
    if (!("geolocation" in navigator)) {
      console.log("当前环境不支持 geolocation");
      applyDeviceLocation(false, settings.deviceLocationGranted, "", 0);
      return;
    }

    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const lon = pos.coords.longitude;
        const lat = pos.coords.latitude;
        const tsMs = Date.now();
        const lonLat = `${lon},${lat}`;
        applyDeviceLocation(true, true, lonLat, tsMs);
      },
      (err) => {
        console.log("获取设备定位失败:", err?.message);
        applyDeviceLocation(false, settings.deviceLocationGranted, "", 0);
      },
      {
        enableHighAccuracy: true,
        timeout: 8000,
        maximumAge: 60 * 1000,
      }
    );
  };

  const handleToggleDeviceLocation = () => {
    if (settings.deviceLocationEnabled) {
      applyDeviceLocation(false, settings.deviceLocationGranted, "", 0);
      return;
    }

    // 开启设备定位：首次需要一次性授权（应用内确认）
    if (!settings.deviceLocationGranted) {
      const now = new Date();
      setDeviceLocationConfirm({
        id: `confirm_device_location_${now.getTime()}`,
        riskLevel: "medium",
        reason: "开启设备定位后，助手会向系统请求你的位置信息，用于更准确地查询当前位置天气。",
        summary: "允许助手使用设备定位（更准确的天气定位）",
        suggestions: ["仅用于天气定位，不会读取本地文件", "你可以随时在设置中关闭该开关"],
        timestamp: now.toISOString(),
        expiresAt: new Date(now.getTime() + 5 * 60 * 1000).toISOString(),
        toolCall: {
          id: "device_location",
          name: "device_location",
          arguments: { deviceLocationEnabled: true },
        },
      });
      return;
    }

    requestDeviceLocation();
  };

  const handleToggleNetworkAccess = () => {
    if (settings.networkAccessEnabled) {
      // 关闭联网：不需要二次确认
      applyNetworkAccessEnabled(false, settings.networkAccessGranted);
      return;
    }

    // 开启联网：首次需要一次性授权
    if (!settings.networkAccessGranted) {
      const now = new Date();
      setNetworkConfirm({
        id: `confirm_network_${now.getTime()}`,
        riskLevel: "medium",
        reason: "开启联网后，助手可能向第三方服务发起请求（搜索/新闻/天气），以获取实时信息。",
        summary: "允许助手联网查询实时信息",
        suggestions: [
          "仅发送必要的查询参数（例如：关键词、经纬度），不会发送本地文件内容",
          "你可以随时在设置中关闭联网",
        ],
        timestamp: now.toISOString(),
        expiresAt: new Date(now.getTime() + 5 * 60 * 1000).toISOString(),
        toolCall: {
          id: "network_access",
          name: "network_access",
          arguments: { networkAccessEnabled: true },
        },
      });
      return;
    }

    applyNetworkAccessEnabled(true, true);
  };

  const handleSave = () => {
    setIsLoading(true);
    persistSettings(settings);

    // 即使用户只点了“保存设置”，也同步一次联网开关（避免刷新后后端状态不一致）
    updateNetworkSettings(!!settings.networkAccessEnabled);

    // 同步设备定位（若已启用且已有坐标）
    updateDeviceLocation({
      deviceLocationEnabled: !!settings.deviceLocationEnabled,
      lonLat: settings.deviceLocationLonLat,
      tsMs: settings.deviceLocationTsMs,
    });

    updateTTSSettings({
      gender: settings.voiceGender,
      rate: settings.voiceRate,
      pitch: settings.voicePitch,
      voice: settings.voiceModel,
      // 兼容后端旧字段：仍然透传一份 model
      model: settings.voiceModel,
      allowLocalControl: settings.allowLocalControl,
    });
    setSavedSettings(settings);
    setIsLoading(false);
    onClose();
  };

  const handleReset = () => {
    const defaultSettings: AppSettings = {
      voiceGender: "female",
      voiceRate: 1.0,
      voicePitch: 1.0,
      allowLocalControl: true,
      networkAccessEnabled: false,
      networkAccessGranted: false,
      deviceLocationEnabled: false,
      deviceLocationGranted: false,
      deviceLocationLonLat: "",
      deviceLocationTsMs: 0,
      voiceModel: "Cherry",
    };
    setSettings(defaultSettings);
  };

  const hasChanges = JSON.stringify(settings) !== JSON.stringify(savedSettings);

  if (!isOpen) return null;

  const modal = (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-[9999]">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md mx-4 max-h-[85vh] flex flex-col">
        <div className="flex items-center justify-between p-6 border-b border-gray-200 flex-shrink-0">
          <div className="flex items-center gap-2">
            <Settings size={24} className="text-gray-700" />
            <h2 className="text-xl font-semibold text-gray-900">应用设置</h2>
            <div className="flex items-center gap-1 ml-4">
              {isOnline ? (
                <div className="flex items-center gap-1 text-green-600">
                  <Wifi size={16} />
                  <span className="text-xs">已连接</span>
                </div>
              ) : (
                <div className="flex items-center gap-1 text-red-600">
                  <WifiOff size={16} />
                  <span className="text-xs">离线</span>
                </div>
              )}
            </div>
          </div>
          <button onClick={onClose} className="p-2 hover:bg-gray-100 rounded-lg transition-colors">
            <X size={20} className="text-gray-500" />
          </button>
        </div>

        <div className="p-6 space-y-6 overflow-y-auto flex-1">
          <div className="space-y-4">
            <div className="flex items-center gap-2">
              <Volume2 size={20} className="text-gray-600" />
              <h3 className="text-lg font-medium text-gray-900">语音设置</h3>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">语音音色</label>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <div className="text-sm text-gray-600 mb-2">男声</div>
                  <div className="border rounded-lg h-32 overflow-y-auto p-2">
                    <div className="space-y-2">
                      {VOICES.filter((v) => v.gender === "male").map((v) => (
                        <button
                          key={v.voice}
                          onClick={() =>
                            setSettings((prev) => ({
                              ...prev,
                              voiceModel: v.voice,
                              voiceGender: "male",
                            }))
                          }
                          className={`w-full text-left px-3 py-2 rounded-md border ${
                            settings.voiceModel === v.voice
                              ? "border-blue-500 bg-blue-50 text-blue-700"
                              : "border-gray-200 hover:border-gray-300"
                          }`}
                        >
                          {v.label} - {v.desc}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
                <div>
                  <div className="text-sm text-gray-600 mb-2">女声</div>
                  <div className="border rounded-lg h-32 overflow-y-auto p-2">
                    <div className="space-y-2">
                      {VOICES.filter((v) => v.gender === "female").map((v) => (
                        <button
                          key={v.voice}
                          onClick={() =>
                            setSettings((prev) => ({
                              ...prev,
                              voiceModel: v.voice,
                              voiceGender: "female",
                            }))
                          }
                          className={`w-full text-left px-3 py-2 rounded-md border ${
                            settings.voiceModel === v.voice
                              ? "border-blue-500 bg-blue-50 text-blue-700"
                              : "border-gray-200 hover:border-gray-300"
                          }`}
                        >
                          {v.label} - {v.desc}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
              <div className="text-xs text-gray-500 mt-1">当前：{settings.voiceModel}</div>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                语速: {settings.voiceRate.toFixed(1)}
              </label>
              <input
                type="range"
                min="0.5"
                max="2.0"
                step="0.1"
                value={settings.voiceRate}
                onChange={(e) =>
                  setSettings((prev) => ({ ...prev, voiceRate: parseFloat(e.target.value) }))
                }
                className="w-full h-2 bg-gray-200 rounded-lg appearance-none cursor-pointer"
              />
              <div className="flex justify-between text-xs text-gray-500 mt-1">
                <span>慢</span>
                <span>正常</span>
                <span>快</span>
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                音调: {settings.voicePitch.toFixed(1)}
              </label>
              <input
                type="range"
                min="0.5"
                max="2.0"
                step="0.1"
                value={settings.voicePitch}
                onChange={(e) =>
                  setSettings((prev) => ({ ...prev, voicePitch: parseFloat(e.target.value) }))
                }
                className="w-full h-2 bg-gray-200 rounded-lg appearance-none cursor-pointer"
              />
              <div className="flex justify-between text-xs text-gray-500 mt-1">
                <span>低</span>
                <span>正常</span>
                <span>高</span>
              </div>
            </div>
          </div>

          <div className="space-y-4 pt-4 border-t border-gray-200">
            <div className="flex items-center gap-2">
              <Shield size={20} className="text-gray-600" />
              <h3 className="text-lg font-medium text-gray-900">安全设置</h3>
            </div>

            <div className="flex items-center justify-between">
              <div>
                <label className="text-sm font-medium text-gray-700">允许本地应用操控</label>
                <p className="text-xs text-gray-500 mt-1">关闭后将阻止所有本地应用操作请求</p>
              </div>
              <button
                onClick={() =>
                  setSettings((prev) => ({ ...prev, allowLocalControl: !prev.allowLocalControl }))
                }
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                  settings.allowLocalControl ? "bg-blue-600" : "bg-gray-200"
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                    settings.allowLocalControl ? "translate-x-6" : "translate-x-1"
                  }`}
                />
              </button>
            </div>

            <div className="flex items-center justify-between">
              <div>
                <label className="text-sm font-medium text-gray-700">允许联网查询（搜索/新闻/天气）</label>
                <p className="text-xs text-gray-500 mt-1">
                  开启后助手可能向第三方服务发起请求以获取实时信息（可随时关闭）
                </p>
              </div>
              <button
                onClick={handleToggleNetworkAccess}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                  settings.networkAccessEnabled ? "bg-blue-600" : "bg-gray-200"
                }`}
                aria-label="network-access-toggle"
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                    settings.networkAccessEnabled ? "translate-x-6" : "translate-x-1"
                  }`}
                />
              </button>
            </div>

            <div className="flex items-center justify-between">
              <div>
                <label className="text-sm font-medium text-gray-700">允许使用设备定位（更准确的天气定位）</label>
                <p className="text-xs text-gray-500 mt-1">
                  开启后会请求系统定位权限，用于提升“当前位置天气”准确度（可随时关闭）
                </p>
                {settings.deviceLocationEnabled && settings.deviceLocationLonLat ? (
                  <p className="text-xs text-gray-500 mt-1">最近定位：{settings.deviceLocationLonLat}</p>
                ) : null}
              </div>
              <button
                onClick={handleToggleDeviceLocation}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                  settings.deviceLocationEnabled ? "bg-blue-600" : "bg-gray-200"
                }`}
                aria-label="device-location-toggle"
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                    settings.deviceLocationEnabled ? "translate-x-6" : "translate-x-1"
                  }`}
                />
              </button>
            </div>
          </div>
        </div>

        <div className="flex items-center justify-between p-6 border-t border-gray-200 bg-gray-50 flex-shrink-0">
          <button
            onClick={handleReset}
            className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800 transition-colors"
          >
            重置默认
          </button>
          <div className="flex gap-3">
            <button
              onClick={onClose}
              className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800 transition-colors"
            >
              取消
            </button>
            <button
              onClick={handleSave}
              disabled={!hasChanges || isLoading}
              className={`px-4 py-2 text-sm rounded-lg transition-colors ${
                hasChanges && !isLoading
                  ? "bg-blue-600 text-white hover:bg-blue-700"
                  : "bg-gray-300 text-gray-500 cursor-not-allowed"
              }`}
            >
              {isLoading ? "保存中..." : "保存设置"}
            </button>
          </div>
        </div>
      </div>

      {networkConfirm ? (
        <ConfirmationDialog
          confirmation={networkConfirm}
          onConfirm={(approved) => {
            if (approved) {
              applyNetworkAccessEnabled(true, true);
            }
            setNetworkConfirm(null);
          }}
          onCancel={() => setNetworkConfirm(null)}
        />
      ) : null}

      {deviceLocationConfirm ? (
        <ConfirmationDialog
          confirmation={deviceLocationConfirm}
          onConfirm={(approved) => {
            if (approved) {
              requestDeviceLocation();
            }
            setDeviceLocationConfirm(null);
          }}
          onCancel={() => setDeviceLocationConfirm(null)}
        />
      ) : null}
    </div>
  );

  return createPortal(modal, document.body);
}
