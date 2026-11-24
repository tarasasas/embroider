import SwiftUI
import PhotosUI

/// Fabric presets: the stitch density and pull compensation that suit each fabric.
enum Fabric: String, CaseIterable, Identifiable {
    case woven = "Woven cotton"
    case knit = "T-shirt / knit"
    case denim = "Denim / canvas"
    case towel = "Towel / fleece"
    case cap = "Cap / thick"

    var id: String { rawValue }

    var rowSpacingMM: Double {
        switch self {
        case .woven, .denim: return 0.3
        case .knit: return 0.4
        case .towel: return 0.25
        case .cap: return 0.35
        }
    }
    var pullCompMM: Double {
        switch self {
        case .woven: return 0.15
        case .knit: return 0.25
        case .denim: return 0.1
        case .towel, .cap: return 0.2
        }
    }
    var tip: String {
        switch self {
        case .woven: return "Use medium tear-away stabilizer."
        case .knit: return "Use cut-away stabilizer. Knits stretch, so the stitching is kept light."
        case .denim: return "Use tear-away stabilizer and a 90/14 needle."
        case .towel: return "Put water-soluble topping on top so stitches don't sink into the pile."
        case .cap: return "Use firm tear-away backing. Keep designs under about 2 inches tall."
        }
    }
}

/// Hoop sewing fields, in millimetres.
enum Hoop: String, CaseIterable, Identifiable {
    case small = "2.5 x 2.5 in"
    case fourByFour = "4 x 4 in"
    case fiveBySeven = "5 x 7 in"
    case sixByTen = "6 x 10 in"
    case eightByEight = "8 x 8 in"

    var id: String { rawValue }

    var sizeMM: (w: Double, h: Double) {
        switch self {
        case .small: return (60, 60)
        case .fourByFour: return (100, 100)
        case .fiveBySeven: return (130, 180)
        case .sixByTen: return (160, 260)
        case .eightByEight: return (200, 200)
        }
    }
}

private let mmPerInch = 25.4

struct ContentView: View {
    @State private var pickerItem: PhotosPickerItem?
    @State private var image: UIImage?
    @State private var settings = Settings()
    @State private var hoop: Hoop = .fourByFour
    @State private var fabric: Fabric = .woven
    @State private var showAdvanced = false
    @State private var result: ConversionResult?
    @State private var preview: UIImage?
    @State private var fileURL: URL?
    @State private var busy = false
    @State private var errorText: String?

    init() {
        var s = Settings()
        s.widthMM = 90
        s.heightMM = 90
        _settings = State(initialValue: s)
    }

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
                        Text(String(format: "%.1f x %.1f in, %d stitches, %d colors, about %.0f min",
                                    result.widthMM / mmPerInch, result.heightMM / mmPerInch, result.stitchCount,
                                    result.pattern.threads.count, result.minutes))
                        ForEach(Array(result.pattern.threads.enumerated()), id: \.offset) { i, t in
                            HStack {
                                ColorPicker("Thread \(i + 1) color", selection: colorBinding(i), supportsOpacity: false)
                                    .labelsHidden()
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

                Section {
                    Picker("Hoop", selection: $hoop) {
                        ForEach(Hoop.allCases) { Text($0.rawValue).tag($0) }
                    }
                    inchSlider("Width", $settings.widthMM, maxMM: hoop.sizeMM.w)
                    inchSlider("Height", $settings.heightMM, maxMM: hoop.sizeMM.h)
                } header: {
                    Text("Size")
                } footer: {
                    Text("The design is scaled to fit inside this box and keeps its proportions. Stay a little under the hoop size.")
                }

                Section {
                    Picker("Fabric", selection: $fabric) {
                        ForEach(Fabric.allCases) { Text($0.rawValue).tag($0) }
                    }
                    Stepper("Thread colors: \(settings.colors)", value: $settings.colors, in: 1...15)
                    Toggle("Remove background", isOn: $settings.removeBackground)
                } header: {
                    Text("Basics")
                } footer: {
                    Text("\(fabric.tip) Density, stitch length and the other stitch settings are chosen for you from the fabric.")
                }

                Section {
                    DisclosureGroup("Advanced (optional)", isExpanded: $showAdvanced) {
                        Stepper("Detail cleanup: \(settings.cleanup)", value: $settings.cleanup, in: 0...3)
                        Toggle("Use Brother thread colors", isOn: $settings.brotherColors)
                        Toggle("Underlay", isOn: $settings.underlay)
                        mmSlider("Row spacing", $settings.rowSpacingMM, 0.2...0.6)
                        mmSlider("Stitch length", $settings.stitchLengthMM, 1.5...6)
                        mmSlider("Pull compensation", $settings.pullCompMM, 0...0.5)
                        VStack(alignment: .leading) {
                            Text("Fill angle: \(Int(settings.fillAngle)) deg")
                            Slider(value: $settings.fillAngle, in: 0...180)
                        }
                        Button("Reset to automatic settings") { applyAutomatic() }
                    }
                } footer: {
                    Text("Stitch settings are in millimetres, the unit thread and machines use. Smaller row spacing = denser stitching.")
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
                    applyAutomatic()
                    generate()
                } else {
                    errorText = "Could not load that photo."
                }
            }
        }
        .onChange(of: hoop) { newHoop in
            // keep the design a little smaller than the hoop
            settings.widthMM = min(settings.widthMM, newHoop.sizeMM.w - 10)
            settings.heightMM = min(settings.heightMM, newHoop.sizeMM.h - 10)
            if settings.widthMM < 10 { settings.widthMM = newHoop.sizeMM.w - 10 }
            if settings.heightMM < 10 { settings.heightMM = newHoop.sizeMM.h - 10 }
        }
        .onChange(of: fabric) { _ in applyAutomatic() }
    }

    /// Lets the user change a thread's color after conversion; the stitches stay the same.
    private func colorBinding(_ i: Int) -> Binding<Color> {
        Binding<Color>(
            get: {
                guard let t = result?.pattern.threads[safe: i] else { return .gray }
                return Color(red: Double(t.r) / 255, green: Double(t.g) / 255, blue: Double(t.b) / 255)
            },
            set: { newColor in
                var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
                UIColor(newColor).getRed(&r, green: &g, blue: &b, alpha: &a)
                guard result != nil, i < result!.pattern.threads.count else { return }
                let rgb = RGB(r: Int((r * 255).rounded()), g: Int((g * 255).rounded()), b: Int((b * 255).rounded()))
                result!.pattern.threads[i].r = rgb.r
                result!.pattern.threads[i].g = rgb.g
                result!.pattern.threads[i].b = rgb.b
                result!.pattern.threads[i].name = ThreadChart.threads[nearestBrother(rgb)].name
                refreshOutputs()
            }
        )
    }

    /// Redraws the preview and rewrites the .pes after a color change.
    private func refreshOutputs() {
        guard let pattern = result?.pattern else { return }
        preview = Digitizer.preview(pattern)
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("design.pes")
        try? PES.data(for: pattern).write(to: url)
        fileURL = url
    }

    /// Sets every stitch setting from the chosen fabric. These are good defaults for most designs.
    private func applyAutomatic() {
        settings.rowSpacingMM = fabric.rowSpacingMM
        settings.pullCompMM = fabric.pullCompMM
        settings.stitchLengthMM = 3.5
        settings.fillAngle = 45
        settings.underlay = true
        settings.cleanup = 1
        settings.brotherColors = true
        settings.trimMM = 3
    }

    private func inchSlider(_ title: String, _ mm: Binding<Double>, maxMM: Double) -> some View {
        let inches = Binding<Double>(get: { mm.wrappedValue / mmPerInch }, set: { mm.wrappedValue = $0 * mmPerInch })
        return VStack(alignment: .leading) {
            Text("\(title): \(String(format: "%.2f", inches.wrappedValue)) in")
            Slider(value: inches, in: 0.5...max(1, maxMM / mmPerInch), step: 0.05)
        }
    }

    private func mmSlider(_ title: String, _ value: Binding<Double>, _ range: ClosedRange<Double>) -> some View {
        VStack(alignment: .leading) {
            Text("\(title): \(String(format: "%.2f", value.wrappedValue)) mm")
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
