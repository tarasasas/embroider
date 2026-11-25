import SwiftUI
import PhotosUI

/// Fabric presets: the stitch density and pull compensation that suit each fabric.
/// (The names must match the PC app's, which receives the fabric name.)
enum Fabric: String, CaseIterable, Identifiable {
    case woven = "Woven cotton"
    case knit = "T-shirt / knit"
    case denim = "Denim / canvas"
    case towel = "Towel / fleece"
    case cap = "Cap / thick"

    var id: String { rawValue }

    var rowSpacingMM: Double {
        switch self {
        case .woven, .denim, .knit, .cap: return 0.20
        case .towel: return 0.18
        }
    }
    var pullCompMM: Double {
        switch self {
        case .woven: return 0.15
        case .knit: return 0.45
        case .denim: return 0.1
        case .towel, .cap: return 0.2
        }
    }
    var tip: String {
        switch self {
        case .woven: return "Use medium tear-away stabilizer."
        case .knit: return "Use cut-away stabilizer, so the knit can't stretch while it is sewn."
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
private let lineTypes = ["none", "running", "triple", "satin"]

struct ContentView: View {
    @AppStorage("pcHost") private var pcHost = ""
    @AppStorage("pcCode") private var pcCode = ""

    @State private var pickerItem: PhotosPickerItem?
    @State private var image: UIImage?
    @State private var photoOriginal: UIImage?
    @State private var showSubjectIsolation = false
    @State private var settings = Settings()
    @State private var hoop: Hoop = .fourByFour
    @State private var fabric: Fabric = .knit
    @State private var edge = "none"
    @State private var outline = "none"
    @State private var showAdvanced = false
    @State private var showPC = false
    @State private var showLettering = false
    @State private var showMotifs = false
    @State private var showLayout = false
    @State private var layoutItems: [HoopLayoutItem] = []
    @State private var layoutStyle = LetteringStitchStyle.smooth
    @State private var letteringDraft = LetteringDraft()
    @State private var motifDraft = LetteringDraft(stitch: .auto, motif: "Star")
    @State private var photoRemoveBackground: Bool?
    @State private var artworkColorCount = 0
    @State private var lockRatio = true   // keep width x height in the image's proportions
    @State private var aspect = 1.0       // image width / height

    @State private var design: Design?
    @State private var fileURL: URL?
    @State private var recolor: [Int: RGB] = [:]
    @State private var recolorTask: Task<Void, Never>?
    @State private var busy = false
    @State private var errorText: String?
    @State private var notice: String?
    @State private var pcStatus = ""

    init() {
        var s = Settings()
        s.widthMM = 90
        s.heightMM = 90
        s.colors = 0  // automatic
        _settings = State(initialValue: s)
    }

    private var pcConfigured: Bool { !pcHost.trimmingCharacters(in: .whitespaces).isEmpty }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    PhotosPicker(selection: $pickerItem, matching: .images) {
                        Label(image == nil ? "Choose image" : "Choose a different image", systemImage: "photo")
                    }
                    if #available(iOS 17, *), let original = photoOriginal {
                        Button {
                            showSubjectIsolation = true
                        } label: {
                            Label("Isolate subject", systemImage: "scissors")
                        }
                        if image !== original {
                            Button("Restore original photo") { useImage(original, sourcePhoto: original) }
                        }
                    }
                    Button {
                        showLettering = true
                    } label: {
                        Label("Add lettering", systemImage: "textformat")
                    }
                    Button {
                        showMotifs = true
                    } label: {
                        Label("Add motif", systemImage: "star")
                    }
                    Button { showLayout = true } label: {
                        Label(settings.fixedHoop ? "Edit 4 × 4 layout" : "4 × 4 hoop layout", systemImage: "square.grid.2x2")
                    }
                }
                .disabled(busy)

                if let design {
                    Section("Stitch preview") {
                        Image(uiImage: design.preview)
                            .resizable()
                            .scaledToFit()
                            .frame(maxWidth: .infinity)
                    }
                    Section {
                        Text(String(format: "%.1f x %.1f in, %d stitches, %d colors, about %.0f min",
                                    design.widthMM / mmPerInch, design.heightMM / mmPerInch, design.stitchCount,
                                    design.threads.count, design.minutes))
                        ForEach(Array(design.threads.enumerated()), id: \.offset) { i, t in
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
                    } header: {
                        Text(design.fromPC ? "Design (made on your PC)" : "Design (made on this phone)")
                    } footer: {
                        if !design.fromPC {
                            Text("Phone-made designs only use simple fill stitching. Connect to your PC for the full quality.")
                        }
                    }
                }

                if let notice {
                    Section { Text(notice).foregroundStyle(.orange) }
                }
                if let errorText {
                    Section { Text(errorText).foregroundStyle(.red) }
                }

                Section {
                    if settings.fixedHoop {
                        LabeledContent("Hoop", value: Hoop.fourByFour.rawValue)
                    } else {
                        Picker("Hoop", selection: $hoop) {
                            ForEach(Hoop.allCases) { Text($0.rawValue).tag($0) }
                        }
                        Toggle("Lock width x height", isOn: $lockRatio)
                        inchSlider("Width", Binding(get: { settings.widthMM }, set: { setWidth($0) }), maxMM: hoop.sizeMM.w)
                        inchSlider("Height", Binding(get: { settings.heightMM }, set: { setHeight($0) }), maxMM: hoop.sizeMM.h)
                    }
                } header: {
                    Text("Size")
                } footer: {
                    Text(settings.fixedHoop ? "Edit individual sizes and positions in the hoop layout."
                         : "The design is scaled to fit inside this box and keeps its proportions. Stay a little under the hoop size.")
                }

                Section {
                    Picker("Fabric", selection: $fabric) {
                        ForEach(Fabric.allCases) { Text($0.rawValue).tag($0) }
                    }
                    Stepper(settings.colors == 0 ? "Thread colors: Auto" : "Thread colors: \(settings.colors)",
                            value: $settings.colors, in: 0...15)
                    Toggle("Remove background", isOn: $settings.removeBackground)
                        .disabled(settings.fixedHoop || (photoOriginal != nil && image !== photoOriginal))
                    VStack(alignment: .leading) {
                        Text(settings.boldMM < 0.05 ? "Bolder lines: off" : String(format: "Bolder lines: +%.1f mm", settings.boldMM))
                        Slider(value: $settings.boldMM, in: 0...1.0, step: 0.1)
                    }
                } header: {
                    Text("Basics")
                } footer: {
                    Text("\(fabric.tip) Density, stitch length and the other stitch settings are chosen for you from the fabric.")
                }

                Section {
                    DisclosureGroup("Advanced (optional)", isExpanded: $showAdvanced) {
                        Picker("Outline", selection: $outline) {
                            ForEach(lineTypes, id: \.self) { Text($0.capitalized).tag($0) }
                        }
                        Picker("Color edges", selection: $edge) {
                            ForEach(lineTypes, id: \.self) { Text($0.capitalized).tag($0) }
                        }
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
                    Text("Outline and edges are made by your PC. The stitch settings only affect phone-made designs; on the PC, the fabric picks them. Stitch settings are in millimetres.")
                }

                Section {
                    DisclosureGroup("PC connection", isExpanded: $showPC) {
                        TextField("PC address, e.g. 192.168.1.20:8793", text: $pcHost)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .keyboardType(.URL)
                        TextField("Pairing code", text: $pcCode)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                        Button("Test connection") { testConnection() }
                        if !pcStatus.isEmpty { Text(pcStatus).font(.footnote) }
                    }
                } footer: {
                    Text(pcConfigured
                         ? "Designs are made on your PC (full quality) and downloaded here. If the PC can't be reached, the phone makes a simpler version."
                         : "Not connected: designs are made on this phone with simple fill stitching. On your PC, run show_phone_info.bat to get the address and code.")
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
        .sheet(isPresented: $showSubjectIsolation) {
            if #available(iOS 17, *), let original = photoOriginal {
                SubjectIsolationView(original: original) { cutout in
                    useImage(cutout, sourcePhoto: original)
                }
            }
        }
        .sheet(isPresented: $showLettering) {
            LetteringView(hoopMM: hoop.sizeMM, pcConfigured: pcConfigured, draft: $letteringDraft) { img, style, colors, _ in
                useImage(img, style: style, colors: colors)
            }
        }
        .sheet(isPresented: $showMotifs) {
            LetteringView(hoopMM: hoop.sizeMM, pcConfigured: pcConfigured, draft: $motifDraft, motifOnly: true) { img, style, colors, _ in
                useImage(img, style: style, colors: colors)
            }
        }
        .sheet(isPresented: $showLayout) {
            HoopLayoutView(items: layoutItems, style: layoutStyle, pcConfigured: pcConfigured) { image, style, colors, items in
                layoutItems = items
                layoutStyle = style
                useImage(image, style: style, colors: colors, fixedHoop: true)
            }
        }
        .onChange(of: pickerItem) { item in
            guard let item else { return }
            Task {
                if let data = try? await item.loadTransferable(type: Data.self), let img = UIImage(data: data) {
                    useImage(img, sourcePhoto: img)
                } else {
                    errorText = "Could not load that photo."
                }
            }
        }
        .onChange(of: hoop) { _ in fitToHoop() }
        .onChange(of: lockRatio) { locked in
            if locked { setWidth(settings.widthMM) }
        }
        .onChange(of: fabric) { _ in applyAutomatic() }
    }

    // MARK: - Colors

    /// Lets the user change a thread's color after conversion; the stitches stay the same.
    private func colorBinding(_ i: Int) -> Binding<Color> {
        Binding<Color>(
            get: {
                guard let t = design?.threads[safe: i] else { return .gray }
                return Color(red: Double(t.r) / 255, green: Double(t.g) / 255, blue: Double(t.b) / 255)
            },
            set: { newColor in
                var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
                UIColor(newColor).getRed(&r, green: &g, blue: &b, alpha: &a)
                let rgb = RGB(r: Int((r * 255).rounded()), g: Int((g * 255).rounded()), b: Int((b * 255).rounded()))
                recolorThread(i, rgb)
            }
        )
    }

    private func recolorThread(_ i: Int, _ rgb: RGB) {
        guard design != nil, i < design!.threads.count else { return }
        design!.threads[i].r = rgb.r
        design!.threads[i].g = rgb.g
        design!.threads[i].b = rgb.b
        design!.threads[i].name = ThreadChart.threads[nearestBrother(rgb)].name

        if design!.fromPC {
            // the PC rebuilds the file with the new color; wait until the picker stops changing
            recolor[i] = rgb
            recolorTask?.cancel()
            recolorTask = Task {
                try? await Task.sleep(nanoseconds: 800_000_000)
                if !Task.isCancelled { generate(keepColors: true) }
            }
        } else if var pattern = design!.pattern {
            pattern.threads[i].r = rgb.r
            pattern.threads[i].g = rgb.g
            pattern.threads[i].b = rgb.b
            design!.pattern = pattern
            design!.preview = Digitizer.preview(pattern, fixedHoop: settings.fixedHoop)
            design!.pes = PES.data(for: pattern)
            writeFile(design!.pes)
        }
    }

    private func writeFile(_ data: Data) {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("design.pes")
        try? data.write(to: url)
        fileURL = url
    }

    /// Sets every stitch setting from the chosen fabric. These are good defaults for most designs.
    private func applyAutomatic() {
        settings.rowSpacingMM = fabric.rowSpacingMM
        settings.pullCompMM = fabric.pullCompMM
        settings.stitchLengthMM = 4.0
        settings.fillAngle = 45
        settings.underlay = true
        settings.cleanup = 1
        settings.brotherColors = true
        settings.trimMM = 1
    }

    /// Starts a new design from a picture (a photo, or lettering drawn on the lettering page).
    private func useImage(_ img: UIImage, style: LetteringStitchStyle? = nil, colors: Int = 0,
                          fixedHoop: Bool = false, sourcePhoto: UIImage? = nil) {
        design = nil
        fileURL = nil
        recolorTask?.cancel()
        let isolated = sourcePhoto != nil && img !== sourcePhoto
        if style != nil || isolated {
            if photoRemoveBackground == nil { photoRemoveBackground = settings.removeBackground }
            settings.removeBackground = false
        } else if let previous = photoRemoveBackground {
            settings.removeBackground = previous
            photoRemoveBackground = nil
        }
        image = img
        photoOriginal = sourcePhoto
        artworkColorCount = colors
        settings.letteringStyle = style
        settings.fixedHoop = fixedHoop
        settings.preserveTransparency = isolated
        aspect = img.size.width / max(1, img.size.height)
        applyAutomatic()
        fitToHoop()
        generate()
    }

    // MARK: - Size

    /// Sets the width; with the lock on, the height follows (and the pair stays inside the hoop).
    private func setWidth(_ mm: Double) {
        settings.widthMM = mm
        guard lockRatio else { return }
        var h = mm / aspect
        if h > hoop.sizeMM.h {
            h = hoop.sizeMM.h
            settings.widthMM = h * aspect
        }
        settings.heightMM = h
    }

    private func setHeight(_ mm: Double) {
        settings.heightMM = mm
        guard lockRatio else { return }
        var w = mm * aspect
        if w > hoop.sizeMM.w {
            w = hoop.sizeMM.w
            settings.heightMM = w / aspect
        }
        settings.widthMM = w
    }

    /// Biggest design that fits the hoop with a margin (keeps the proportions if locked).
    private func fitToHoop() {
        if settings.fixedHoop {
            settings.widthMM = Hoop.fourByFour.sizeMM.w
            settings.heightMM = Hoop.fourByFour.sizeMM.h
            return
        }
        var w = hoop.sizeMM.w - 10
        var h = hoop.sizeMM.h - 10
        if lockRatio {
            w = min(w, h * aspect)
            h = w / aspect
        }
        settings.widthMM = w
        settings.heightMM = h
    }

    // MARK: - Controls

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

    // MARK: - PC and conversion

    private func testConnection() {
        pcStatus = "Connecting..."
        let client = PCClient(host: pcHost, token: pcCode)
        Task {
            do {
                let name = try await client.ping()
                pcStatus = "Connected to \(name)."
            } catch {
                pcStatus = error.localizedDescription
            }
        }
    }

    private func generate(keepColors: Bool = false) {
        guard let img = image, !busy else { return }
        if !keepColors { recolor = [:] }
        let s = settings
        let fabricName = fabric.rawValue
        let edgeType = edge, outlineType = outline
        let overrides = recolor
        let editorColors = artworkColorCount
        let client = pcConfigured ? PCClient(host: pcHost, token: pcCode) : nil
        busy = true
        errorText = nil
        notice = nil
        Task {
            defer {
                busy = false
                if image !== img { generate() }
            }
            var made: Design?
            if let client {
                do {
                    made = try await client.convert(image: img, settings: s, fabric: fabricName, edge: edgeType,
                                                    outline: outlineType, recolor: overrides)
                } catch let error as PCError {
                    switch error {
                    case .rejected(let message):
                        errorText = message  // a real problem with the image: the phone won't do better
                    default:
                        if s.letteringStyle?.requiresPC == true {
                            errorText = error.localizedDescription
                        } else {
                            notice = "\(error.localizedDescription) Made a simpler version on the phone instead."
                        }
                    }
                } catch {
                    if s.letteringStyle?.requiresPC == true {
                        errorText = error.localizedDescription
                    } else {
                        notice = "PC connection failed. Made a simpler version on the phone instead."
                    }
                }
            }
            if made == nil && errorText == nil {
                if s.letteringStyle?.requiresPC == true {
                    errorText = "Connect to the PC server to use this stitch style."
                } else {
                    var local = s
                    if local.colors == 0 { local.colors = min(15, editorColors) }
                    made = await convertOnPhone(img, local)
                }
            }
            if let made, image === img {
                design = made
                if made.heightMM > 0 { aspect = made.widthMM / made.heightMM }  // lock follows the design itself
                writeFile(made.pes)
            }
        }
    }

    private func convertOnPhone(_ img: UIImage, _ s: Settings) async -> Design? {
        do {
            return try await Task.detached(priority: .userInitiated) { () -> Design in
                let r = try Digitizer.convert(img, s)
                return Design(threads: r.pattern.threads, widthMM: r.widthMM, heightMM: r.heightMM,
                              stitchCount: r.stitchCount, minutes: r.minutes, pes: PES.data(for: r.pattern),
                              preview: Digitizer.preview(r.pattern, fixedHoop: s.fixedHoop), fromPC: false, pattern: r.pattern)
            }.value
        } catch {
            errorText = error.localizedDescription
            return nil
        }
    }
}
