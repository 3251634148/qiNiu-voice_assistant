import { createContext, type ReactNode, useContext } from "react";

import { useSocket } from "./useSocket";

type SocketContextValue = ReturnType<typeof useSocket>;

const SocketContext = createContext<SocketContextValue | null>(null);

export function SocketProvider({ children }: { children: ReactNode }) {
  const socket = useSocket();

  return <SocketContext.Provider value={socket}>{children}</SocketContext.Provider>;
}

export function useSocketContext(): SocketContextValue {
  const context = useContext(SocketContext);
  if (!context) {
    throw new Error("useSocketContext must be used within a SocketProvider");
  }

  return context;
}
