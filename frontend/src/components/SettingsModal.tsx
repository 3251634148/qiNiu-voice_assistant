import { Settings, Shield, Volume2, Wifi, WifiOff, X } from "lucide-react";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useSocket } from "../hooks/useSocket";

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

interface AppSettings {
  voiceGender: "male" | "female";
  voiceRate: number;
  voicePitch: number;
  allowLocalControl: boolean;
  voiceModel: string;
}

type VoiceItem = { label: string; model: string; gender: "male" | "female"; desc: string };

const VOICES: VoiceItem[] = [
  { label: "知楠", model: "sambert-zhinan-v1", gender: "male", desc: "广告男声" },
  { label: "知琪", model: "sambert-zhiqi-v1", gender: "female", desc: "温柔女声" },
  { label: "知厨", model: "sambert-zhichu-v1", gender: "male", desc: "舌尖男声" },
  { label: "知德", model: "sambert-zhide-v1", gender: "male", desc: "新闻男声" },
  { label: "知佳", model: "sambert-zhijia-v1", gender: "female", desc: "标准女声" },
  { label: "知茹", model: "sambert-zhiru-v1", gender: "female", desc: "新闻播报" },
  { label: "知倩", model: "sambert-zhiqian-v1", gender: "female", desc: "配音解说、新闻播报" },
  { label: "知祥", model: "sambert-zhixiang-v1", gender: "male", desc: "磁性男声" },
  { label: "知薇", model: "sambert-zhiwei-v1", gender: "female", desc: "萝莉女声" },
  { label: "知浩", model: "sambert-zhihao-v1", gender: "male", desc: "咨询男声" },
  { label: "知婧", model: "sambert-zhijing-v1", gender: "female", desc: "严厉女声" },
  { label: "知茗", model: "sambert-zhiming-v1", gender: "male", desc: "诙谐男声" },
  { label: "知墨", model: "sambert-zhimo-v1", gender: "male", desc: "情感男声" },
  { label: "知娜", model: "sambert-zhina-v1", gender: "female", desc: "浙普女声" },
  { label: "知树", model: "sambert-zhishu-v1", gender: "male", desc: "资讯男声" },
  { label: "知莎", model: "sambert-zhistella-v1", gender: "female", desc: "知性女声" },
  { label: "知婷", model: "sambert-zhiting-v1", gender: "female", desc: "电台女声" },
  { label: "知笑", model: "sambert-zhixiao-v1", gender: "female", desc: "资讯女声" },
  { label: "知雅", model: "sambert-zhiya-v1", gender: "female", desc: "严厉女声" },
  { label: "知晔", model: "sambert-zhiye-v1", gender: "male", desc: "青年男声" },
  { label: "知颖", model: "sambert-zhiying-v1", gender: "female", desc: "软萌童声" },
  { label: "知媛", model: "sambert-zhiyuan-v1", gender: "female", desc: "知心姐姐" },
  { label: "知悦", model: "sambert-zhiyue-v1", gender: "female", desc: "客服温柔女声" },
  { label: "知柜", model: "sambert-zhigui-v1", gender: "female", desc: "直播女声" },
  { label: "知硕", model: "sambert-zhishuo-v1", gender: "male", desc: "自然男声" },
  { label: "知妙", model: "sambert-zhimiao-emo-v1", gender: "female", desc: "多情感女声" },
  { label: "知猫", model: "sambert-zhimao-v1", gender: "female", desc: "直播女声" },
  { label: "知伦", model: "sambert-zhilun-v1", gender: "male", desc: "悬疑解说" },
  { label: "知飞", model: "sambert-zhifei-v1", gender: "male", desc: "激昂解说" },
  { label: "知达", model: "sambert-zhida-v1", gender: "male", desc: "标准男声" },
  { label: "Camila", model: "sambert-camila-v1", gender: "female", desc: "西班牙语女声" },
  { label: "Perla", model: "sambert-perla-v1", gender: "female", desc: "意大利语女声" },
  { label: "Indah", model: "sambert-indah-v1", gender: "female", desc: "印尼语女声" },
  { label: "Clara", model: "sambert-clara-v1", gender: "female", desc: "法语女声" },
  { label: "Hanna", model: "sambert-hanna-v1", gender: "female", desc: "德语女声" },
  { label: "Beth", model: "sambert-beth-v1", gender: "female", desc: "美式英文女声" },
  { label: "Betty", model: "sambert-betty-v1", gender: "female", desc: "客服女声" },
  { label: "Cally", model: "sambert-cally-v1", gender: "female", desc: "自然女声" },
  { label: "Cindy", model: "sambert-cindy-v1", gender: "female", desc: "对话女声" },
  { label: "Eva", model: "sambert-eva-v1", gender: "female", desc: "陪伴女声" },
  { label: "Donna", model: "sambert-donna-v1", gender: "female", desc: "教育女声" },
  { label: "Brian", model: "sambert-brian-v1", gender: "male", desc: "美式英文男声" },
  { label: "Waan", model: "sambert-waan-v1", gender: "female", desc: "泰语女声" },
];

export function SettingsModal({ isOpen, onClose }: SettingsModalProps) {
  const { updateTTSSettings, getTTSSettings } = useSocket();
  const [settings, setSettings] = useState<AppSettings>({
    voiceGender: "female",
    voiceRate: 1.0,
    voicePitch: 1.0,
    allowLocalControl: true,
    voiceModel: "sambert-zhishuo-v1",
  });
  const [savedSettings, setSavedSettings] = useState<AppSettings>(settings);
  const [isLoading, setIsLoading] = useState(false);
  const [isOnline, _setIsOnline] = useState(false);

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
          voiceModel: parsed.voiceModel ?? "sambert-zhishuo-v1",
        } as AppSettings;
        setSettings(merged);
        setSavedSettings(merged);
      }
      getTTSSettings();
    }
  }, [isOpen, getTTSSettings]);

  const handleSave = () => {
    setIsLoading(true);
    localStorage.setItem("appSettings", JSON.stringify(settings));
    updateTTSSettings({
      gender: settings.voiceGender,
      rate: settings.voiceRate,
      pitch: settings.voicePitch,
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
      voiceModel: "sambert-zhishuo-v1",
    };
    setSettings(defaultSettings);
  };

  const hasChanges = JSON.stringify(settings) !== JSON.stringify(savedSettings);

  if (!isOpen) return null;

  const modal = (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-[9999]">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md mx-4 max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between p-6 border-b border-gray-200">
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

        <div className="p-6 space-y-6">
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
                  <div className="border rounded-lg h-40 overflow-y-auto p-2">
                    <div className="space-y-2">
                      {VOICES.filter(v=>v.gender==="male").map((v) => (
                        <button
                          key={v.model}
                          onClick={() => setSettings((prev) => ({ ...prev, voiceModel: v.model, voiceGender: "male" }))}
                          className={`w-full text-left px-3 py-2 rounded-md border ${
                            settings.voiceModel === v.model
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
                  <div className="border rounded-lg h-40 overflow-y-auto p-2">
                    <div className="space-y-2">
                      {VOICES.filter(v=>v.gender==="female").map((v) => (
                        <button
                          key={v.model}
                          onClick={() => setSettings((prev) => ({ ...prev, voiceModel: v.model, voiceGender: "female" }))}
                          className={`w-full text-left px-3 py-2 rounded-md border ${
                            settings.voiceModel === v.model
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
              <label className="block text-sm font-medium text-gray-700 mb-2">语速: {settings.voiceRate.toFixed(1)}</label>
              <input
                type="range"
                min="0.5"
                max="2.0"
                step="0.1"
                value={settings.voiceRate}
                onChange={(e) => setSettings((prev) => ({ ...prev, voiceRate: parseFloat(e.target.value) }))}
                className="w-full h-2 bg-gray-200 rounded-lg appearance-none cursor-pointer"
              />
              <div className="flex justify-between text-xs text-gray-500 mt-1">
                <span>慢</span>
                <span>正常</span>
                <span>快</span>
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">音调: {settings.voicePitch.toFixed(1)}</label>
              <input
                type="range"
                min="0.5"
                max="2.0"
                step="0.1"
                value={settings.voicePitch}
                onChange={(e) => setSettings((prev) => ({ ...prev, voicePitch: parseFloat(e.target.value) }))}
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
                onClick={() => setSettings((prev) => ({ ...prev, allowLocalControl: !prev.allowLocalControl }))}
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
          </div>
        </div>

        <div className="flex items-center justify-between p-6 border-t border-gray-200 bg-gray-50">
          <button onClick={handleReset} className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800 transition-colors">
            重置默认
          </button>
          <div className="flex gap-3">
            <button onClick={onClose} className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800 transition-colors">
              取消
            </button>
            <button
              onClick={handleSave}
              disabled={!hasChanges || isLoading}
              className={`px-4 py-2 text-sm rounded-lg transition-colors ${
                hasChanges && !isLoading ? "bg-blue-600 text-white hover:bg-blue-700" : "bg-gray-300 text-gray-500 cursor-not-allowed"
              }`}
            >
              {isLoading ? "保存中..." : "保存设置"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );

  return createPortal(modal, document.body);
}

export const getSettings = (): AppSettings => {
  const defaultSettings: AppSettings = {
    voiceGender: "female",
    voiceRate: 1.0,
    voicePitch: 1.0,
    allowLocalControl: true,
    voiceModel: "sambert-zhishuo-v1",
  };
  const loadedSettings = localStorage.getItem("appSettings");
  return loadedSettings ? { ...defaultSettings, ...JSON.parse(loadedSettings) } : defaultSettings;
};
