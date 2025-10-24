import { Clock, Cpu, HardDrive, Monitor } from "lucide-react";
import { formatFileSize, formatUptime } from "../utils/helpers";

interface SystemInfoDisplayProps {
  systemInfo: {
    platform: string;
    arch: string;
    nodeVersion: string;
    homeDir: string;
    freeMemory: number;
    totalMemory: number;
    uptime: number;
    loadavg: number[];
  } | null;
}

export function SystemInfoDisplay({ systemInfo }: SystemInfoDisplayProps) {
  if (!systemInfo) {
    return (
      <div className="bg-gray-50 border border-gray-200 rounded-lg p-4">
        <div className="flex items-center gap-2 text-gray-500">
          <Monitor size={20} />
          <span>系统信息加载中...</span>
        </div>
      </div>
    );
  }

  const memoryUsage =
    ((systemInfo.totalMemory - systemInfo.freeMemory) / systemInfo.totalMemory) * 100;

  return (
    <div className="bg-gray-50 border border-gray-200 rounded-lg p-4">
      <h3 className="text-lg font-semibold text-gray-900 mb-4 flex items-center gap-2">
        <Monitor size={20} />
        系统信息
      </h3>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* 基本信息 */}
        <div className="space-y-3">
          <div>
            <div className="text-sm font-medium text-gray-700">操作系统</div>
            <div className="text-sm text-gray-600">
              {systemInfo.platform} ({systemInfo.arch})
            </div>
          </div>

          <div>
            <div className="text-sm font-medium text-gray-700">运行时间</div>
            <div className="text-sm text-gray-600 flex items-center gap-1">
              <Clock size={14} />
              {formatUptime(systemInfo.uptime)}
            </div>
          </div>

          {systemInfo.loadavg && systemInfo.loadavg.length > 0 && (
            <div>
              <div className="text-sm font-medium text-gray-700 flex items-center gap-1">
                <Cpu size={14} />
                系统负载
              </div>
              <div className="text-sm text-gray-600">
                {systemInfo.loadavg.map((load, index) => (
                  <span key={`load-${index}`} className="mr-2">
                    {load.toFixed(2)}
                    {index === 0 && " (1min)"}
                    {index === 1 && " (5min)"}
                    {index === 2 && " (15min)"}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* 内存信息 */}
        <div className="space-y-3">
          <div>
            <div className="text-sm font-medium text-gray-700 flex items-center gap-1">
              <HardDrive size={14} />
              内存使用情况
            </div>
            <div className="mt-2">
              <div className="flex justify-between text-sm text-gray-600 mb-1">
                <span>
                  已使用: {formatFileSize(systemInfo.totalMemory - systemInfo.freeMemory)}
                </span>
                <span>总计: {formatFileSize(systemInfo.totalMemory)}</span>
              </div>
              <div className="w-full bg-gray-200 rounded-full h-2">
                <div
                  className="bg-primary-600 h-2 rounded-full transition-all duration-300"
                  style={{ width: `${memoryUsage}%` }}
                ></div>
              </div>
              <div className="text-xs text-gray-500 mt-1">使用率: {memoryUsage.toFixed(1)}%</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
