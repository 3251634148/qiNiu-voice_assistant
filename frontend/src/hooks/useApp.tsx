import type React from "react";
import { createContext, type ReactNode, useContext, useReducer } from "react";
import type { ConnectionState, Message, SessionState, VoiceState } from "../types";
import { generateId } from "../utils/helpers";

interface AppState {
  messages: Message[];
  voiceState: VoiceState;
  connectionState: ConnectionState;
  sessionState: SessionState;
  systemInfo: any;
}

type AppAction =
  | { type: "ADD_MESSAGE"; payload: Message }
  | { type: "SET_VOICE_STATE"; payload: Partial<VoiceState> }
  | { type: "SET_CONNECTION_STATE"; payload: Partial<ConnectionState> }
  | { type: "SET_SESSION_STATE"; payload: Partial<SessionState> }
  | { type: "SET_SYSTEM_INFO"; payload: any }
  | { type: "CLEAR_MESSAGES" };

const initialState: AppState = {
  messages: [],
  voiceState: {
    isListening: false,
    isSpeaking: false,
    volume: 0.7,
  },
  connectionState: {
    connected: false,
    connecting: false,
  },
  sessionState: {
    hasPendingToolCall: false,
    hasPendingConfirmation: false,
    isCanceled: false,
  },
  systemInfo: null,
};

function appReducer(state: AppState, action: AppAction): AppState {
  switch (action.type) {
    case "ADD_MESSAGE":
      return {
        ...state,
        messages: [...state.messages, action.payload],
      };
    case "SET_VOICE_STATE":
      return {
        ...state,
        voiceState: { ...state.voiceState, ...action.payload },
      };
    case "SET_CONNECTION_STATE":
      return {
        ...state,
        connectionState: { ...state.connectionState, ...action.payload },
      };
    case "SET_SESSION_STATE":
      return {
        ...state,
        sessionState: { ...state.sessionState, ...action.payload },
      };
    case "SET_SYSTEM_INFO":
      return {
        ...state,
        systemInfo: action.payload,
      };
    case "CLEAR_MESSAGES":
      return {
        ...state,
        messages: [],
      };
    default:
      return state;
  }
}

interface AppContextType {
  state: AppState;
  dispatch: React.Dispatch<AppAction>;
  addMessage: (
    type:
      | "user"
      | "assistant"
      | "system"
      | "tool_call"
      | "tool_result"
      | "safety_warning"
      | "canceled",
    content: string,
    metadata?: any
  ) => void;
  clearMessages: () => void;
  setVoiceState: (state: Partial<VoiceState>) => void;
  setConnectionState: (state: Partial<ConnectionState>) => void;
  setSessionState: (state: Partial<SessionState>) => void;
}

const AppContext = createContext<AppContextType | undefined>(undefined);

interface AppProviderProps {
  children: ReactNode;
}

export function AppProvider({ children }: AppProviderProps) {
  const [state, dispatch] = useReducer(appReducer, initialState);

  const addMessage = (
    type:
      | "user"
      | "assistant"
      | "system"
      | "tool_call"
      | "tool_result"
      | "safety_warning"
      | "canceled",
    content: string,
    metadata?: any
  ) => {
    const message: Message = {
      id: generateId(),
      type,
      content,
      timestamp: new Date(),
      metadata,
    };
    dispatch({ type: "ADD_MESSAGE", payload: message });
  };

  const clearMessages = () => {
    dispatch({ type: "CLEAR_MESSAGES" });
  };

  const setVoiceState = (newState: Partial<VoiceState>) => {
    dispatch({ type: "SET_VOICE_STATE", payload: newState });
  };

  const setConnectionState = (newState: Partial<ConnectionState>) => {
    dispatch({ type: "SET_CONNECTION_STATE", payload: newState });
  };

  const setSessionState = (newState: Partial<SessionState>) => {
    dispatch({ type: "SET_SESSION_STATE", payload: newState });
  };

  return (
    <AppContext.Provider
      value={{
        state,
        dispatch,
        addMessage,
        clearMessages,
        setVoiceState,
        setConnectionState,
        setSessionState,
      }}
    >
      {children}
    </AppContext.Provider>
  );
}

export function useApp() {
  const context = useContext(AppContext);
  if (context === undefined) {
    throw new Error("useApp must be used within an AppProvider");
  }
  return context;
}
