import ReactDOM from "react-dom/client";
import App from "./App";

const rootElement = document.getElementById("root");
if (rootElement) {
  ReactDOM.createRoot(rootElement).render(
  // 在开发模式下禁用StrictMode以避免重复连接
  // <React.StrictMode>
  <App />
  // </React.StrictMode>,
  );
}
