// Jest setup file
global.console = {
  ...console,
  // 在测试中禁用某些日志
  log: jest.fn(),
  debug: jest.fn(),
  info: jest.fn(),
  warn: jest.fn(),
  error: jest.fn(),
};

// Mock process.env
process.env.DASHSCOPE_API_KEY = "sk-846133080d6247e6a6ae8d2cd44e8d02";
process.env.NODE_ENV = "test";
