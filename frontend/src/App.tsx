import { ConfirmationDialog } from "./components/ConfirmationDialog";
import { Header } from "./components/Header";
import { MessageList } from "./components/MessageList";
import { SystemInfoDisplay } from "./components/SystemInfoDisplay";
import { VoiceInput } from "./components/VoiceInput";
import { AppProvider, useApp } from "./hooks/useApp";
import { useSocket } from "./hooks/useSocket";
import "./index.css";

function AppContent() {
  const { state } = useApp();
  const { isInitialized, confirmAction, cancel } = useSocket();

  if (!isInitialized) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-primary-600 mx-auto mb-4"></div>
          <p className="text-lg font-medium text-gray-900">正在连接服务器...</p>
        </div>
      </div>
    );
  }

  const handleConfirm = (approved: boolean) => {
    if (state.sessionState.confirmationRequest) {
      confirmAction(state.sessionState.confirmationRequest.id, approved);
    }
  };

  const handleCancel = () => {
    cancel(true);
  };

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col">
      <Header />

      <main className="flex-1 flex flex-col max-w-4xl mx-auto w-full">
        <MessageList messages={state.messages} />

        {/* 系统信息面板 */}
        {state.systemInfo && (
          <div className="border-t border-gray-200 p-4">
            <SystemInfoDisplay systemInfo={state.systemInfo} />
          </div>
        )}

        <VoiceInput />
      </main>

      {/* 确认对话框 */}
      {state.sessionState.confirmationRequest && (
        <ConfirmationDialog
          confirmation={state.sessionState.confirmationRequest}
          onConfirm={handleConfirm}
          onCancel={handleCancel}
        />
      )}
    </div>
  );
}

function App() {
  return (
    <AppProvider>
      <AppContent />
    </AppProvider>
  );
}

export default App;
