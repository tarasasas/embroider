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

struct ReceivedPES: Decodable {
    let name: String
    let pes: Data

    func save() throws -> URL {
        let filename = name.replacingOccurrences(of: "\\", with: "/").split(separator: "/").last.map(String.init)
        guard let filename, filename.lowercased().hasSuffix(".pes"), filename.count > 4,
              pes.starts(with: "#PES".utf8) else {
            throw PCError.rejected("The PC sent back an invalid PES.")
        }
        let documents = try FileManager.default.url(for: .documentDirectory, in: .userDomainMask,
                                                    appropriateFor: nil, create: true)
        let folder = documents.appendingPathComponent("PC Transfers", isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let url = folder.appendingPathComponent(filename)
        try pes.write(to: url, options: .atomic)
        return url
    }
}

enum PCError: LocalizedError {
    case unreachable
    case wrongCode
    case needsLayoutServer
    case needsStitchServer
    case rejected(String)

    var errorDescription: String? {
        switch self {
        case .unreachable: return "Could not reach the PC. Is it on, on the same Wi-Fi, and is the phone server running?"
        case .wrongCode: return "The PC rejected the pairing code."
        case .needsLayoutServer: return "Update and restart the PC server to preserve hoop placement."
        case .needsStitchServer: return "Update and restart the PC server to use this stitch style."
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

    func receivePES() async throws -> ReceivedPES {
        var request = URLRequest(url: try url("/pes/latest"), timeoutInterval: 15)
        request.setValue(token.trimmingCharacters(in: .whitespaces), forHTTPHeaderField: "X-Token")
        let (data, status) = try await send(request)
        if status == 404, case .rejected(let message) = fail(data, status), message == "Not found." {
            throw PCError.rejected("Update and restart the PC server to receive PES files.")
        }
        guard status == 200 else { throw fail(data, status) }
        guard let file = try? JSONDecoder().decode(ReceivedPES.self, from: data) else {
            throw PCError.rejected("The PC sent back an invalid PES.")
        }
        return file
    }

    func convert(image: UIImage, settings: Settings, fabric: String, edge: String, outline: String,
                 recolor: [Int: RGB]) async throws -> Design {
        guard let png = Self.pngData(image) else { throw PCError.rejected("Could not prepare the image.") }
        var colors: [String: String] = [:]
        for (i, c) in recolor { colors[String(i)] = String(format: "#%02x%02x%02x", c.r, c.g, c.b) }
        var body: [String: Any] = [
            "image": png.base64EncodedString(),
            "width_mm": settings.widthMM, "height_mm": settings.heightMM,
            "colors": settings.colors, "remove_background": settings.removeBackground, "bold_mm": settings.boldMM,
            "fabric": fabric, "edge": edge, "outline": outline, "recolor": colors,
        ]
        if let style = settings.letteringStyle {
            body["fill_type"] = style.fill
            body["edge"] = style.edge ?? edge
            body["applique_all"] = style == .applique
        } else {
            body["fill_type"] = settings.photoFill.rawValue
        }
        if settings.fixedHoop { body["fixed_hoop"] = true }
        if settings.preserveTransparency { body["preserve_transparency"] = true }
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
        if settings.fixedHoop, json["fixed_hoop"] as? Bool != true {
            throw PCError.needsLayoutServer
        }
        if let style = settings.letteringStyle, style.usesShortFillStitches,
           json["fill_type"] as? String != style.fill {
            throw PCError.needsStitchServer
        }
        if settings.letteringStyle == nil,
           (json["planned_fill"] as? Bool != true || json["fill_type"] as? String != settings.photoFill.rawValue) {
            throw PCError.needsStitchServer
        }
        if settings.letteringStyle?.requiresPC == true, json["lettering_styles"] as? Bool != true {
            throw PCError.rejected("Update and restart the PC server to use this stitch style.")
        }

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
