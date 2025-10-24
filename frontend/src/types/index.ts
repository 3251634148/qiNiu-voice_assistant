export interface Message {
  id: string;
  type:
    | "user"
    | "assistant"
    | "system"
    | "tool_call"
    | "tool_result"
    | "safety_warning"
    | "canceled";
  content: string;
  timestamp: Date;
  audioData?: ArrayBuffer;
  metadata?: {
    toolCall?: ToolCall;
    toolResult?: any;
    riskLevel?: string;
    confirmationId?: string;
    [key: string]: any;
  };
}

export interface ToolCall {
  id: string;
  name: string;
  arguments: Record<string, any>;
}

export interface ConfirmationRequest {
  id: string;
  toolCall: ToolCall;
  riskLevel: "low" | "medium" | "high";
  summary: string;
  reason: string;
  suggestions: string[];
  timestamp: string;
  expiresAt: string;
}

export interface ToolResult {
  type: "success" | "error";
  toolCall: {
    id: string;
    name: string;
  };
  result?: any;
  error?: string;
  timestamp: string;
}

export interface Action {
  type: string;
  target?: string;
  path?: string;
  content?: string;
  source?: string;
  destination?: string;
  command?: string;
  level?: number;
  url?: string;
}

export interface AssistantResponse {
  text: string;
  actions: Action[];
}

export interface ActionResult {
  action: Action;
  result: {
    success: boolean;
    message?: string;
    content?: string;
    items?: any[];
    output?: string;
    error?: string;
  };
}

export interface SystemInfo {
  platform: string;
  arch: string;
  nodeVersion: string;
  homeDir: string;
  freeMemory: number;
  totalMemory: number;
  uptime: number;
  loadavg: number[];
}

export interface VoiceState {
  isListening: boolean;
  isSpeaking: boolean;
  volume: number;
}

export interface ConnectionState {
  connected: boolean;
  connecting: boolean;
  error?: string;
}

export interface SessionState {
  hasPendingToolCall: boolean;
  hasPendingConfirmation: boolean;
  isCanceled: boolean;
  confirmationRequest?: ConfirmationRequest;
}
