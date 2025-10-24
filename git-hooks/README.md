# Git Hooks

这个目录包含项目的 Git hooks，用于在特定的 Git 操作时自动执行检查和验证。

## 设置说明

### 1. 配置 Git 使用自定义 hooks 目录

```bash
git config core.hooksPath git-hooks
```

### 2. 确保脚本有执行权限

```bash
chmod +x git-hooks/*
```

## 可用的 Hooks

### pre-commit

在每次 `git commit` 之前自动运行，执行以下检查：

- **Biome 格式检查**：确保代码格式符合项目标准
- **Biome Lint 检查**：检查代码质量和潜在问题

#### 特性

- ✅ 只检查即将提交的文件（使用 `git diff --cached`）
- ✅ 支持 JavaScript、TypeScript、JSX、TSX、JSON 文件
- ✅ 提供清晰的错误信息和修复建议
- ✅ 检查失败时阻止提交

#### 如果检查失败

当格式或 lint 检查失败时，commit 会被阻止。你可以：

1. **修复格式问题**：
   ```bash
   npx @biomejs/biome format --write .
   ```

2. **修复 lint 问题**：
   ```bash
   npx @biomejs/biome lint --apply .
   ```

3. **查看详细的 lint 问题**：
   ```bash
   npx @biomejs/biome lint .
   ```

#### 跳过 hooks（不推荐）

如果确实需要跳过 pre-commit 检查：

```bash
git commit --no-verify -m "commit message"
```

## 依赖要求

- Node.js 和 npm
- 项目根目录需要有 `biome.json` 配置文件
- 安装了 `@biomejs/biome` 包

## 故障排除

### Hook 没有执行

1. 确认 Git hooks 路径配置正确：
   ```bash
   git config core.hooksPath
   ```

2. 确认脚本有执行权限：
   ```bash
   ls -la git-hooks/
   ```

### Biome 命令找不到

确保项目安装了 Biome：

```bash
npm install --save-dev @biomejs/biome
```

或全局安装：

```bash
npm install -g @biomejs/biome
```