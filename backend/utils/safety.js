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

      logger.info(`安全校验工具调用: ${name}`, { args, context });

      if (context && context.allowLocalControl === false) {
        const localControlTools = ["write_file", "open_app", "file_control", "write_run_code", "send_message"];
        if (localControlTools.includes(name)) {
          return {
            allowed: false,
            riskLevel: "high",
            reason: "本地应用操控已被关闭",
            requiresConfirmation: false,
          };
        }
      }

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
        case "send_message":
          specificValidation = this.validateSendMessage(args);
          break;
        case "write_run_code":
          specificValidation = this.validateWriteRunCode(args);
          break;
        case "file_control":
          specificValidation = this.validateFileControl(args);
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
      // 优先用更稳健的匹配：对包含特殊字符的命令使用子串匹配，避免 \b 边界导致漏检
      const loweredCmd = String(dangerousCmd).toLowerCase();
      const needsSimpleMatch = /[^a-z0-9_\s]/i.test(dangerousCmd);

      if (needsSimpleMatch) {
        if (argsString.includes(loweredCmd)) {
          return { valid: false, reason: `参数包含危险命令: ${dangerousCmd}` };
        }
        continue;
      }

      // 对纯“单词/空格”命令使用边界匹配，降低误报
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

    // source 可选：缺省时由后端自动选择可用播放器/来源
    const allowedSources = ["spotify", "apple", "local"];
    if (source && !allowedSources.includes(source)) {
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

    const trimmedName = name.trim();
    if (!trimmedName) {
      return { valid: false, reason: "应用程序名称不能为空" };
    }

    if (trimmedName.length > 200) {
      return { valid: false, reason: "应用程序名称过长" };
    }

    // 禁止用户直接传路径，统一走“已安装应用检索”逻辑
    if (trimmedName.includes("/") || trimmedName.includes("\\")) {
      return { valid: false, reason: "应用程序名称不应包含路径分隔符" };
    }

    // 基础注入防护：禁止控制字符与明显的 shell 元字符
    if (/\r|\n|\0/.test(trimmedName) || /[;&|<>]/.test(trimmedName)) {
      return { valid: false, reason: "应用程序名称包含不安全字符" };
    }

    return { valid: true };
  }

  validateSendMessage(args) {
    const { target, content, channel } = args;

    if (!target) {
      return { valid: false, reason: "缺少消息目标参数" };
    }

    if (typeof target !== "string" || target.trim().length > 200) {
      return { valid: false, reason: "消息目标无效或过长" };
    }

    if (!content) {
      return { valid: false, reason: "缺少消息内容参数" };
    }

    if (typeof content !== "string" || content.length > 5000) {
      return { valid: false, reason: "消息内容无效或过长" };
    }

    if (channel && !["auto", "sms", "email", "im"].includes(channel)) {
      return { valid: false, reason: `不支持的发送渠道: ${channel}` };
    }

    return { valid: true };
  }

  validateWriteRunCode(args) {
    const { language, code, run } = args;

    if (!language || !["javascript", "python", "bash"].includes(language)) {
      return { valid: false, reason: "代码语言不支持或缺失" };
    }

    if (!code || typeof code !== "string" || code.length > 50000) {
      return { valid: false, reason: "代码内容无效或过长" };
    }

    if (typeof run !== "undefined" && typeof run !== "boolean") {
      return { valid: false, reason: "run 参数必须是布尔值" };
    }

    return { valid: true };
  }

  isPathAllowed(filePath) {
    if (!filePath || typeof filePath !== "string") {
      return { allowed: false, reason: "文件路径无效" };
    }

    if (filePath.includes("..") || filePath.includes("~")) {
      return { allowed: false, reason: "文件路径包含不安全字符" };
    }

    const absolutePath = path.isAbsolute(filePath) ? filePath : path.join(os.homedir(), filePath);
    const isAllowed = this.allowedDirectories.some((allowedDir) => absolutePath.startsWith(allowedDir));

    if (!isAllowed) {
      return { allowed: false, reason: "文件路径不在允许的目录范围内" };
    }

    const ext = path.extname(filePath).toLowerCase();
    if (ext && this.dangerousExtensions.includes(ext)) {
      return { allowed: false, reason: `不允许的文件类型: ${ext}` };
    }

    return { allowed: true };
  }

  validateFileControl(args) {
    const { operation, path: filePath, destination } = args;

    if (!operation || !["list", "read", "move", "copy", "delete", "mkdir"].includes(operation)) {
      return { valid: false, reason: "文件操作类型不支持或缺失" };
    }

    const pathCheck = this.isPathAllowed(filePath);
    if (!pathCheck.allowed) {
      return { valid: false, reason: pathCheck.reason };
    }

    if (destination) {
      const dstCheck = this.isPathAllowed(destination);
      if (!dstCheck.allowed) {
        return { valid: false, reason: `目标路径不安全: ${dstCheck.reason}` };
      }
    }

    return { valid: true };
  }

  assessRisk(toolName, args, _context) {
    // 基础风险评估
    const riskLevels = {
      play_music: { level: "low", requiresConfirmation: false },
      stop_music: { level: "low", requiresConfirmation: false },
      open_app: { level: "low", requiresConfirmation: false },
      write_article: { level: "medium", requiresConfirmation: false },
      write_file: { level: "medium", requiresConfirmation: true },
      send_message: { level: "medium", requiresConfirmation: true },
      write_run_code: { level: "high", requiresConfirmation: true },
      file_control: { level: "high", requiresConfirmation: true },
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
      if (typeof filePath === "string" && filePath.trim()) {
        const absolutePath = path.isAbsolute(filePath) ? filePath : path.join(os.homedir(), filePath);
        if (this.systemPaths.some((sysPath) => absolutePath.startsWith(sysPath))) {
          adjustedRisk.level = "high";
          adjustedRisk.requiresConfirmation = true;
        }
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
      const sourceText = source ? `${source} ` : "";
      const autoHint = source ? "" : "（自动选择播放器）";
      suggestions.push(`将播放${sourceText}音乐${query ? `: ${query}` : ""}${autoHint}`);
    }

    if (toolName === "send_message") {
      const { target, channel } = args;
      const channelText = channel && channel !== "auto" ? `（渠道: ${channel}）` : "";
      suggestions.push(`将发送消息给: ${target}${channelText}`);
    }

    if (toolName === "write_run_code") {
      const { language, run } = args;
      suggestions.push(`将编写${language}代码${run ? "并运行" : ""}`);
      if (riskLevel === "high") {
        suggestions.push("此操作可能执行本地代码，请确认是否继续");
      }
    }

    if (toolName === "file_control") {
      const { operation, path: filePath, destination } = args;
      const dstText = destination ? ` → ${destination}` : "";
      suggestions.push(`将执行文件操作: ${operation} ${filePath}${dstText}`);
      if (riskLevel === "high") {
        suggestions.push("此操作可能影响本地文件，请确认是否继续");
      }
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
      case "play_music": {
        const sourceText = args.source ? `${args.source} ` : "";
        const autoHint = args.source ? "" : "（自动选择播放器）";
        return `播放${sourceText}音乐${args.query ? `: ${args.query}` : ""}${autoHint}`;
      }
      case "stop_music":
        return "停止音乐播放";
      case "write_article":
        return `写文章: ${args.topic}`;
      case "write_file":
        return `${args.mode === "append" ? "追加到" : args.mode === "overwrite" ? "覆盖" : "创建"}文件: ${args.path}`;
      case "open_app":
        return `打开应用程序: ${args.name}`;
      case "send_message":
        return `发送消息给: ${args.target}`;
      case "write_run_code":
        return `编写${args.language}代码${args.run ? "并运行" : ""}`;
      case "file_control": {
        const dstText = args.destination ? ` → ${args.destination}` : "";
        return `文件操作: ${args.operation} ${args.path}${dstText}`;
      }
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
