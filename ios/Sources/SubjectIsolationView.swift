import CoreImage
import SwiftUI
import Vision

@available(iOS 17, *)
struct SubjectIsolationView: View {
    let original: UIImage
    let useSubject: (UIImage) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var cutout: UIImage?
    @State private var showOriginal = false
    @State private var error: String?

    var body: some View {
        NavigationStack {
            VStack(spacing: 16) {
                if cutout == nil && error == nil {
                    ProgressView("Isolating subject…")
                }
                Image(uiImage: showOriginal ? original : (cutout ?? original))
                    .resizable()
                    .scaledToFit()
                    .frame(maxWidth: .infinity, maxHeight: 450)
                    .background(Color.secondary.opacity(0.15))
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                    .accessibilityLabel(showOriginal || cutout == nil ? "Original photo" : "Isolated subject")
                if cutout != nil {
                    Toggle("Show original", isOn: $showOriginal)
                }
                if let error {
                    Text(error).foregroundStyle(.secondary)
                }
            }
            .padding()
            .navigationTitle("Isolate subject")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Use subject") {
                        guard let cutout else { return }
                        useSubject(cutout)
                        dismiss()
                    }
                    .disabled(cutout == nil)
                }
            }
            .task {
                guard cutout == nil, error == nil else { return }
                do {
                    let result = try await Task.detached(priority: .userInitiated) {
                        try Self.isolate(original)
                    }.value
                    if !Task.isCancelled { cutout = result }
                } catch {
                    if !Task.isCancelled {
                        self.error = "No clear subject could be isolated. Try a closer crop of the photo."
                    }
                }
            }
        }
    }

    private enum IsolationError: Error { case noSubject }

    nonisolated private static func isolate(_ image: UIImage) throws -> UIImage {
        // Normalize orientation and bound the work done on full-resolution camera photos.
        let longest = max(image.size.width, image.size.height)
        guard longest > 0 else { throw IsolationError.noSubject }
        let scale = min(1, 1800 / longest)
        let size = CGSize(width: max(1, image.size.width * scale), height: max(1, image.size.height * scale))
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        let upright = UIGraphicsImageRenderer(size: size, format: format).image { _ in
            image.draw(in: CGRect(origin: .zero, size: size))
        }
        guard let cgImage = upright.cgImage else { throw IsolationError.noSubject }
        let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
        let request = VNGenerateForegroundInstanceMaskRequest()
        try handler.perform([request])
        guard let mask = request.results?.first, !mask.allInstances.isEmpty else {
            throw IsolationError.noSubject
        }
        let buffer = try mask.generateMaskedImage(ofInstances: mask.allInstances, from: handler,
                                                  croppedToInstancesExtent: true)
        let cutout = CIImage(cvPixelBuffer: buffer)
        guard let output = CIContext().createCGImage(cutout, from: cutout.extent) else {
            throw IsolationError.noSubject
        }
        return UIImage(cgImage: output)
    }
}

