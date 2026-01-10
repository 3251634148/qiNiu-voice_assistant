const { app, BrowserWindow, globalShortcut } = require("electron");
const path = require("node:path");
const isDev = process.env.NODE_ENV === "development";

let mainWindow;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 800,
    minHeight: 600,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      enableRemoteModule: false,
      preload: path.join(__dirname, "preload.js"),
    },
    icon: path.join(__dirname, "assets/icon.png"),
    titleBarStyle: "default",
    show: false,
  });

  // 加载应用
  if (isDev) {
    mainWindow.loadURL("http://localhost:5173");
    mainWindow.webContents.openDevTools();
  } else {
    mainWindow.loadFile(path.join(__dirname, "../dist/index.html"));
  }

  // 窗口准备好后显示
  mainWindow.once("ready-to-show", () => {
    mainWindow.show();

    if (isDev) {
      mainWindow.webContents.openDevTools();
    }
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

/**
 * 注册全局快捷键
 * F12: 唤醒/聚焦应用窗口
 */
function registerGlobalShortcuts() {
  // 注册F12快捷键
  const ret = globalShortcut.register("F12", () => {
    console.log("F12快捷键被触发");

    if (mainWindow) {
      if (mainWindow.isMinimized()) {
        // 如果窗口最小化，恢复窗口
        mainWindow.restore();
      }

      if (!mainWindow.isVisible()) {
        // 如果窗口不可见，显示窗口
        mainWindow.show();
      }

      // 聚焦窗口
      mainWindow.focus();

      // 可选：触发语音输入
      // mainWindow.webContents.send("trigger-voice-input");
    }
  });

  if (!ret) {
    console.log("F12快捷键注册失败");
  } else {
    console.log("F12快捷键注册成功");
  }

  // 可选：注册其他快捷键
  // Ctrl+Shift+V 或 Command+Shift+V: 触发语音输入
  const voiceShortcut = globalShortcut.register("CommandOrControl+Shift+V", () => {
    console.log("语音快捷键被触发");

    if (mainWindow && mainWindow.isVisible()) {
      mainWindow.webContents.send("trigger-voice-input");
    }
  });

  if (voiceShortcut) {
    console.log("语音快捷键注册成功 (Ctrl/Cmd+Shift+V)");
  }
}

/**
 * 注销所有全局快捷键
 */
function unregisterGlobalShortcuts() {
  globalShortcut.unregisterAll();
  console.log("已注销所有全局快捷键");
}

// 应用准备就绪时创建窗口并注册快捷键
app.whenReady().then(() => {
  createWindow();
  registerGlobalShortcuts();
});

// 所有窗口关闭时退出应用 (macOS 除外)
app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

// macOS 上点击 dock 图标时重新创建窗口
app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});

// 应用退出前注销快捷键
app.on("will-quit", () => {
  unregisterGlobalShortcuts();
});

// 安全设置
app.on("web-contents-created", (_event, contents) => {
  contents.on("new-window", (navigationEvent, navigationURL) => {
    navigationEvent.preventDefault();
    require("electron").shell.openExternal(navigationURL);
  });
});
