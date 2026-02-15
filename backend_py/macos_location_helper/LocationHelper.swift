import Cocoa
import CoreLocation

struct ResultPayload: Codable {
    let ok: Bool
    let error: String?
    let latitude: Double?
    let longitude: Double?
    let accuracy_m: Double?
    let altitude_m: Double?
    let timestamp_ms: Int64?
    let address: [String: String?]?
    let method: String?
}

final class LocationDelegate: NSObject, CLLocationManagerDelegate {
    var lastLocation: CLLocation?
    var lastError: Error?
    var authStatus: CLAuthorizationStatus?

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        if let loc = locations.last {
            lastLocation = loc
        }
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        lastError = error
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        authStatus = manager.authorizationStatus
    }

    // macOS 10.x compatibility
    func locationManager(_ manager: CLLocationManager, didChangeAuthorization status: CLAuthorizationStatus) {
        authStatus = status
    }
}

func nowMs() -> Int64 {
    return Int64(Date().timeIntervalSince1970 * 1000)
}

func encodeJSON(_ obj: ResultPayload) -> String {
    let enc = JSONEncoder()
    enc.outputFormatting = [.prettyPrinted, .withoutEscapingSlashes]
    if let data = try? enc.encode(obj), let s = String(data: data, encoding: .utf8) {
        return s
    }
    return "{\"ok\":false,\"error\":\"failed_to_encode_json\"}"
}

func emitResult(_ obj: ResultPayload, outPath: String?) {
    let s = encodeJSON(obj)
    print(s)

    guard let p = outPath, !p.isEmpty else { return }
    do {
        try s.write(toFile: p, atomically: true, encoding: .utf8)
    } catch {
        // best-effort
    }
}

func usageAndExit() -> Never {
    let payload = ResultPayload(
        ok: false,
        error: "usage: VoiceAssistantLocationHelper --timeoutSec <seconds> --outPath <path>",
        latitude: nil,
        longitude: nil,
        accuracy_m: nil,
        altitude_m: nil,
        timestamp_ms: nowMs(),
        address: nil,
        method: "macOS CoreLocation helper"
    )
    emitResult(payload, outPath: nil)
    exit(2)
}

let args = CommandLine.arguments
var timeoutSec: Double = 12
var outPath: String? = nil
if let idx = args.firstIndex(of: "--timeoutSec") {
    if idx + 1 >= args.count { usageAndExit() }
    timeoutSec = Double(args[idx + 1]) ?? 12
}
if let idx = args.firstIndex(of: "--outPath") {
    if idx + 1 >= args.count { usageAndExit() }
    outPath = String(args[idx + 1])
}
if timeoutSec < 3 { timeoutSec = 3 }
if timeoutSec > 30 { timeoutSec = 30 }

// Ensure app is active so permission prompts can show.
let app = NSApplication.shared
app.setActivationPolicy(.regular)
app.activate(ignoringOtherApps: true)

if !CLLocationManager.locationServicesEnabled() {
    let payload = ResultPayload(
        ok: false,
        error: "location_services_disabled",
        latitude: nil,
        longitude: nil,
        accuracy_m: nil,
        altitude_m: nil,
        timestamp_ms: nowMs(),
        address: nil,
        method: "macOS CoreLocation helper"
    )
    emitResult(payload, outPath: outPath)
    exit(1)
}

let manager = CLLocationManager()
let delegate = LocationDelegate()
manager.delegate = delegate

// Request authorization (requires NSLocationWhenInUseUsageDescription in Info.plist)
manager.requestWhenInUseAuthorization()

let deadline = Date().addingTimeInterval(timeoutSec)
let geocoder = CLGeocoder()
var startedUpdating = false

while Date() < deadline {
    RunLoop.current.run(until: Date().addingTimeInterval(0.05))

    let status = delegate.authStatus ?? manager.authorizationStatus
    if status == .denied || status == .restricted {
        let payload = ResultPayload(
            ok: false,
            error: "authorization_denied_or_restricted",
            latitude: nil,
            longitude: nil,
            accuracy_m: nil,
            altitude_m: nil,
            timestamp_ms: nowMs(),
            address: ["authorizationStatus": String(describing: status.rawValue)],
            method: "macOS CoreLocation helper"
        )
        emitResult(payload, outPath: outPath)
        exit(1)
    }

    // macOS 上 `.authorizedWhenInUse` 标记为 unavailable；用 rawValue 兼容判断。
    // 参考：0=notDetermined, 1=restricted, 2=denied, 3=authorizedAlways, 4=authorizedWhenInUse(iOS)
    if (status.rawValue == 3 || status.rawValue == 4) && !startedUpdating {
        startedUpdating = true
        manager.startUpdatingLocation()
    }

    if let err = delegate.lastError {
        // Map common error codes for easier diagnosis.
        let errStr = String(describing: err)
        let payload = ResultPayload(
            ok: false,
            error: "location_failed: \(errStr)",
            latitude: nil,
            longitude: nil,
            accuracy_m: nil,
            altitude_m: nil,
            timestamp_ms: nowMs(),
            address: ["authorizationStatus": String(describing: status.rawValue)],
            method: "macOS CoreLocation helper"
        )
        emitResult(payload, outPath: outPath)
        exit(1)
    }

    if let loc = delegate.lastLocation {
        manager.stopUpdatingLocation()

        // Reverse geocode best-effort.
        var addr: [String: String?] = [:]
        let sema = DispatchSemaphore(value: 0)
        geocoder.reverseGeocodeLocation(loc) { placemarks, error in
            if let p = placemarks?.first {
                addr["name"] = p.name
                addr["thoroughfare"] = p.thoroughfare
                addr["subThoroughfare"] = p.subThoroughfare
                addr["subLocality"] = p.subLocality
                addr["locality"] = p.locality
                addr["subAdministrativeArea"] = p.subAdministrativeArea
                addr["administrativeArea"] = p.administrativeArea
                addr["postalCode"] = p.postalCode
                addr["country"] = p.country
                addr["isoCountryCode"] = p.isoCountryCode
            } else if let e = error {
                addr["geocodeError"] = String(describing: e)
            }
            sema.signal()
        }

        _ = sema.wait(timeout: .now() + 3)

        let payload = ResultPayload(
            ok: true,
            error: nil,
            latitude: loc.coordinate.latitude,
            longitude: loc.coordinate.longitude,
            accuracy_m: loc.horizontalAccuracy,
            altitude_m: loc.altitude,
            timestamp_ms: nowMs(),
            address: addr,
            method: "macOS CoreLocation helper"
        )
        emitResult(payload, outPath: outPath)
        exit(0)
    }
}

manager.stopUpdatingLocation()

let status = delegate.authStatus ?? manager.authorizationStatus
let payload = ResultPayload(
    ok: false,
    error: "authorization_or_location_timeout",
    latitude: nil,
    longitude: nil,
    accuracy_m: nil,
    altitude_m: nil,
    timestamp_ms: nowMs(),
    address: ["authorizationStatus": String(describing: status.rawValue)],
    method: "macOS CoreLocation helper"
)
emitResult(payload, outPath: outPath)
exit(1)
