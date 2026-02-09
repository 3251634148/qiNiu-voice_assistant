export interface AppSettings {
  voiceGender: "male" | "female";
  voiceRate: number;
  voicePitch: number;
  allowLocalControl: boolean;
  voiceModel: string;
}

const DEFAULT_SETTINGS: AppSettings = {
  voiceGender: "female",
  voiceRate: 1.0,
  voicePitch: 1.0,
  allowLocalControl: true,
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
