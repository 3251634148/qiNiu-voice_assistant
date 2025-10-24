const fs = require("node:fs").promises;
const path = require("node:path");
const os = require("node:os");
const logger = require("../utils/logger");

class FileWriter {
  constructor() {
    this.baseDir = path.join(os.homedir(), "Documents", "VoiceAssistant");
    this.draftsDir = path.join(this.baseDir, "drafts");
    this.finalDir = path.join(this.baseDir, "final");
  }

  async initialize() {
    try {
      await this.ensureDir(this.baseDir);
      await this.ensureDir(this.draftsDir);
      await this.ensureDir(this.finalDir);
      logger.info("FileWriter初始化完成", { baseDir: this.baseDir });
    } catch (error) {
      logger.error("FileWriter初始化失败:", error);
      throw error;
    }
  }

  async ensureDir(dirPath) {
    try {
      await fs.access(dirPath);
    } catch {
      await fs.mkdir(dirPath, { recursive: true });
      logger.info(`创建目录: ${dirPath}`);
    }
  }

  async saveDraft(content, filename = null) {
    try {
      await this.initialize();

      const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
      const defaultFilename = `draft_${timestamp}.txt`;
      const finalFilename = filename || defaultFilename;
      const filePath = path.join(this.draftsDir, finalFilename);

      await fs.writeFile(filePath, content, "utf8");

      logger.info(`保存草稿文件: ${filePath}`);

      return {
        success: true,
        filePath: filePath,
        filename: finalFilename,
        size: content.length,
        timestamp: new Date().toISOString(),
      };
    } catch (error) {
      logger.error("保存草稿失败:", error);
      throw new Error(`保存草稿失败: ${error.message}`);
    }
  }

  async finalize(draftFilename, finalFilename = null) {
    try {
      const draftPath = path.join(this.draftsDir, draftFilename);

      // 检查草稿文件是否存在
      await fs.access(draftPath);

      const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
      const defaultFinalFilename = draftFilename
        .replace(/^draft_/, "final_")
        .replace(/\.txt$/, `_final_${timestamp}.txt`);
      const finalName = finalFilename || defaultFinalFilename;
      const finalPath = path.join(this.finalDir, finalName);

      // 移动文件到最终目录
      await fs.rename(draftPath, finalPath);

      logger.info(`文件已最终化: ${draftPath} -> ${finalPath}`);

      return {
        success: true,
        draftPath: draftPath,
        finalPath: finalPath,
        filename: finalName,
        timestamp: new Date().toISOString(),
      };
    } catch (error) {
      logger.error("文件最终化失败:", error);
      throw new Error(`文件最终化失败: ${error.message}`);
    }
  }

  async writeFile(filePath, content, mode = "create") {
    try {
      await this.initialize();

      // 如果是相对路径，基于baseDir解析
      const absolutePath = path.isAbsolute(filePath) ? filePath : path.join(this.baseDir, filePath);

      // 确保目录存在
      await this.ensureDir(path.dirname(absolutePath));

      let result;

      switch (mode) {
        case "create":
          await fs.writeFile(absolutePath, content, "utf8");
          result = { action: "created", size: content.length };
          break;

        case "append": {
          await fs.appendFile(absolutePath, content, "utf8");
          const stats = await fs.stat(absolutePath);
          result = { action: "appended", size: stats.size };
          break;
        }

        case "overwrite":
          await fs.writeFile(absolutePath, content, "utf8");
          result = { action: "overwritten", size: content.length };
          break;

        default:
          throw new Error(`不支持的写入模式: ${mode}`);
      }

      logger.info(`文件写入完成: ${absolutePath}`, { mode, ...result });

      return {
        success: true,
        filePath: absolutePath,
        mode: mode,
        ...result,
        timestamp: new Date().toISOString(),
      };
    } catch (error) {
      logger.error("文件写入失败:", error);
      throw new Error(`文件写入失败: ${error.message}`);
    }
  }

  async readFile(filePath) {
    try {
      const absolutePath = path.isAbsolute(filePath) ? filePath : path.join(this.baseDir, filePath);

      const content = await fs.readFile(absolutePath, "utf8");
      const stats = await fs.stat(absolutePath);

      return {
        success: true,
        filePath: absolutePath,
        content: content,
        size: stats.size,
        modified: stats.mtime.toISOString(),
      };
    } catch (error) {
      logger.error("文件读取失败:", error);
      throw new Error(`文件读取失败: ${error.message}`);
    }
  }

  async listFiles(directory = null) {
    try {
      const targetDir = directory ? path.join(this.baseDir, directory) : this.baseDir;

      const entries = await fs.readdir(targetDir, { withFileTypes: true });
      const files = [];

      for (const entry of entries) {
        if (entry.isFile()) {
          const filePath = path.join(targetDir, entry.name);
          const stats = await fs.stat(filePath);

          files.push({
            name: entry.name,
            path: filePath,
            size: stats.size,
            modified: stats.mtime.toISOString(),
            type: "file",
          });
        } else if (entry.isDirectory()) {
          files.push({
            name: entry.name,
            path: path.join(targetDir, entry.name),
            type: "directory",
          });
        }
      }

      return {
        success: true,
        directory: targetDir,
        files: files.sort((a, b) => b.modified.localeCompare(a.modified)),
      };
    } catch (error) {
      logger.error("列出文件失败:", error);
      throw new Error(`列出文件失败: ${error.message}`);
    }
  }

  async deleteFile(filePath) {
    try {
      const absolutePath = path.isAbsolute(filePath) ? filePath : path.join(this.baseDir, filePath);

      await fs.unlink(absolutePath);

      logger.info(`文件已删除: ${absolutePath}`);

      return {
        success: true,
        filePath: absolutePath,
        timestamp: new Date().toISOString(),
      };
    } catch (error) {
      logger.error("文件删除失败:", error);
      throw new Error(`文件删除失败: ${error.message}`);
    }
  }

  async getStats() {
    try {
      const [drafts, finals] = await Promise.all([
        this.listFiles("drafts"),
        this.listFiles("final"),
      ]);

      const totalDrafts = drafts.files.filter((f) => f.type === "file").length;
      const totalFinals = finals.files.filter((f) => f.type === "file").length;

      return {
        baseDir: this.baseDir,
        draftsDir: this.draftsDir,
        finalDir: this.finalDir,
        totalDrafts: totalDrafts,
        totalFinals: totalFinals,
        totalFiles: totalDrafts + totalFinals,
      };
    } catch (error) {
      logger.error("获取统计信息失败:", error);
      throw new Error(`获取统计信息失败: ${error.message}`);
    }
  }

  // 验证文件路径安全性
  validatePath(filePath) {
    // 防止路径遍历攻击
    const normalizedPath = path.normalize(filePath);

    if (normalizedPath.includes("..")) {
      throw new Error("不安全的文件路径");
    }

    // 限制在baseDir内
    const absolutePath = path.isAbsolute(normalizedPath)
      ? normalizedPath
      : path.join(this.baseDir, normalizedPath);
    const relativePath = path.relative(this.baseDir, absolutePath);

    if (relativePath.startsWith("..")) {
      throw new Error("文件路径超出允许范围");
    }

    return absolutePath;
  }
}

module.exports = FileWriter;
