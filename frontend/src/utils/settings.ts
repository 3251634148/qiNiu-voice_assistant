export interface AppSettings {
  voiceGender: "male" | "female";
  voiceRate: number;
  voicePitch: number;
  allowLocalControl: boolean;
  networkAccessEnabled: boolean;
  /**
   * 是否已完成“首次联网一次性授权”。
   * - false：用户第一次尝试开启联网时需要弹窗确认
   * - true：后续切换联网开关不再弹窗
   */
  networkAccessGranted: boolean;
  /**
   * 是否允许使用设备定位（更准确）。
   * 注意：该开关仅表示“本地允许”，实际仍会触发系统定位授权弹窗。
   */
  deviceLocationEnabled: boolean;
  /**
   * 是否已完成“首次设备定位一次性授权”（应用内确认）。
   */
  deviceLocationGranted: boolean;
  /**
   * 最近一次设备定位坐标（lon,lat），用于查询当前位置天气。
   */
  deviceLocationLonLat: string;
  /**
   * 最近一次设备定位时间戳（ms）。
   */
  deviceLocationTsMs: number;
  voiceModel: string;
}

const DEFAULT_SETTINGS: AppSettings = {
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

export function getSettings(): AppSettings {
  const loadedSettings = localStorage.getItem("appSettings");
  if (!loadedSettings) {
    return DEFAULT_SETTINGS;
  }

  try {
    const parsed = JSON.parse(loadedSettings);
    return { ...DEFAULT_SETTINGS, ...parsed } as AppSettings;
  } catch {
    return DEFAULT_SETTINGS;
  }
}
