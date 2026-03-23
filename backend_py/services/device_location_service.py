#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""macOS 设备定位（CoreLocation）。

该模块仅在 macOS 下可用，依赖 PyObjC 的 `pyobjc-framework-CoreLocation`。

设计目标：
- 尽量获取街区级精度的经纬度 + 精度（米）
- 尽量返回可读的地址信息（街道/区/市/省），便于对用户输出
- 超时/未授权时返回明确错误信息

注意：CoreLocation 回调需要在主线程的 RunLoop 上运行；因此这里采用“阻塞等待 + RunLoop pump”。
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


try:
    import objc  # type: ignore
    from Foundation import NSObject  # type: ignore
except Exception:  # pragma: no cover
    objc = None
    NSObject = object  # type: ignore


@dataclass
class DeviceLocationResult:
    ok: bool
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class LocationManagerDelegate(NSObject):
    """CLLocationManager delegate。

    注意：该类必须在模块级定义（不能放在方法内部动态定义），否则 PyObjC 会在多次调用时
    尝试重复注册同名 Objective-C 类，触发 "overriding existing Objective-C class" 异常。
    """

    def init(self):  # noqa: N802
        if objc is None:
            return None
        self = objc.super(LocationManagerDelegate, self).init()
        if self is None:
            return None
        self.last_location = None
        self.last_error = None
        return self

    def locationManager_didUpdateLocations_(self, _manager, locations):  # noqa: N802
        try:
            if locations and len(locations) > 0:
                self.last_location = locations[-1]
        except Exception as e:
            self.last_error = e

    def locationManager_didFailWithError_(self, _manager, error):  # noqa: N802
        self.last_error = error


class DeviceLocationService:
    """设备定位服务（macOS CoreLocation）。

    说明：
    - 直接在 Python 进程里调用 CoreLocation 在部分环境下会出现授权弹窗不出现、状态长期停留在
      NotDetermined(0) 的情况。
    - 为了稳定触发系统授权与 TCC 记录，这里优先使用带 Info.plist 的 macOS Helper App 获取定位。
    """

    DEFAULT_TIMEOUT_SEC = 12

    def __init__(self) -> None:
        self._is_available = True

        try:
            import objc  # noqa: F401
            from CoreLocation import CLLocationManager  # noqa: F401
        except Exception:
            self._is_available = False

    def is_available(self) -> bool:
        return bool(self._is_available)

    @staticmethod
    def _repo_root() -> Path:
        # backend_py/services -> backend_py -> repo root
        return Path(__file__).resolve().parents[2]

    def _helper_app_dir(self) -> Path:
        return self._repo_root() / "backend_py" / "macos_location_helper" / "VoiceAssistantLocationHelper.app"

    def _helper_bin_path(self) -> Path:
        return self._helper_app_dir() / "Contents" / "MacOS" / "VoiceAssistantLocationHelper"

    def _build_helper_if_needed(self) -> None:
        app_dir = self._helper_app_dir()
        bin_path = self._helper_bin_path()
        if bin_path.exists():
            return

        build_sh = self._repo_root() / "backend_py" / "macos_location_helper" / "build.sh"
        if not build_sh.exists():
            raise RuntimeError(f"定位 Helper 构建脚本不存在：{build_sh}")

        # 使用 bash 构建，避免依赖外部 Python 包。
        proc = subprocess.run(
            ["bash", str(build_sh)],
            cwd=str(build_sh.parent),
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0 or (not bin_path.exists()):
            stderr_tail = (proc.stderr or "").strip()[-800:]
            stdout_tail = (proc.stdout or "").strip()[-800:]
            raise RuntimeError(f"构建定位 Helper 失败（code={proc.returncode}）：stdout={stdout_tail} stderr={stderr_tail}")

        _ = app_dir

    def _run_helper(self, *, timeout_sec: int) -> DeviceLocationResult:
        """运行 macOS 定位 Helper，并读取其 JSON 输出。

        注意：必须通过 `open -W <.app>` 启动，确保系统按 App（bundle id）维度触发定位授权弹窗。
        直接执行 `.app/Contents/MacOS/*` 在部分机器上会被 TCC 归类为命令行进程，从而导致授权失败。
        """

        self._build_helper_if_needed()

        app_dir = self._helper_app_dir()
        if not app_dir.exists():
            return DeviceLocationResult(ok=False, error="定位 Helper 不存在或未构建")

        out_path = Path("/tmp") / f"voiceassistant_location_{os.getpid()}_{int(time.time() * 1000)}.json"
        try:
            if out_path.exists():
                out_path.unlink()
        except Exception:
            pass

        cmd = [
            "open",
            "-n",
            "-W",
            str(app_dir),
            "--args",
            "--timeoutSec",
            str(int(timeout_sec)),
            "--outPath",
            str(out_path),
        ]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=float(max(5, min(90, timeout_sec + 20))),
                check=False,
            )
        except subprocess.TimeoutExpired:
            return DeviceLocationResult(ok=False, error="定位 Helper 启动/等待超时")

        if not out_path.exists():
            stderr_tail = (proc.stderr or "").strip()[-800:]
            stdout_tail = (proc.stdout or "").strip()[-800:]
            return DeviceLocationResult(
                ok=False,
                error=(
                    "定位 Helper 未产出结果文件。"
                    f"openExitCode={proc.returncode} stdoutTail={stdout_tail} stderrTail={stderr_tail}"
                ),
            )

        try:
            out = out_path.read_text(encoding="utf-8").strip()
        except Exception as e:
            return DeviceLocationResult(ok=False, error=f"读取定位 Helper 输出失败：{e}")

        if not out:
            return DeviceLocationResult(ok=False, error="定位 Helper 输出为空")

        try:
            obj = json.loads(out)
        except Exception:
            return DeviceLocationResult(ok=False, error=f"定位 Helper 输出无法解析为 JSON：{out[-800:]}")

        if not isinstance(obj, dict):
            return DeviceLocationResult(ok=False, error="定位 Helper 输出格式异常（非对象）")

        if obj.get("ok") is True and obj.get("longitude") is not None and obj.get("latitude") is not None:
            lon = float(obj.get("longitude"))
            lat = float(obj.get("latitude"))
            result = {
                "latitude": lat,
                "longitude": lon,
                "lon_lat": f"{lon},{lat}",
                "accuracy_m": obj.get("accuracy_m"),
                "altitude_m": obj.get("altitude_m"),
                "address": obj.get("address"),
                "timestamp_ms": int(obj.get("timestamp_ms") or int(time.time() * 1000)),
                "method": str(obj.get("method") or "macOS CoreLocation helper"),
            }
            return DeviceLocationResult(ok=True, result=result)

        err = str(obj.get("error") or "定位 Helper 获取位置失败")
        return DeviceLocationResult(ok=False, error=err)

    def get_current_location(self, *, timeout_sec: int = DEFAULT_TIMEOUT_SEC) -> DeviceLocationResult:
        """获取当前位置。

        Returns:
            DeviceLocationResult: 成功时包含经纬度、精度与地址信息。

        Notes:
            该方法是同步阻塞调用。建议在 asyncio 环境中通过 run_in_executor 调用。
        """

        # 优先走 macOS Helper App（更稳定的授权/TCC 行为）。
        try:
            return self._run_helper(timeout_sec=int(timeout_sec))
        except Exception as e:
            helper_err = str(e)

        # Helper 失败时，回退到 Python 直连 CoreLocation（可能在部分机器上仍可用）。
        if not self._is_available:
            return DeviceLocationResult(ok=False, error=f"CoreLocation 不可用：{helper_err}")

        try:
            import objc
            from CoreLocation import (
                CLGeocoder,
                CLLocationManager,
                kCLAuthorizationStatusAuthorizedAlways,
                kCLAuthorizationStatusAuthorizedWhenInUse,
                kCLAuthorizationStatusDenied,
                kCLAuthorizationStatusNotDetermined,
                kCLAuthorizationStatusRestricted,
            )
            from Foundation import NSRunLoop, NSDate
        except Exception as e:
            return DeviceLocationResult(ok=False, error=f"导入 CoreLocation 失败：{e}（helperErr={helper_err}）")

        if timeout_sec <= 0:
            timeout_sec = self.DEFAULT_TIMEOUT_SEC

        # 关键：系统定位的授权弹窗与回调通常依赖主线程 RunLoop。
        # 若在非主线程调用，可能出现授权状态长期不刷新，最终只能超时。
        if threading.current_thread() is not threading.main_thread():
            return DeviceLocationResult(
                ok=False,
                error=(
                    "CoreLocation 必须在主线程执行（当前线程非主线程），否则授权状态可能无法刷新。"
                    "请重启后端并确保主进程在主线程运行。"
                ),
            )

        try:
            if not bool(CLLocationManager.locationServicesEnabled()):
                return DeviceLocationResult(ok=False, error="macOS 系统定位服务未开启（locationServicesEnabled=false）")
        except Exception:
            # 某些系统版本/桥接环境不支持该 API，忽略。
            pass

        loc_manager = CLLocationManager.alloc().init()
        delegate = LocationManagerDelegate.alloc().init()
        loc_manager.setDelegate_(delegate)

        # 请求权限（首次会触发系统弹窗）
        try:
            loc_manager.requestWhenInUseAuthorization()
        except Exception:
            # 某些系统版本该方法可用性不同，但不应直接失败
            pass

        def _pump_runloop_until(deadline: float) -> None:
            runloop = NSRunLoop.currentRunLoop()
            while time.time() < deadline:
                runloop.runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.05))

        # 等待授权状态
        deadline = time.time() + min(timeout_sec, 15)
        authorized = False
        last_status = None
        status_samples = []
        while time.time() < deadline:
            try:
                # 兼容不同系统版本：优先类方法，其次实例方法。
                try:
                    last_status = CLLocationManager.authorizationStatus()
                except Exception:
                    last_status = loc_manager.authorizationStatus()
            except Exception:
                last_status = None

            if last_status is not None:
                status_samples.append(int(last_status))

            if last_status in {kCLAuthorizationStatusAuthorizedWhenInUse, kCLAuthorizationStatusAuthorizedAlways}:
                authorized = True
                break
            if last_status in {kCLAuthorizationStatusDenied, kCLAuthorizationStatusRestricted}:
                return DeviceLocationResult(ok=False, error="定位权限被拒绝或受限，请在系统设置中允许定位")

            # 未决定：继续等待用户点授权
            if last_status == kCLAuthorizationStatusNotDetermined:
                _pump_runloop_until(time.time() + 0.2)
                continue

            _pump_runloop_until(time.time() + 0.2)

        if not authorized:
            sample_tail = status_samples[-10:] if status_samples else []
            return DeviceLocationResult(
                ok=False,
                error=f"定位权限未授予或等待超时（statusSamplesTail={sample_tail}）",
            )

        # 开始定位
        try:
            loc_manager.startUpdatingLocation()
        except Exception as e:
            return DeviceLocationResult(ok=False, error=f"启动定位失败：{e}")

        # 等待位置结果
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            if delegate.last_location is not None:
                break
            if delegate.last_error is not None:
                break
            _pump_runloop_until(time.time() + 0.2)

        try:
            loc_manager.stopUpdatingLocation()
        except Exception:
            pass

        if delegate.last_error is not None:
            return DeviceLocationResult(ok=False, error=f"定位失败：{delegate.last_error}")

        if delegate.last_location is None:
            return DeviceLocationResult(ok=False, error="未获取到 macOS 系统定位数据")

        location = delegate.last_location
        coord = location.coordinate()
        latitude = float(coord.latitude)
        longitude = float(coord.longitude)
        accuracy_m = float(location.horizontalAccuracy())
        altitude_m = float(location.altitude())

        # 反地理编码（尽量给出街道/区/市信息）
        geocoder = CLGeocoder.alloc().init()
        geo_holder: Dict[str, Any] = {"placemark": None, "error": None}

        def _geocode_handler(placemarks, error):
            geo_holder["error"] = error
            try:
                if placemarks and len(placemarks) > 0:
                    geo_holder["placemark"] = placemarks[0]
            except Exception:
                geo_holder["placemark"] = None

        try:
            geocoder.reverseGeocodeLocation_completionHandler_(location, _geocode_handler)
            _pump_runloop_until(time.time() + min(3.0, timeout_sec))
        except Exception:
            # geocoder 非关键，忽略失败
            pass

        address: Dict[str, Any] = {}
        placemark = geo_holder.get("placemark")
        if placemark is not None:
            try:
                # CoreLocation/CLPlacemark 常见字段
                address = {
                    "name": str(getattr(placemark, "name", None)() if callable(getattr(placemark, "name", None)) else getattr(placemark, "name", None) or "")
                    or None,
                    "thoroughfare": str(getattr(placemark, "thoroughfare", None) or "") or None,
                    "subThoroughfare": str(getattr(placemark, "subThoroughfare", None) or "") or None,
                    "subLocality": str(getattr(placemark, "subLocality", None) or "") or None,
                    "locality": str(getattr(placemark, "locality", None) or "") or None,
                    "subAdministrativeArea": str(getattr(placemark, "subAdministrativeArea", None) or "") or None,
                    "administrativeArea": str(getattr(placemark, "administrativeArea", None) or "") or None,
                    "postalCode": str(getattr(placemark, "postalCode", None) or "") or None,
                    "country": str(getattr(placemark, "country", None) or "") or None,
                    "isoCountryCode": str(getattr(placemark, "ISOcountryCode", None) or "") or None,
                }
            except Exception:
                address = {}

        out = {
            "latitude": latitude,
            "longitude": longitude,
            "lon_lat": f"{longitude},{latitude}",
            "accuracy_m": accuracy_m,
            "altitude_m": altitude_m,
            "address": address,
            "timestamp_ms": int(time.time() * 1000),
            "method": "macOS CoreLocation",
        }
        return DeviceLocationResult(ok=True, result=out)
