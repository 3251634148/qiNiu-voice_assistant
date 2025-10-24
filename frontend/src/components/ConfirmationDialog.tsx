import type { ConfirmationRequest } from "../types";

interface ConfirmationDialogProps {
  confirmation: ConfirmationRequest;
  onConfirm: (approved: boolean) => void;
  onCancel: () => void;
}

export function ConfirmationDialog({ confirmation, onConfirm, onCancel }: ConfirmationDialogProps) {
  const getRiskLevelColor = (level: string) => {
    switch (level) {
      case "high":
        return "text-red-600 bg-red-50 border-red-200";
      case "medium":
        return "text-yellow-600 bg-yellow-50 border-yellow-200";
      case "low":
        return "text-green-600 bg-green-50 border-green-200";
      default:
        return "text-gray-600 bg-gray-50 border-gray-200";
    }
  };

  const getRiskLevelText = (level: string) => {
    switch (level) {
      case "high":
        return "高风险";
      case "medium":
        return "中等风险";
      case "low":
        return "低风险";
      default:
        return "未知风险";
    }
  };

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl max-w-md w-full mx-4 border-2 border-gray-200">
        <div className="p-6">
          <div className="flex items-center mb-4">
            <div
              className={`w-3 h-3 rounded-full mr-3 ${
                confirmation.riskLevel === "high"
                  ? "bg-red-500"
                  : confirmation.riskLevel === "medium"
                    ? "bg-yellow-500"
                    : "bg-green-500"
              }`}
            ></div>
            <h3 className="text-lg font-semibold text-gray-900">确认操作</h3>
          </div>

          <div
            className={`mb-4 p-3 rounded-lg border ${getRiskLevelColor(confirmation.riskLevel)}`}
          >
            <div className="flex items-center justify-between mb-2">
              <span className="font-medium">风险等级:</span>
              <span className="font-bold">{getRiskLevelText(confirmation.riskLevel)}</span>
            </div>
            <p className="text-sm text-gray-700">{confirmation.reason}</p>
          </div>

          <div className="mb-4">
            <h4 className="font-medium text-gray-900 mb-2">操作摘要:</h4>
            <p className="text-gray-700">{confirmation.summary}</p>
          </div>

          <div className="mb-4">
            <h4 className="font-medium text-gray-900 mb-2">详细信息:</h4>
            <div className="bg-gray-50 p-3 rounded border">
              <p className="text-sm font-mono">
                <span className="font-medium">工具:</span> {confirmation.toolCall.name}
              </p>
              {Object.entries(confirmation.toolCall.arguments).map(([key, value]) => (
                <p key={key} className="text-sm font-mono">
                  <span className="font-medium">{key}:</span> {String(value)}
                </p>
              ))}
            </div>
          </div>

          {confirmation.suggestions && confirmation.suggestions.length > 0 && (
            <div className="mb-6">
              <h4 className="font-medium text-gray-900 mb-2">建议:</h4>
              <ul className="list-disc list-inside text-sm text-gray-700 space-y-1">
                {confirmation.suggestions.map((suggestion, index) => (
                  <li key={index}>{suggestion}</li>
                ))}
              </ul>
            </div>
          )}

          <div className="flex justify-end space-x-3">
            <button
              type="button"
              onClick={() => onCancel()}
              className="px-4 py-2 text-gray-700 bg-gray-100 hover:bg-gray-200 rounded-lg transition-colors duration-200 font-medium"
            >
              取消
            </button>
            <button
              type="button"
              onClick={() => onConfirm(true)}
              className={`px-4 py-2 rounded-lg transition-colors duration-200 font-medium ${
                confirmation.riskLevel === "high"
                  ? "bg-red-600 hover:bg-red-700 text-white"
                  : confirmation.riskLevel === "medium"
                    ? "bg-yellow-600 hover:bg-yellow-700 text-white"
                    : "bg-blue-600 hover:bg-blue-700 text-white"
              }`}
            >
              确认执行
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default ConfirmationDialog;
