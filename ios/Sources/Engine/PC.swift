import UIKit

/// A finished design, either made on the PC (full engine) or on the phone (simpler fallback).
struct Design {
    var threads: [ThreadColor]
    var widthMM: Double
    var heightMM: Double
    var stitchCount: Int
    var minutes: Double
    var pes: Data
    var preview: UIImage
    var fromPC: Bool
    var pattern: EmbPattern?  // only for phone-made designs, so their colors can be edited locally
}

enum PCError: LocalizedError {
    case unreachable
    case wrongCode
    case rejected(String)

    var errorDescription: String? {
        switch self {
        case .unreachable: return "Could not reach the PC. Is it on, on the same Wi-Fi, and is the phone server running?"
        case .wrongCode: return "The PC rejected the pairing code."
        case .rejected(let message): return message
        }
    }
}

/// Talks to server.py on the PC (same Wi-Fi network).
struct PCClient {
    let host: String
    let token: String

    private func url(_ path: String) throws -> URL {
        var h = host.trimmingCharacters(in: .whitespacesAndNewlines)
        for prefix in ["http://", "https://"] where h.hasPrefix(prefix) { h.removeFirst(prefix.count) }
        while h.hasSuffix("/") { h.removeLast() }
        if h.isEmpty { throw PCError.unreachable }
        if !h.contains(":") { h += ":8793" }
        guard let u = URL(string: "http://\(h)\(path)") else { throw PCError.unreachable }
        return u
    }

    private func send(_ request: URLRequest) async throws -> (Data, Int) {
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            return (data, (response as? HTTPURLResponse)?.statusCode ?? 0)
        } catch {
            throw PCError.unreachable
        }
    }

    private func fail(_ data: Data, _ status: Int) -> PCError {
        if status == 401 { return .wrongCode }
        let message = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["error"] as? String
        return .rejected(message ?? "The PC returned an error (\(status)).")
    }

    /// Returns the PC's name if it is reachable and the code is right.
    func ping() async throws -> String {
        var request = URLRequest(url: try url("/ping"), timeoutInterval: 6)
        request.setValue(token.trimmingCharacters(in: .whitespaces), forHTTPHeaderField: "X-Token")
        let (data, status) = try await send(request)
        guard status == 200 else { throw fail(data, status) }
        let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        return (json?["name"] as? String) ?? "PC"
    }

    func convert(image: UIImage, settings: Settings, fabric: String, edge: String, outline: String,
                 recolor: [Int: RGB]) async throws -> Design {
        guard let png = Self.pngData(image) else { throw PCError.rejected("Could not prepare the image.") }
        var colors: [String: String] = [:]
        for (i, c) in recolor { colors[String(i)] = String(format: "#%02x%02x%02x", c.r, c.g, c.b) }
        let body: [String: Any] = [
            "image": png.base64EncodedString(),
            "width_mm": settings.widthMM, "height_mm": settings.heightMM,
            "colors": settings.colors, "remove_background": settings.removeBackground,
            "fabric": fabric, "edge": edge, "outline": outline, "recolor": colors,
        ]
        var request = URLRequest(url: try url("/convert"), timeoutInterval: 180)
        request.httpMethod = "POST"
        request.setValue(token.trimmingCharacters(in: .whitespaces), forHTTPHeaderField: "X-Token")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (data, status) = try await send(request)
        guard status == 200 else { throw fail(data, status) }
        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let pes = (json["pes"] as? String).flatMap({ Data(base64Encoded: $0) }),
              let preview = (json["preview"] as? String).flatMap({ Data(base64Encoded: $0) }).flatMap({ UIImage(data: $0) })
        else { throw PCError.rejected("The PC sent back something unexpected.") }

        let threads: [ThreadColor] = (json["threads"] as? [[String: Any]] ?? []).map {
            ThreadColor(r: $0["r"] as? Int ?? 0, g: $0["g"] as? Int ?? 0, b: $0["b"] as? Int ?? 0,
                        name: $0["name"] as? String ?? "", stitches: $0["stitches"] as? Int ?? 0)
        }
        return Design(threads: threads, widthMM: json["width_mm"] as? Double ?? 0,
                      heightMM: json["height_mm"] as? Double ?? 0, stitchCount: json["stitches"] as? Int ?? 0,
                      minutes: json["minutes"] as? Double ?? 0, pes: pes, preview: preview, fromPC: true,
                      pattern: nil)
    }

    /// Upright PNG (keeps transparency), at most 1600 px on the long side to keep uploads small.
    private static func pngData(_ image: UIImage) -> Data? {
        let longest = max(image.size.width, image.size.height)
        let scale = min(1, 1600 / max(longest, 1))
        let size = CGSize(width: max(1, (image.size.width * scale).rounded()),
                          height: max(1, (image.size.height * scale).rounded()))
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        return UIGraphicsImageRenderer(size: size, format: format).image { _ in
            image.draw(in: CGRect(origin: .zero, size: size))
        }.pngData()
    }
}
