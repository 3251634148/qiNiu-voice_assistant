const { exec, execFile } = require("node:child_process");
const fs = require("node:fs").promises;
const path = require("node:path");
const os = require("node:os");
const logger = require("../utils/logger");

class SystemController {
  constructor() {
    this.platform = os.platform();
    this.homeDir = os.homedir();

    // macOS 应用索引缓存（避免每次 open_app 都全盘扫描）
    this.macAppIndex = null;
    this.macAppIndexBuiltAt = 0;
  }

  async executeAction(action) {
    try {
      logger.info(`执行操作:`, action);

      switch (action.type) {
        case "open_app":
          return await this.openApplication(action.target);

        case "close_app":
          return await this.closeApplication(action.target);

        case "create_file":
          return await this.createFile(action.path, action.content);

        case "create_folder":
          return await this.createFolder(action.path);

        case "delete_file":
          return await this.deleteFile(action.path);

        case "delete_folder":
          return await this.deleteFolder(action.path);

        case "move_file":
          return await this.moveFile(action.source, action.destination);

        case "copy_file":
          return await this.copyFile(action.source, action.destination);

        case "read_file":
          return await this.readFile(action.path);

        case "list_directory":
          return await this.listDirectory(action.path);

        case "system_command":
          return await this.executeSystemCommand(action.command);

        case "volume_control":
          return await this.controlVolume(action.level);

        case "open_url":
          return await this.openUrl(action.url);

        default:
          throw new Error(`不支持的操作类型: ${action.type}`);
      }
    } catch (error) {
      logger.error(`执行操作失败:`, error);
      return { success: false, error: error.message };
    }
  }

  execFileAsync(command, args) {
    return new Promise((resolve, reject) => {
      execFile(command, args, (error, stdout, stderr) => {
        if (error) {
          reject(error);
          return;
        }
        resolve({ stdout, stderr });
      });
    });
  }

  normalizeMacAppQuery(text) {
    return String(text || "")
      .toLowerCase()
      .replace(/\.app$/i, "")
      .replace(/[\s._-]/g, "")
      .trim();
  }

  getMacAppAliasCandidates(appName) {
    const raw = String(appName || "").trim();
    const normalized = this.normalizeMacAppQuery(raw);

    const aliasMap = {
      // 常见中英文别名（不是白名单，仅用于提升匹配成功率）
      "微信": ["WeChat"],
      "wechat": ["WeChat", "微信"],
      "企业微信": ["WeCom", "WeChat Work"],
      "wecom": ["WeCom", "企业微信"],
      "qq音乐": ["QQMusic"],
      "qqmusic": ["QQMusic", "QQ音乐"],
    };

    const candidates = [raw];

    const aliasList = aliasMap[raw] || aliasMap[normalized] || [];
    for (const alias of aliasList) {
      candidates.push(alias);
    }

    // 去重
    return [...new Set(candidates.filter(Boolean))];
  }

  getMacAppSearchDirs() {
    return [
      "/Applications",
      "/Applications/Utilities",
      "/System/Applications",
      "/System/Applications/Utilities",
      path.join(this.homeDir, "Applications"),
    ];
  }

  async buildMacAppIndex() {
    const dirs = this.getMacAppSearchDirs();
    const apps = [];

    for (const dirPath of dirs) {
      try {
        const items = await fs.readdir(dirPath, { withFileTypes: true });
        for (const item of items) {
          if (!item.isDirectory()) {
            continue;
          }
          if (!item.name.toLowerCase().endsWith(".app")) {
            continue;
          }

          const displayName = item.name.replace(/\.app$/i, "");
          const fullPath = path.join(dirPath, item.name);
          apps.push({
            displayName,
            fullPath,
            normalized: this.normalizeMacAppQuery(displayName),
          });
        }
      } catch (_error) {
        // 忽略不存在或无权限目录
      }
    }

    return apps;
  }

  async getMacAppIndex() {
    const cacheTtlMs = 60 * 1000;
    if (this.macAppIndex && Date.now() - this.macAppIndexBuiltAt < cacheTtlMs) {
      return this.macAppIndex;
    }

    const index = await this.buildMacAppIndex();
    this.macAppIndex = index;
    this.macAppIndexBuiltAt = Date.now();
    return index;
  }

  async findInstalledMacApp(appName) {
    const candidates = this.getMacAppAliasCandidates(appName);
    const index = await this.getMacAppIndex();

    for (const candidate of candidates) {
      const normalized = this.normalizeMacAppQuery(candidate);

      const exact = index.find((app) => app.normalized === normalized);
      if (exact) {
        return exact;
      }

      const fuzzy = index.find((app) => app.normalized.includes(normalized) || normalized.includes(app.normalized));
      if (fuzzy) {
        return fuzzy;
      }
    }

    return null;
  }

  async openApplication(appName) {
    const name = String(appName || "").trim();
    if (!name) {
      throw new Error("应用程序名称不能为空");
    }

    if (this.platform === "darwin") {
      const match = await this.findInstalledMacApp(name);

      try {
        if (match?.fullPath) {
          await this.execFileAsync("open", [match.fullPath]);
          return { success: true, message: `已打开 ${match.displayName}` };
        }

        // 兜底：让系统根据名称解析（某些应用不在默认目录，但可被 LaunchServices 识别）
        await this.execFileAsync("open", ["-a", name]);
        return { success: true, message: `已打开 ${name}` };
      } catch (error) {
        if (!match) {
          throw new Error(`未找到已安装应用: ${name}`);
        }
        throw new Error(`打开应用失败: ${error.message}`);
      }
    }

    // 其他平台保持原有实现（由 safety.js 做基础字符校验）
    return new Promise((resolve, reject) => {
      let command;

      switch (this.platform) {
        case "win32":
          command = `start "" "${name}"`;
          break;
        case "linux":
          command = `${name}`;
          break;
        default:
          reject(new Error("不支持的平台"));
          return;
      }

      exec(command, (error, _stdout, _stderr) => {
        if (error) {
          reject(error);
        } else {
          resolve({ success: true, message: `已打开 ${name}` });
        }
      });
    });
  }

  async closeApplication(appName) {
    return new Promise((resolve, reject) => {
      let command;

      switch (this.platform) {
        case "win32":
          command = `taskkill /F /IM "${appName}.exe"`;
          break;
        case "darwin":
          command = `pkill -f "${appName}"`;
          break;
        case "linux":
          command = `pkill -f "${appName}"`;
          break;
        default:
          reject(new Error("不支持的平台"));
          return;
      }

      exec(command, (error, _stdout, _stderr) => {
        if (error) {
          reject(error);
        } else {
          resolve({ success: true, message: `已关闭 ${appName}` });
        }
      });
    });
  }

  async createFile(filePath, content = "") {
    try {
      const fullPath = path.resolve(this.homeDir, filePath);
      await fs.writeFile(fullPath, content, "utf8");
      return { success: true, message: `已创建文件 ${filePath}` };
    } catch (error) {
      throw new Error(`创建文件失败: ${error.message}`);
    }
  }

  async createFolder(folderPath) {
    try {
      const fullPath = path.resolve(this.homeDir, folderPath);
      await fs.mkdir(fullPath, { recursive: true });
      return { success: true, message: `已创建文件夹 ${folderPath}` };
    } catch (error) {
      throw new Error(`创建文件夹失败: ${error.message}`);
    }
  }

  async deleteFile(filePath) {
    try {
      const fullPath = path.resolve(this.homeDir, filePath);
      await fs.unlink(fullPath);
      return { success: true, message: `已删除文件 ${filePath}` };
    } catch (error) {
      throw new Error(`删除文件失败: ${error.message}`);
    }
  }

  async deleteFolder(folderPath) {
    try {
      const fullPath = path.resolve(this.homeDir, folderPath);
      await fs.rmdir(fullPath, { recursive: true });
      return { success: true, message: `已删除文件夹 ${folderPath}` };
    } catch (error) {
      throw new Error(`删除文件夹失败: ${error.message}`);
    }
  }

  async moveFile(source, destination) {
    try {
      const sourcePath = path.resolve(this.homeDir, source);
      const destPath = path.resolve(this.homeDir, destination);
      await fs.rename(sourcePath, destPath);
      return { success: true, message: `已移动文件从 ${source} 到 ${destination}` };
    } catch (error) {
      throw new Error(`移动文件失败: ${error.message}`);
    }
  }

  async copyFile(source, destination) {
    try {
      const sourcePath = path.resolve(this.homeDir, source);
      const destPath = path.resolve(this.homeDir, destination);
      await fs.copyFile(sourcePath, destPath);
      return { success: true, message: `已复制文件从 ${source} 到 ${destination}` };
    } catch (error) {
      throw new Error(`复制文件失败: ${error.message}`);
    }
  }

  async readFile(filePath) {
    try {
      const fullPath = path.resolve(this.homeDir, filePath);
      const content = await fs.readFile(fullPath, "utf8");
      return { success: true, content };
    } catch (error) {
      throw new Error(`读取文件失败: ${error.message}`);
    }
  }

  async listDirectory(dirPath) {
    try {
      const fullPath = path.resolve(this.homeDir, dirPath);
      const items = await fs.readdir(fullPath, { withFileTypes: true });

      const result = items.map((item) => ({
        name: item.name,
        isDirectory: item.isDirectory(),
        isFile: item.isFile(),
      }));

      return { success: true, items: result };
    } catch (error) {
      throw new Error(`列出目录失败: ${error.message}`);
    }
  }

  async executeSystemCommand(command) {
    return new Promise((resolve, reject) => {
      exec(command, (error, stdout, _stderr) => {
        if (error) {
          reject(error);
        } else {
          resolve({ success: true, output: stdout });
        }
      });
    });
  }

  async controlVolume(level) {
    let command;

    switch (this.platform) {
      case "win32":
        command = `nircmd.exe setsysvolume ${Math.round(level * 655.35)}`;
        break;
      case "darwin":
        command = `osascript -e "set volume output volume ${Math.round(level * 100)}"`;
        break;
      case "linux":
        command = `amixer set Master ${Math.round(level * 100)}%`;
        break;
      default:
        throw new Error("不支持的平台");
    }

    return this.executeSystemCommand(command);
  }

  async openUrl(url) {
    return new Promise((resolve, reject) => {
      let command;

      switch (this.platform) {
        case "win32":
          command = `start "" "${url}"`;
          break;
        case "darwin":
          command = `open "${url}"`;
          break;
        case "linux":
          command = `xdg-open "${url}"`;
          break;
        default:
          reject(new Error("不支持的平台"));
          return;
      }

      exec(command, (error, _stdout, _stderr) => {
        if (error) {
          reject(error);
        } else {
          resolve({ success: true, message: `已打开链接 ${url}` });
        }
      });
    });
  }

  async getSystemInfo() {
    try {
      const info = {
        platform: this.platform,
        arch: os.arch(),
        nodeVersion: process.version,
        homeDir: this.homeDir,
        freeMemory: os.freemem(),
        totalMemory: os.totalmem(),
        uptime: os.uptime(),
        loadavg: os.loadavg(),
      };

      return { success: true, info };
    } catch (error) {
      throw new Error(`获取系统信息失败: ${error.message}`);
    }
  }
}

module.exports = SystemController;
