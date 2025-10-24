const { exec } = require("node:child_process");
const util = require("node:util");
const logger = require("../utils/logger");

class MusicController {
  constructor() {
    this.execPromise = util.promisify(exec);
    this.currentPlayer = null;
    this.isPlaying = false;
  }

  async playMusic(source, query = null) {
    try {
      logger.info(`播放音乐 - 来源: ${source}, 查询: ${query}`);

      switch (source) {
        case "spotify":
          return await this.playSpotify(query);
        case "apple":
          return await this.playAppleMusic(query);
        case "local":
          return await this.playLocalMusic(query);
        default:
          throw new Error(`不支持的音乐来源: ${source}`);
      }
    } catch (error) {
      logger.error("播放音乐失败:", error);
      throw error;
    }
  }

  async stopMusic() {
    try {
      logger.info("停止音乐播放");

      const stopCommands = [
        "osascript -e 'tell application \"Spotify\" to pause' 2>/dev/null",
        "osascript -e 'tell application \"Music\" to pause' 2>/dev/null",
        "killall afplay 2>/dev/null",
      ];

      const results = [];

      for (const command of stopCommands) {
        try {
          const result = await this.execPromise(command);
          if (result.stdout || result.stderr) {
            results.push(result.stdout || result.stderr);
          }
        } catch (error) {
          // 忽略错误，继续尝试其他命令
          results.push(error.message);
        }
      }

      this.isPlaying = false;
      this.currentPlayer = null;

      return {
        success: true,
        message: "已停止音乐播放",
        results: results,
      };
    } catch (error) {
      logger.error("停止音乐失败:", error);
      throw new Error(`停止音乐失败: ${error.message}`);
    }
  }

  async playSpotify(query = null) {
    try {
      // 检查Spotify是否运行
      const isRunning = await this.checkAppRunning("Spotify");

      if (!isRunning) {
        await this.execPromise("open -a Spotify");
        await this.sleep(2000); // 等待Spotify启动
      }

      let script;
      if (query) {
        // 搜索并播放
        script = `
          tell application "Spotify"
            activate
            set searchResults to search "${query}"
            if (count of searchResults) > 0 then
              play track (item 1 of searchResults)
              return "正在播放: " & (name of (item 1 of searchResults))
            else
              return "未找到相关音乐"
            end if
          end tell
        `;
      } else {
        // 继续播放
        script = `
          tell application "Spotify"
            activate
            play
            return "继续播放Spotify"
          end tell
        `;
      }

      const result = await this.execPromise(`osascript -e '${script}'`);

      this.currentPlayer = "spotify";
      this.isPlaying = true;

      return {
        success: true,
        source: "spotify",
        query: query,
        message: result.stdout.trim(),
        player: "Spotify",
      };
    } catch (error) {
      throw new Error(`Spotify播放失败: ${error.message}`);
    }
  }

  async playAppleMusic(query = null) {
    try {
      // 检测Apple Music应用名称（新系统是Music，旧系统是iTunes）
      let appName = "Music";
      try {
        await this.execPromise("osascript -e 'tell application \"Music\" to get name' 2>/dev/null");
      } catch (_error) {
        appName = "iTunes";
      }

      // 检查Apple Music是否运行
      const isRunning = await this.checkAppRunning(appName);

      if (!isRunning) {
        await this.execPromise(`open -a "${appName}"`);
        await this.sleep(3000); // 等待Music启动，增加等待时间
      }

      // 先检查音乐库是否有歌曲
      const libraryCheck = await this.execPromise(`
        osascript -e '
          tell application "${appName}"
            try
              set trackCount to count of tracks of library playlist 1
              return trackCount
            on error
              return 0
            end try
          end tell'
      `);

      const trackCount = parseInt(libraryCheck.stdout.trim(), 10);

      if (trackCount === 0) {
        return {
          success: false,
          source: "apple",
          query: query,
          message: `音乐库为空，请先在${appName}中添加一些歌曲`,
          player: appName,
          suggestion: "您可以通过Apple Music订阅或导入本地音乐文件来添加歌曲",
        };
      }

      let script;
      if (query) {
        // 搜索并播放 - 使用更兼容的AppleScript语法
        script = `
          tell application "${appName}"
            activate
            try
              -- 尝试搜索整个资料库
              set searchResults to search playlist 1 for "${query}"
              if (count of searchResults) > 0 then
                set foundTrack to item 1 of searchResults
                play foundTrack
                return "正在播放: " & (name of foundTrack)
              else
                -- 如果没找到，尝试搜索所有播放列表
                repeat with aPlaylist in playlists
                  set searchResults to search aPlaylist for "${query}"
                  if (count of searchResults) > 0 then
                    set foundTrack to item 1 of searchResults
                    play foundTrack
                    return "正在播放: " & (name of foundTrack)
                  end if
                end repeat
                return "未找到相关音乐: ${query}，请尝试其他关键词"
              end if
            on error errMsg
              return "搜索失败: " & errMsg
            end try
          end tell
        `;
      } else {
        // 继续播放
        script = `
          tell application "${appName}"
            activate
            if player state is not playing then
              play
            end if
            return "继续播放${appName}"
          end tell
        `;
      }

      const result = await this.execPromise(`osascript -e '${script}'`);

      this.currentPlayer = "apple";
      this.isPlaying = true;

      return {
        success: true,
        source: "apple",
        query: query,
        message: result.stdout.trim(),
        player: appName,
        trackCount: trackCount,
      };
    } catch (error) {
      throw new Error(`Apple Music播放失败: ${error.message}`);
    }
  }

  async playLocalMusic(query = null) {
    try {
      const musicDir = `${process.env.HOME}/Music`;

      if (!query) {
        // 随机播放音乐目录中的文件
        const result = await this.execPromise(
          `find "${musicDir}" -name "*.mp3" -o -name "*.m4a" -o -name "*.wav" | head -1`
        );
        const musicFile = result.stdout.trim();

        if (!musicFile) {
          throw new Error("音乐目录中没有找到音频文件");
        }

        return await this.playLocalFile(musicFile);
      } else {
        // 搜索匹配的文件
        const searchResult = await this.execPromise(
          `find "${musicDir}" -iname "*${query}*" -name "*.mp3" -o -iname "*${query}*" -name "*.m4a" -o -iname "*${query}*" -name "*.wav" | head -1`
        );
        const musicFile = searchResult.stdout.trim();

        if (!musicFile) {
          throw new Error(`未找到匹配的音乐: ${query}`);
        }

        return await this.playLocalFile(musicFile);
      }
    } catch (error) {
      throw new Error(`本地音乐播放失败: ${error.message}`);
    }
  }

  async playLocalFile(filePath) {
    try {
      // 使用afplay播放本地文件（后台运行）
      const _result = await this.execPromise(`afplay "${filePath}" &`);

      this.currentPlayer = "local";
      this.isPlaying = true;

      return {
        success: true,
        source: "local",
        filePath: filePath,
        message: `正在播放本地音乐: ${filePath.split("/").pop()}`,
        player: "afplay",
      };
    } catch (error) {
      throw new Error(`本地文件播放失败: ${error.message}`);
    }
  }

  async checkAppRunning(appName) {
    try {
      const result = await this.execPromise(`pgrep -f "${appName}"`);
      return result.stdout.trim().length > 0;
    } catch {
      return false;
    }
  }

  async getMusicStatus() {
    try {
      const status = {
        isPlaying: this.isPlaying,
        currentPlayer: this.currentPlayer,
        info: null,
      };

      if (this.currentPlayer === "spotify") {
        try {
          const result = await this.execPromise(`osascript -e '
            tell application "Spotify"
              if player state is playing then
                set trackName to name of current track
                set artistName to artist of current track
                set albumName to album of current track
                return trackName & " - " & artistName & " (" & albumName & ")"
              else
                return "Spotify未播放"
              end if
            end tell'`);

          status.info = result.stdout.trim();
        } catch (_error) {
          status.info = "无法获取Spotify状态";
        }
      } else if (this.currentPlayer === "apple") {
        try {
          const result = await this.execPromise(`osascript -e '
            tell application "Music"
              if player state is playing then
                set trackName to name of current track
                set artistName to artist of current track
                set albumName to album of current track
                return trackName & " - " & artistName & " (" & albumName & ")"
              else
                return "Apple Music未播放"
              end if
            end tell'`);

          status.info = result.stdout.trim();
        } catch (_error) {
          status.info = "无法获取Apple Music状态";
        }
      }

      return status;
    } catch (error) {
      logger.error("获取音乐状态失败:", error);
      return {
        isPlaying: false,
        currentPlayer: null,
        info: "状态获取失败",
      };
    }
  }

  async pauseMusic() {
    try {
      if (this.currentPlayer === "spotify") {
        await this.execPromise("osascript -e 'tell application \"Spotify\" to pause'");
      } else if (this.currentPlayer === "apple") {
        await this.execPromise("osascript -e 'tell application \"Music\" to pause'");
      } else if (this.currentPlayer === "local") {
        await this.execPromise("killall afplay");
      }

      this.isPlaying = false;

      return {
        success: true,
        message: "音乐已暂停",
      };
    } catch (error) {
      throw new Error(`暂停音乐失败: ${error.message}`);
    }
  }

  async nextTrack() {
    try {
      if (this.currentPlayer === "spotify") {
        await this.execPromise("osascript -e 'tell application \"Spotify\" to next track'");
      } else if (this.currentPlayer === "apple") {
        await this.execPromise("osascript -e 'tell application \"Music\" to next track'");
      } else {
        throw new Error("当前播放器不支持下一曲功能");
      }

      return {
        success: true,
        message: "切换到下一曲",
      };
    } catch (error) {
      throw new Error(`切换下一曲失败: ${error.message}`);
    }
  }

  async previousTrack() {
    try {
      if (this.currentPlayer === "spotify") {
        await this.execPromise("osascript -e 'tell application \"Spotify\" to previous track'");
      } else if (this.currentPlayer === "apple") {
        await this.execPromise("osascript -e 'tell application \"Music\" to previous track'");
      } else {
        throw new Error("当前播放器不支持上一曲功能");
      }

      return {
        success: true,
        message: "切换到上一曲",
      };
    } catch (error) {
      throw new Error(`切换上一曲失败: ${error.message}`);
    }
  }

  sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  getSupportedSources() {
    return ["spotify", "apple", "local"];
  }

  getAvailableCommands() {
    return ["play", "stop", "pause", "next", "previous", "status"];
  }
}

module.exports = MusicController;
