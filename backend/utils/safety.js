const path = require("node:path");
const os = require("node:os");
const logger = require("../utils/logger");

class SafetyService {
  constructor() {
    this.initializeWhitelists();
  }

  initializeWhitelists() {
    // 允许的应用程序白名单
    this.allowedApps = [
      "spotify",
      "music",
      "itunes",
      "chrome",
      "safari",
      "firefox",
      "visual studio code",
      "vscode",
      "terminal",
      "finder",
      "calculator",
      "calendar",
      "mail",
      "notes",
      "pages",
      "numbers",
      "keynote",
      "preview",
      "photos",
      "system preferences",
      "activity monitor",
      "textedit",
      "reminder",
      "maps",
      "weather",
      "contacts",
    ];

    // 允许的目录白名单
    this.allowedDirectories = [
      path.join(os.homedir(), "Documents"),
      path.join(os.homedir(), "Desktop"),
      path.join(os.homedir(), "Downloads"),
      path.join(os.homedir(), "Music"),
      path.join(os.homedir(), "Pictures"),
      path.join(os.homedir(), "Movies"),
      path.join(os.homedir(), "Documents/VoiceAssistant"),
      "/tmp",
      "/var/tmp",
    ];

    // 危险命令黑名单
    this.dangerousCommands = [
      "rm -rf /",
      "sudo rm",
      "format",
      "fdisk",
      "mkfs",
      "shutdown",
      "reboot",
      "halt",
      "poweroff",
      "init 0",
      "killall",
      "pkill",
      "kill -9",
      "chmod 777",
      "chown",
      "passwd",
      "su",
      "sudo su",
      "crontab",
      "at",
      "batch",
      "nohup",
      "dd if=",
      "cat /dev/",
      "echo > /dev/",
      "mount",
      "umount",
      "fsck",
      "iptables",
      "ufw",
      "firewall",
      "route",
      "arp",
      "netstat",
      "sshd",
    ];

    // 危险文件扩展名
    this.dangerousExtensions = [
      ".exe",
      ".bat",
      ".cmd",
      ".com",
      ".pif",
      ".scr",
      ".vbs",
      ".js",
      ".jar",
      ".app",
      ".dmg",
      ".pkg",
      ".mpkg",
      ".deb",
      ".rpm",
      ".run",
      ".bin",
    ];

    // 敏感系统路径
    this.systemPaths = [
      "/bin",
      "/sbin",
      "/usr/bin",
      "/usr/sbin",
      "/etc",
      "/var",
      "/sys",
      "/proc",
      "/dev",
      "/boot",
      "/lib",
      "/lib64",
      "/usr/lib",
      "/usr/lib64",
    ];
  }

  async validateToolCall(toolCall, context = {}) {
    try {
      const { name, arguments: args } = toolCall;

      logger.info(`安全校验工具调用: ${name}`, { args });

      // 基础参数校验
      const basicValidation = this.validateBasicParameters(name, args);
      if (!basicValidation.valid) {
        return {
          allowed: false,
          riskLevel: "high",
          reason: basicValidation.reason,
          requiresConfirmation: true,
        };
      }

      // 根据工具类型进行特定校验
      let specificValidation;
      switch (name) {
        case "play_music":
          specificValidation = this.validatePlayMusic(args);
          break;
        case "stop_music":
          specificValidation = this.validateStopMusic(args);
          break;
        case "write_article":
          specificValidation = this.validateWriteArticle(args);
          break;
        case "write_file":
          specificValidation = this.validateWriteFile(args);
          break;
        case "open_app":
          specificValidation = this.validateOpenApp(args);
          break;
        default:
          specificValidation = { valid: false, reason: `未知的工具类型: ${name}` };
      }

      if (!specificValidation.valid) {
        return {
          allowed: false,
          riskLevel: "high",
          reason: specificValidation.reason,
          requiresConfirmation: true,
        };
      }

      // 评估风险等级
      const riskAssessment = this.assessRisk(name, args, context);

      return {
        allowed: true,
        riskLevel: riskAssessment.level,
        reason: riskAssessment.reason,
        requiresConfirmation: riskAssessment.requiresConfirmation,
        suggestions: riskAssessment.suggestions,
      };
    } catch (error) {
      logger.error("安全校验失败:", error);
      return {
        allowed: false,
        riskLevel: "high",
        reason: `安全校验过程出错: ${error.message}`,
        requiresConfirmation: true,
      };
    }
  }

  validateBasicParameters(name, args) {
    if (!name || typeof name !== "string") {
      return { valid: false, reason: "工具名称无效" };
    }

    if (!args || typeof args !== "object") {
      return { valid: false, reason: "工具参数无效" };
    }

    // 检查参数中是否包含危险内容 - 使用更精确的匹配
    const argsString = JSON.stringify(args).toLowerCase();
    for (const dangerousCmd of this.dangerousCommands) {
      // 使用正则表达式确保完整匹配命令，避免误报
      const escapedCmd = dangerousCmd.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      const regex = new RegExp(`\\b${escapedCmd}\\b`, "i");
      if (regex.test(argsString)) {
        return { valid: false, reason: `参数包含危险命令: ${dangerousCmd}` };
      }
    }

    return { valid: true };
  }

  validatePlayMusic(args) {
    const { source, query } = args;

    if (!source) {
      return { valid: false, reason: "缺少音乐来源参数" };
    }

    const allowedSources = ["spotify", "apple", "local"];
    if (!allowedSources.includes(source)) {
      return { valid: false, reason: `不支持的音乐来源: ${source}` };
    }

    if (query && typeof query !== "string") {
      return { valid: false, reason: "查询参数必须是字符串" };
    }

    if (query && query.length > 200) {
      return { valid: false, reason: "查询参数过长" };
    }

    return { valid: true };
  }

  validateStopMusic(_args) {
    // stop_music 不需要参数，总是安全的
    return { valid: true };
  }

  validateWriteArticle(args) {
    const { topic, style, length } = args;

    if (!topic) {
      return { valid: false, reason: "缺少文章主题参数" };
    }

    if (typeof topic !== "string" || topic.length > 500) {
      return { valid: false, reason: "文章主题无效或过长" };
    }

    if (style && !["formal", "casual", "professional", "creative"].includes(style)) {
      return { valid: false, reason: `不支持的写作风格: ${style}` };
    }

    if (length && !["short", "medium", "long"].includes(length)) {
      return { valid: false, reason: `不支持的文章长度: ${length}` };
    }

    return { valid: true };
  }

  validateWriteFile(args) {
    const { path: filePath, content, mode } = args;

    if (!filePath) {
      return { valid: false, reason: "缺少文件路径参数" };
    }

    if (typeof filePath !== "string") {
      return { valid: false, reason: "文件路径必须是字符串" };
    }

    // 检查路径遍历攻击
    if (filePath.includes("..") || filePath.includes("~")) {
      return { valid: false, reason: "文件路径包含不安全字符" };
    }

    // 检查是否在允许的目录内
    const absolutePath = path.isAbsolute(filePath) ? filePath : path.join(os.homedir(), filePath);
    const isAllowed = this.allowedDirectories.some((allowedDir) =>
      absolutePath.startsWith(allowedDir)
    );

    if (!isAllowed) {
      return { valid: false, reason: "文件路径不在允许的目录范围内" };
    }

    // 检查文件扩展名
    const ext = path.extname(filePath).toLowerCase();
    if (this.dangerousExtensions.includes(ext)) {
      return { valid: false, reason: `不允许的文件类型: ${ext}` };
    }

    if (content && typeof content !== "string") {
      return { valid: false, reason: "文件内容必须是字符串" };
    }

    if (content && content.length > 1000000) {
      // 1MB限制
      return { valid: false, reason: "文件内容过大" };
    }

    if (mode && !["create", "append", "overwrite"].includes(mode)) {
      return { valid: false, reason: `不支持的写入模式: ${mode}` };
    }

    return { valid: true };
  }

  validateOpenApp(args) {
    const { name } = args;

    if (!name) {
      return { valid: false, reason: "缺少应用程序名称参数" };
    }

    if (typeof name !== "string") {
      return { valid: false, reason: "应用程序名称必须是字符串" };
    }

    // 检查是否在白名单中
    const normalizedName = name.toLowerCase();
    const isAllowed = this.allowedApps.some(
      (allowedApp) => normalizedName.includes(allowedApp) || allowedApp.includes(normalizedName)
    );

    if (!isAllowed) {
      return {
        valid: false,
        reason: `应用程序 "${name}" 不在允许的白名单中`,
        suggestion: "请选择允许的应用程序或联系管理员添加到白名单",
      };
    }

    return { valid: true };
  }

  assessRisk(toolName, args, _context) {
    // 基础风险评估
    const riskLevels = {
      play_music: { level: "low", requiresConfirmation: false },
      stop_music: { level: "low", requiresConfirmation: false },
      write_article: { level: "medium", requiresConfirmation: false },
      write_file: { level: "medium", requiresConfirmation: true },
      open_app: { level: "low", requiresConfirmation: false },
    };

    const baseRisk = riskLevels[toolName] || { level: "medium", requiresConfirmation: true };

    // 根据参数调整风险等级
    const adjustedRisk = { ...baseRisk };

    if (toolName === "write_file") {
      const { path: filePath, mode } = args;

      // 覆盖模式风险更高
      if (mode === "overwrite") {
        adjustedRisk.level = "high";
        adjustedRisk.requiresConfirmation = true;
      }

      // 系统路径风险更高
      const absolutePath = path.isAbsolute(filePath) ? filePath : path.join(os.homedir(), filePath);
      if (this.systemPaths.some((sysPath) => absolutePath.startsWith(sysPath))) {
        adjustedRisk.level = "high";
        adjustedRisk.requiresConfirmation = true;
      }
    }

    if (toolName === "open_app") {
      const { name } = args;

      // 系统工具风险更高
      const systemApps = ["terminal", "activity monitor", "system preferences"];
      if (systemApps.includes(name.toLowerCase())) {
        adjustedRisk.level = "medium";
        adjustedRisk.requiresConfirmation = true;
      }
    }

    // 生成风险说明和建议
    const riskDescriptions = {
      low: "低风险操作，可以安全执行",
      medium: "中等风险操作，建议确认后执行",
      high: "高风险操作，必须用户确认才能执行",
    };

    return {
      level: adjustedRisk.level,
      requiresConfirmation: adjustedRisk.requiresConfirmation,
      reason: riskDescriptions[adjustedRisk.level],
      suggestions: this.generateSuggestions(toolName, args, adjustedRisk.level),
    };
  }

  generateSuggestions(toolName, args, riskLevel) {
    const suggestions = [];

    if (toolName === "write_file") {
      const { path: filePath, mode } = args;
      suggestions.push(
        `将${mode === "append" ? "追加到" : mode === "overwrite" ? "覆盖" : "创建"}文件: ${filePath}`
      );

      if (riskLevel === "high") {
        suggestions.push("此操作可能覆盖现有文件，请确认是否继续");
      }
    }

    if (toolName === "open_app") {
      const { name } = args;
      suggestions.push(`将打开应用程序: ${name}`);
    }

    if (toolName === "play_music") {
      const { source, query } = args;
      suggestions.push(`将播放${source}音乐${query ? `: ${query}` : ""}`);
    }

    return suggestions;
  }

  generateConfirmationRequest(toolCall, riskAssessment) {
    return {
      id: `confirm_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
      toolCall: {
        id: toolCall.id,
        name: toolCall.name,
        arguments: toolCall.arguments,
      },
      riskLevel: riskAssessment.level,
      summary: this.generateSummary(toolCall),
      reason: riskAssessment.reason,
      suggestions: riskAssessment.suggestions,
      timestamp: new Date().toISOString(),
      expiresAt: new Date(Date.now() + 5 * 60 * 1000).toISOString(), // 5分钟后过期
    };
  }

  generateSummary(toolCall) {
    const { name, arguments: args } = toolCall;

    switch (name) {
      case "play_music":
        return `播放${args.source}音乐${args.query ? `: ${args.query}` : ""}`;
      case "stop_music":
        return "停止音乐播放";
      case "write_article":
        return `写文章: ${args.topic}`;
      case "write_file":
        return `${args.mode === "append" ? "追加到" : args.mode === "overwrite" ? "覆盖" : "创建"}文件: ${args.path}`;
      case "open_app":
        return `打开应用程序: ${args.name}`;
      default:
        return `执行操作: ${name}`;
    }
  }

  // 更新白名单的方法
  updateAllowedApps(apps) {
    this.allowedApps = [...new Set([...this.allowedApps, ...apps])];
    logger.info("更新允许应用白名单", { apps });
  }

  updateAllowedDirectories(dirs) {
    this.allowedDirectories = [...new Set([...this.allowedDirectories, ...dirs])];
    logger.info("更新允许目录白名单", { dirs });
  }

  // 获取当前配置
  getSafetyConfig() {
    return {
      allowedApps: this.allowedApps,
      allowedDirectories: this.allowedDirectories,
      dangerousCommands: this.dangerousCommands,
      dangerousExtensions: this.dangerousExtensions,
      systemPaths: this.systemPaths,
    };
  }
}

module.exports = SafetyService;
