import { AlertCircle, AlertTriangle, CheckCircle, Settings, User, XCircle } from "lucide-react";
import React from "react";
import robotAvatar from "../assets/robot-avatar.png";
import type { Message } from "../types";
import { formatTimestamp } from "../utils/helpers";

interface MessageItemProps {
  message: Message;
}

function MessageItem({ message }: MessageItemProps) {
  const getIcon = () => {
    switch (message.type) {
      case "user":
        return <User size={16} className="text-blue-500" />;
      case "assistant":
        return (
          <div className="w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 overflow-hidden">
            <img
              src={robotAvatar}
              alt="机器人头像"
              className="w-full h-full object-cover"
              onError={(e) => {
                console.error("机器人头像加载失败:", e);
                // 如果图片加载失败，回退到Bot图标
                e.currentTarget.style.display = "none";
                e.currentTarget.parentElement.innerHTML =
                  '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-green-500"><path d="M12 8V4H8"/><rect width="16" height="12" x="4" y="8" rx="2"/><path d="M2 14h2"/><path d="M20 14h2"/><path d="M15 13v2"/><path d="M9 13v2"/></svg>';
              }}
            />
          </div>
        );
      case "system":
        return <AlertCircle size={16} className="text-orange-500" />;
      case "tool_call":
        return <Settings size={16} className="text-purple-500" />;
      case "tool_result":
        return <CheckCircle size={16} className="text-teal-500" />;
      case "safety_warning":
        return <AlertTriangle size={16} className="text-red-500" />;
      case "canceled":
        return <XCircle size={16} className="text-gray-500" />;
      default:
        return null;
    }
  };

  const getBubbleClass = () => {
    switch (message.type) {
      case "user":
        return "bg-blue-500 text-white ml-auto max-w-[70%]";
      case "assistant":
        return "bg-green-500 text-white mr-auto max-w-[70%]";
      case "system":
        return "bg-orange-100 text-orange-800 mx-auto max-w-[80%] text-center text-sm";
      case "tool_call":
        return "bg-purple-100 text-purple-800 mr-auto max-w-[70%]";
      case "tool_result":
        return "bg-teal-100 text-teal-800 mr-auto max-w-[70%]";
      case "safety_warning":
        return "bg-red-100 text-red-800 mx-auto max-w-[80%] text-center text-sm";
      case "canceled":
        return "bg-gray-100 text-gray-800 mx-auto max-w-[70%] text-center text-sm";
      default:
        return "bg-gray-100 text-gray-800";
    }
  };

  const getBubbleStyle = () => {
    switch (message.type) {
      case "user":
        return { backgroundColor: "#3b82f6", color: "#ffffff" };
      case "assistant":
        return { backgroundColor: "#10b981", color: "#ffffff" };
      default:
        return {};
    }
  };

  const getRiskLevelBadge = () => {
    if (!message.metadata?.riskLevel) return null;

    const riskLevel = message.metadata.riskLevel as "high" | "medium" | "low";
    const colors = {
      high: "bg-red-500 text-white",
      medium: "bg-yellow-500 text-white",
      low: "bg-green-500 text-white",
    };

    const texts = {
      high: "高风险",
      medium: "中等风险",
      low: "低风险",
    };

    return (
      <span
        className={`inline-block px-2 py-1 rounded-full text-xs font-medium ${colors[riskLevel]}`}
      >
        {texts[riskLevel]}
      </span>
    );
  };

  const getIntentBadge = () => {
    const intent = message.metadata?.intent as any;
    if (!intent || message.type !== "assistant") {
      return null;
    }

    const modeTextMap: Record<string, string> = {
      ask: "提问式",
      act: "操作式",
      both: "提问+操作",
    };

    const modeText = modeTextMap[intent.mode] || "未知";
    const confidence =
      typeof intent.confidence === "number" && Number.isFinite(intent.confidence)
        ? `${Math.round(intent.confidence * 100)}%`
        : undefined;

    const actions = Array.isArray(intent.actions) ? intent.actions : [];
    const actionNames = actions
      .map((a: any) => a?.name)
      .filter(Boolean)
      .slice(0, 3)
      .join(", ");

    const tailParts = [
      actionNames ? `动作: ${actionNames}` : null,
      confidence ? `置信度: ${confidence}` : null,
    ].filter(Boolean);

    const tail = tailParts.length > 0 ? `（${tailParts.join("，")}）` : "";

    return <div className="text-xs opacity-80 mb-1">意图: {modeText}{tail}</div>;
  };

  return (
    <div
      className={`flex ${message.type === "user" ? "justify-end" : message.type === "assistant" || message.type === "tool_call" || message.type === "tool_result" ? "justify-start" : "justify-center"} mb-4`}
    >
      <div
        className={`flex items-start gap-2 ${message.type === "system" || message.type === "safety_warning" || message.type === "canceled" ? "flex-col" : ""}`}
      >
        {message.type !== "system" &&
          message.type !== "safety_warning" &&
          message.type !== "canceled" && <div className="flex-shrink-0 mt-1">{getIcon()}</div>}

        <div
          className={`${message.type === "system" || message.type === "safety_warning" || message.type === "canceled" ? "order-first" : ""}`}
        >
          <div
            className={`${getBubbleClass()} rounded-2xl px-4 py-2 shadow-sm`}
            style={getBubbleStyle()}
          >
            {getRiskLevelBadge() && <div className="mb-2">{getRiskLevelBadge()}</div>}
            {getIntentBadge()}
            <p className="text-sm leading-relaxed whitespace-pre-wrap">{message.content}</p>
            {message.metadata?.toolCall && (
              <div className="mt-2 pt-2 border-t border-current border-opacity-20">
                <p className="text-xs opacity-75">工具: {message.metadata.toolCall.name}</p>
              </div>
            )}
          </div>

          {message.type !== "system" &&
            message.type !== "safety_warning" &&
            message.type !== "canceled" && (
              <div className="text-xs text-gray-500 mt-1 px-1">
                {formatTimestamp(message.timestamp)}
              </div>
            )}
        </div>
      </div>
    </div>
  );
}

interface MessageListProps {
  messages: Message[];
}

export function MessageList({ messages }: MessageListProps) {
  const messagesEndRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, []);

  if (messages.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center text-gray-500">
        <div className="text-center">
          <img
            src={robotAvatar}
            alt="机器人头像"
            className="w-12 h-12 mx-auto mb-4 rounded-full object-cover"
            onError={(e) => {
              console.error("欢迎页面机器人头像加载失败:", e);
              e.currentTarget.style.display = "none";
              e.currentTarget.parentElement.innerHTML =
                '<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="mx-auto mb-4 text-gray-300"><path d="M12 8V4H8"/><rect width="16" height="12" x="4" y="8" rx="2"/><path d="M2 14h2"/><path d="M20 14h2"/><path d="M15 13v2"/><path d="M9 13v2"/></svg>';
            }}
          />
          <p className="text-lg font-medium">你好！我是你的智能语音助手</p>
          <p className="text-sm mt-2">点击麦克风或输入文字开始对话</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto p-4 space-y-1">
      {messages.map((message) => (
        <MessageItem key={message.id} message={message} />
      ))}
      <div ref={messagesEndRef} />
    </div>
  );
}
