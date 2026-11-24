import CoreText
import SwiftUI
import UIKit

/// A font available to the lettering editor.
struct LetteringFont: Identifiable {
    let name: String
    let postScript: String
    let category: String
    var id: String { postScript }

    private static let symbolFamilies: Set<String> = [
        "Zapf Dingbats", "Bodoni Ornaments", "Apple Color Emoji", "Apple Symbols", "Symbol",
    ]

    static let all: [LetteringFont] = {
        var bundled: [String: String] = [:]
        if let folders = Bundle.main.resourceURL?.appendingPathComponent("fonts", isDirectory: true),
           let data = try? Data(contentsOf: folders.appendingPathComponent("categories.json")),
           let categories = try? JSONDecoder().decode([String: String].self, from: data) {
            for (file, category) in categories {
                let url = folders.appendingPathComponent(file) as CFURL
                CTFontManagerRegisterFontsForURL(url, .process, nil)
                let descriptors = CTFontManagerCreateFontDescriptorsFromURL(url) as? [CTFontDescriptor] ?? []
                for descriptor in descriptors {
                    if let name = CTFontDescriptorCopyAttribute(descriptor, kCTFontNameAttribute) as? String {
                        bundled[name] = category
                    }
                }
            }
        }

        var names = Set(bundled.keys)
        for family in UIFont.familyNames where !symbolFamilies.contains(family) {
            names.formUnion(UIFont.fontNames(forFamilyName: family))
        }
        return names.compactMap { name -> LetteringFont? in
            guard let font = UIFont(name: name, size: 12) else { return nil }
            let face = font.fontDescriptor.object(forKey: .face) as? String ?? ""
            let plain = ["", "Regular", "Roman", "Normal", "Book", "Plain"].contains(face)
            let label = plain ? font.familyName : "\(font.familyName) \(face)"
            return LetteringFont(name: label, postScript: name, category: bundled[name] ?? "System")
        }.sorted {
            let comparison = $0.name.localizedCaseInsensitiveCompare($1.name)
            return comparison == .orderedSame ? $0.id < $1.id : comparison == .orderedAscending
        }
    }()

    static let categories = ["All"] + Set(all.map(\.category)).sorted()
}

struct LetteringView: View {
    let hoopMM: (w: Double, h: Double)
    let pcConfigured: Bool
    @Binding var savedDraft: LetteringDraft
    let motifOnly: Bool
    let onUse: (UIImage, LetteringStitchStyle, Int) -> Void

    @Environment(\.dismiss) private var dismiss
    @AppStorage("letteringText") private var savedText = "Bears"
    @AppStorage("letteringFont") private var savedFont = "Georgia-Bold"
    @State private var draft: LetteringDraft
    @State private var fontPicker: FontPickerRequest?
    @State private var previousText = ""
    @State private var showStyle = false
    @State private var showMotif = false
    @FocusState private var editing: Bool

    init(hoopMM: (w: Double, h: Double), pcConfigured: Bool, draft: Binding<LetteringDraft>,
         motifOnly: Bool = false, onUse: @escaping (UIImage, LetteringStitchStyle, Int) -> Void) {
        self.hoopMM = hoopMM
        self.pcConfigured = pcConfigured
        self._savedDraft = draft
        self._draft = State(initialValue: draft.wrappedValue)
        self.motifOnly = motifOnly
        self.onUse = onUse
    }

    private var font: UIFont {
        _ = LetteringFont.all   // register bundled fonts before resolving the saved selection
        return UIFont(name: draft.fontName, size: LetteringRenderer.pointSize)
            ?? UIFont.systemFont(ofSize: LetteringRenderer.pointSize, weight: .heavy)
    }

    var body: some View {
        let selectedFont = motifOnly ? UIFont.systemFont(ofSize: LetteringRenderer.pointSize) : font
        let image = LetteringRenderer.render(draft, font: selectedFont, motifOnly: motifOnly)
        NavigationStack {
            VStack(spacing: 0) {
                preview(image, font: selectedFont)
                Divider()
                Form {
                    if !motifOnly {
                        Section {
                            TextField("Your text", text: $draft.text, axis: .vertical)
                                .focused($editing)
                                .font(.system(size: 20, weight: .semibold))
                                .lineLimit(1...4)
                            letterTargets
                            DisclosureGroup("Style", isExpanded: $showStyle) { styleControls }
                        }
                    }
                    Section {
                        if motifOnly {
                            motifControls
                        } else {
                            DisclosureGroup("Motif", isExpanded: $showMotif) { motifControls }
                        }
                    }
                    Section {
                        Picker("Stitch style", selection: $draft.stitch) {
                            ForEach(LetteringStitchStyle.allCases) { style in
                                Text(style.title).tag(style)
                            }
                        }
                    } footer: {
                        if draft.stitch.requiresPC && !pcConfigured {
                            Text("Connect to the PC server to use this stitch style.")
                        } else if draft.stitch == .auto && !pcConfigured {
                            Text("The phone uses fill stitches. Auto uses satin and fill when connected to the PC.")
                        }
                    }
                }
            }
            .navigationTitle(motifOnly ? "Motifs" : "Lettering")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") {
                        guard let image else { return }
                        if !motifOnly {
                            savedText = draft.text
                            savedFont = draft.fontName
                        }
                        savedDraft = draft
                        onUse(image, draft.stitch, LetteringRenderer.colorCount(draft, motifOnly: motifOnly))
                        dismiss()
                    }
                    .bold()
                    .disabled(image == nil || (draft.stitch.requiresPC && !pcConfigured))
                }
                ToolbarItemGroup(placement: .keyboard) {
                    Spacer()
                    Button("Hide keyboard") { editing = false }
                }
            }
            .onAppear { previousText = draft.text }
            .onChange(of: draft.text) { text in
                draft.remapLetterFonts(from: previousText)
                previousText = text
            }
            .fullScreenCover(item: $fontPicker) { request in
                LetterFontPicker(draft: draft, selection: request.letter) { base, letters in
                    draft.fontName = base
                    draft.letterFonts = letters
                }
            }
        }
    }

    private struct FontPickerRequest: Identifiable {
        let letter: Int?
        var id: Int { letter ?? -1 }
    }

    private var letterTargets: some View {
        let characters = Array(draft.text)
        return VStack(alignment: .leading, spacing: 6) {
            Text("Letter fonts").font(.subheadline)
            ScrollView(.horizontal) {
                HStack(spacing: 6) {
                    targetButton("Default font", index: nil)
                    ForEach(characters.indices.filter { !characters[$0].isWhitespace }, id: \.self) { index in
                        targetButton(String(characters[index]), index: index)
                    }
                }
            }
        }
    }

    private func targetButton(_ label: String, index: Int?) -> some View {
        let face = index.flatMap { draft.letterFonts[$0] } ?? draft.fontName
        return Button {
            editing = false
            fontPicker = FontPickerRequest(letter: index)
        } label: {
            Text(label)
                .font(index == nil ? .body : .custom(face, size: 20))
                .padding(.horizontal, 10)
                .frame(minWidth: 40, minHeight: 44)
                .background(Color.accentColor.opacity(draft.letterFonts[index ?? -1] == nil ? 0.08 : 0.18),
                            in: RoundedRectangle(cornerRadius: 10))
        }
        .buttonStyle(.plain)
        .accessibilityLabel(index.map { "Character \($0 + 1), \(label)" } ?? "Default font")
    }

    private func preview(_ image: UIImage?, font: UIFont) -> some View {
        VStack(spacing: 6) {
            ZStack {
                Color(.systemGray5)
                if let image {
                    Image(uiImage: image).resizable().scaledToFit().padding(18)
                        .accessibilityLabel("Design preview")
                } else {
                    Text(motifOnly ? "Choose a motif" : "Type something below").foregroundStyle(.secondary)
                }
            }
            .frame(height: 185)
            if let image, !motifOnly, draft.shape == .straight, draft.letterFonts.isEmpty,
               !draft.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                let ink = image.size
                let cap = Double(font.capHeight) * min((hoopMM.w - 10) / ink.width, (hoopMM.h - 10) / ink.height) / 25.4
                Label(String(format: "Capitals about %.2f in tall when the design fills the hoop", cap),
                      systemImage: cap < 0.25 ? "exclamationmark.triangle.fill" : "ruler")
                    .foregroundStyle(cap < 0.25 ? Color.orange : Color.secondary)
                    .font(.footnote)
                    .padding(.bottom, 6)
            }
        }
        .background(Color(.systemGray5))
    }

    private var styleControls: some View {
        VStack(alignment: .leading, spacing: 12) {
            Picker("Shape", selection: $draft.shape) {
                ForEach(LetteringShape.allCases) { Text($0.rawValue).tag($0) }
            }
            if draft.shape != .straight && draft.shape != .circle {
                slider("Bend", value: $draft.bend, in: 0...1)
            }
            if draft.shape == .straight || draft.shape == .slant {
                Picker("Alignment", selection: $draft.alignment) {
                    Image(systemName: "text.alignleft").accessibilityLabel("Left").tag(0)
                    Image(systemName: "text.aligncenter").accessibilityLabel("Center").tag(1)
                    Image(systemName: "text.alignright").accessibilityLabel("Right").tag(2)
                }
                .pickerStyle(.segmented)
                slider("Line spacing", value: $draft.lineSpacing, in: 0...120)
            }
            slider("Letter spacing", value: $draft.letterSpacing, in: -10...40)
            ColorPicker("Letter color", selection: colorBinding($draft.threadColor), supportsOpacity: false)
            effectControls("Outline", effect: $draft.outline, amount: "Width")
            effectControls("Second outline", effect: $draft.outside, amount: "Width")
            effectControls("Drop shadow", effect: $draft.shadow, amount: "Offset")
        }
        .padding(.top, 8)
    }

    private var motifControls: some View {
        VStack(alignment: .leading, spacing: 12) {
            if LetteringRenderer.motifs.isEmpty {
                Text("Motifs are unavailable.").foregroundStyle(.secondary)
            } else {
                Picker("Motif", selection: $draft.motif) {
                    if !motifOnly { Text("None").tag("") }
                    ForEach(LetteringRenderer.motifs.keys.sorted(), id: \.self) { Text($0).tag($0) }
                }
                if !draft.motif.isEmpty {
                    ColorPicker("Motif color", selection: colorBinding($draft.motifColor), supportsOpacity: false)
                    if !motifOnly {
                        Picker("Placement", selection: $draft.placement) {
                            ForEach(MotifPlacement.allCases) { Text($0.rawValue).tag($0) }
                        }
                        slider("Motif size", value: $draft.motifScale, in: 0.25...1.5)
                    }
                }
            }
        }
    }

    private func slider(_ title: String, value: Binding<Double>, in range: ClosedRange<Double>) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.subheadline)
            Slider(value: value, in: range) { Text(title) }
        }
    }

    private func effectControls(_ title: String, effect: Binding<LetteringEffect>, amount: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Toggle(title, isOn: effect.enabled)
            if effect.wrappedValue.enabled {
                ColorPicker("\(title) color", selection: colorBinding(effect.color), supportsOpacity: false)
                slider(amount, value: effect.amount, in: 0.02...0.14)
            }
        }
    }

    private func colorBinding(_ color: Binding<UIColor>) -> Binding<Color> {
        Binding(get: { Color(uiColor: color.wrappedValue) }, set: { color.wrappedValue = UIColor($0) })
    }
}
