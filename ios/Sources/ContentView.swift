import SwiftUI
import PhotosUI

struct ContentView: View {
    @State private var pickerItem: PhotosPickerItem?
    @State private var image: UIImage?
    @State private var settings = Settings()
    @State private var result: ConversionResult?
    @State private var preview: UIImage?
    @State private var fileURL: URL?
    @State private var busy = false
    @State private var errorText: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    PhotosPicker(selection: $pickerItem, matching: .images) {
                        Label(image == nil ? "Choose image" : "Choose a different image", systemImage: "photo")
                    }
                }

                if let preview {
                    Section("Stitch preview") {
                        Image(uiImage: preview)
                            .resizable()
                            .scaledToFit()
                            .frame(maxWidth: .infinity)
                    }
                }

                if let result {
                    Section("Design") {
                        Text(String(format: "%.0f x %.0f mm, %d stitches, %d colors, about %.0f min",
                                    result.widthMM, result.heightMM, result.stitchCount,
                                    result.pattern.threads.count, result.minutes))
                        ForEach(Array(result.pattern.threads.enumerated()), id: \.offset) { i, t in
                            HStack {
                                Circle()
                                    .fill(Color(red: Double(t.r) / 255, green: Double(t.g) / 255, blue: Double(t.b) / 255))
                                    .frame(width: 22, height: 22)
                                    .overlay(Circle().stroke(Color.gray.opacity(0.4)))
                                Text("\(i + 1). \(t.name)")
                                Spacer()
                                Text("\(t.stitches) st").foregroundStyle(.secondary)
                            }
                        }
                        if let fileURL {
                            ShareLink(item: fileURL) {
                                Label("Save / share .pes file", systemImage: "square.and.arrow.up")
                            }
                        }
                    }
                }

                if let errorText {
                    Section { Text(errorText).foregroundStyle(.red) }
                }

                Section("Size") {
                    slider("Max width", $settings.widthMM, 10...200, "mm")
                    slider("Max height", $settings.heightMM, 10...200, "mm")
                }
                Section("Colors") {
                    Stepper("Thread colors: \(settings.colors)", value: $settings.colors, in: 1...15)
                    Stepper("Detail cleanup: \(settings.cleanup)", value: $settings.cleanup, in: 0...3)
                    Toggle("Remove background", isOn: $settings.removeBackground)
                    Toggle("Use Brother thread colors", isOn: $settings.brotherColors)
                }
                Section("Stitching") {
                    slider("Row spacing", $settings.rowSpacingMM, 0.2...0.6, "mm")
                    slider("Stitch length", $settings.stitchLengthMM, 1.5...6, "mm")
                    slider("Fill angle", $settings.fillAngle, 0...180, "deg")
                    slider("Pull compensation", $settings.pullCompMM, 0...0.5, "mm")
                    Toggle("Underlay", isOn: $settings.underlay)
                }

                Section {
                    Button {
                        generate()
                    } label: {
                        HStack {
                            Text("Generate stitches")
                            if busy {
                                Spacer()
                                ProgressView()
                            }
                        }
                    }
                    .disabled(image == nil || busy)
                }
            }
            .navigationTitle("Embroidery")
        }
        .onChange(of: pickerItem) { item in
            guard let item else { return }
            Task {
                if let data = try? await item.loadTransferable(type: Data.self), let img = UIImage(data: data) {
                    image = img
                    generate()
                } else {
                    errorText = "Could not load that photo."
                }
            }
        }
    }

    private func slider(_ title: String, _ value: Binding<Double>, _ range: ClosedRange<Double>, _ unit: String) -> some View {
        VStack(alignment: .leading) {
            Text("\(title): \(String(format: "%.2f", value.wrappedValue)) \(unit)")
            Slider(value: value, in: range)
        }
    }

    private func generate() {
        guard let img = image, !busy else { return }
        let s = settings
        busy = true
        errorText = nil
        Task {
            do {
                let out = try await Task.detached(priority: .userInitiated) { () -> (ConversionResult, UIImage, Data) in
                    let r = try Digitizer.convert(img, s)
                    return (r, Digitizer.preview(r.pattern), PES.data(for: r.pattern))
                }.value
                let url = FileManager.default.temporaryDirectory.appendingPathComponent("design.pes")
                try out.2.write(to: url)
                result = out.0
                preview = out.1
                fileURL = url
            } catch {
                errorText = error.localizedDescription
            }
            busy = false
        }
    }
}
