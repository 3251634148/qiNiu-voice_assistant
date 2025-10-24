import { Settings, Shield, Volume2, Wifi, WifiOff, X } from "lucide-react";
import { useEffect, useState } from "react";
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
}

export function SettingsModal({ isOpen, onClose }: SettingsModalProps) {
  const { updateTTSSettings, getTTSSettings } = useSocket();
  const [settings, setSettings] = useState<AppSettings>({
    voiceGender: "female",
    voiceRate: 1.0,
    voicePitch: 1.0,
    allowLocalControl: true,
  });
  const [savedSettings, setSavedSettings] = useState<AppSettings>(settings);
  const [isLoading, setIsLoading] = useState(false);
  const [isOnline, _setIsOnline] = useState(false);

  useEffect(() => {
    if (isOpen) {
      // 从localStorage加载设置
      const loadedSettings = localStorage.getItem("appSettings");
      if (loadedSettings) {
        const parsed = JSON.parse(loadedSettings);
        setSettings(parsed);
        setSavedSettings(parsed);
      }

      // 从服务器获取最新设置
      getTTSSettings();
    }
  }, [isOpen, getTTSSettings]);

  const handleSave = () => {
    setIsLoading(true);

    // 保存到localStorage
    localStorage.setItem("appSettings", JSON.stringify(settings));

    // 发送到服务器
    updateTTSSettings({
      gender: settings.voiceGender,
      rate: settings.voiceRate,
      pitch: settings.voicePitch,
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
    };
    setSettings(defaultSettings);
  };

  const hasChanges = JSON.stringify(settings) !== JSON.stringify(savedSettings);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md mx-4">
        {/* Header */}
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

        {/* Settings Content */}
        <div className="p-6 space-y-6">
          {/* 语音设置 */}
          <div className="space-y-4">
            <div className="flex items-center gap-2">
              <Volume2 size={20} className="text-gray-600" />
              <h3 className="text-lg font-medium text-gray-900">语音设置</h3>
            </div>

            {/* 音色选择 */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">语音音色</label>
              <div className="flex gap-3">
                <button
                  onClick={() => setSettings((prev) => ({ ...prev, voiceGender: "female" }))}
                  className={`flex-1 py-2 px-4 rounded-lg border-2 transition-all ${
                    settings.voiceGender === "female"
                      ? "border-blue-500 bg-blue-50 text-blue-700"
                      : "border-gray-200 hover:border-gray-300 text-gray-700"
                  }`}
                >
                  女声
                </button>
                <button
                  onClick={() => setSettings((prev) => ({ ...prev, voiceGender: "male" }))}
                  className={`flex-1 py-2 px-4 rounded-lg border-2 transition-all ${
                    settings.voiceGender === "male"
                      ? "border-blue-500 bg-blue-50 text-blue-700"
                      : "border-gray-200 hover:border-gray-300 text-gray-700"
                  }`}
                >
                  男声
                </button>
              </div>
            </div>

            {/* 语速调节 */}
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

            {/* 音调调节 */}
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

          {/* 安全设置 */}
          <div className="space-y-4 pt-4 border-t border-gray-200">
            <div className="flex items-center gap-2">
              <Shield size={20} className="text-gray-600" />
              <h3 className="text-lg font-medium text-gray-900">安全设置</h3>
            </div>

            {/* 本地应用操控开关 */}
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
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between p-6 border-t border-gray-200 bg-gray-50">
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
    </div>
  );
}

// 导出设置获取函数供其他组件使用
export const getSettings = (): AppSettings => {
  const defaultSettings: AppSettings = {
    voiceGender: "female",
    voiceRate: 1.0,
    voicePitch: 1.0,
    allowLocalControl: true,
  };

  const loadedSettings = localStorage.getItem("appSettings");
  return loadedSettings ? { ...defaultSettings, ...JSON.parse(loadedSettings) } : defaultSettings;
};
