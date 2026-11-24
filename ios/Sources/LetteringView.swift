import SwiftUI
import UIKit

/// A font installed on the phone, offered on the lettering page.
struct LetteringFont: Identifiable, Hashable {
    let name: String        // shown to the user, e.g. "Georgia Bold"
    let postScript: String  // UIFont name
    var id: String { postScript }
    var initial: String {
        let first = name.prefix(1).uppercased()
        return first.rangeOfCharacter(from: .letters) == nil ? "#" : first
    }

    /// Icon / symbol fonts: their letters aren't letters, so they're left out.
    private static let symbolFamilies = ["Zapf Dingbats", "Bodoni Ornaments", "Apple Color Emoji", "Apple Symbols", "Symbol"]

    /// Every font on the phone, A to Z, named "Family Style" (just "Family" for the regular one).
    static let all: [LetteringFont] = {
        var out: [LetteringFont] = []
        for family in UIFont.familyNames.sorted() where !symbolFamilies.contains(family) {
            for ps in UIFont.fontNames(forFamilyName: family) {
                guard let font = UIFont(name: ps, size: 12) else { continue }
                let face = (font.fontDescriptor.object(forKey: .face) as? String) ?? ""
                let plain = face.isEmpty || ["Regular", "Roman", "Normal", "Book", "Plain"].contains(face)
                out.append(LetteringFont(name: plain ? family : "\(family) \(face)", postScript: ps))
            }
        }
        return out.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
    }()

    /// The letters that have at least one font, for the A-Z wheel.
    static let initials: [String] = {
        var seen: [String] = []
        for f in all where !seen.contains(f.initial) { seen.append(f.initial) }
        return seen
    }()
}

/// Draws text as a transparent image the digitizer can turn into stitches.
enum LetteringRenderer {
    static let pointSize: CGFloat = 240

    static func render(text: String, font: UIFont, color: UIColor, alignment: NSTextAlignment,
                       letterSpacing: CGFloat, lineSpacing: CGFloat) -> UIImage? {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        let paragraph = NSMutableParagraphStyle()
        paragraph.alignment = alignment
        paragraph.lineSpacing = lineSpacing
        let attributes: [NSAttributedString.Key: Any] = [
            .font: font, .foregroundColor: color, .kern: letterSpacing, .paragraphStyle: paragraph,
        ]
        let string = NSAttributedString(string: trimmed, attributes: attributes)
        let options: NSStringDrawingOptions = [.usesLineFragmentOrigin, .usesFontLeading]
        let bounds = string.boundingRect(with: CGSize(width: 20_000, height: 20_000), options: options, context: nil).integral
        let pad = font.pointSize * 0.35  // room for script swashes that reach past the glyph box
        let size = CGSize(width: bounds.width + 2 * pad, height: bounds.height + 2 * pad)
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        format.opaque = false
        return UIGraphicsImageRenderer(size: size, format: format).image { _ in
            string.draw(with: CGRect(x: pad, y: pad, width: bounds.width, height: bounds.height),
                        options: options, context: nil)
        }
    }
}

/// Lettering, laid out like Artspira's character picker: a big preview on top, the text, and a
/// two-column wheel (A-Z on the left jumps the font list; every font name is written in its own font).
struct LetteringView: View {
    /// The hoop's sewing field in mm; the text is sized to fit it with a margin.
    let hoopMM: (w: Double, h: Double)
    let onUse: (UIImage) -> Void

    @Environment(\.dismiss) private var dismiss
    @AppStorage("letteringText") private var text = "Bears"
    @AppStorage("letteringFont") private var fontName = "Georgia-Bold"
    @State private var alignment = 1  // 0 left, 1 center, 2 right
    @State private var letterSpacing = 4.0
    @State private var lineSpacing = 20.0
    @State private var threadColor = Color.black
    @State private var showStyle = false
    @FocusState private var editing: Bool

    private var font: UIFont {
        UIFont(name: fontName, size: LetteringRenderer.pointSize)
            ?? UIFont.systemFont(ofSize: LetteringRenderer.pointSize, weight: .heavy)
    }

    private var rendered: UIImage? {
        LetteringRenderer.render(text: text, font: font, color: UIColor(threadColor),
                                 alignment: [NSTextAlignment.left, .center, .right][alignment],
                                 letterSpacing: letterSpacing, lineSpacing: lineSpacing)
    }

    /// Roughly how tall capital letters come out when the text fills the hoop (in inches).
    private func capitalHeight(_ image: UIImage) -> Double {
        let boxW = hoopMM.w - 10, boxH = hoopMM.h - 10
        let pad = Double(font.pointSize * 0.35)
        let inkW = max(1, Double(image.size.width) - 2 * pad), inkH = max(1, Double(image.size.height) - 2 * pad)
        let mmPerPoint = min(boxW / inkW, boxH / inkH)  // the app crops the empty margin, then fits the box
        return Double(font.capHeight) * mmPerPoint / 25.4
    }

    /// The A-Z wheel follows the font wheel; turning it jumps to the first font with that letter.
    private var letter: Binding<String> {
        Binding(
            get: { LetteringFont.all.first { $0.postScript == fontName }?.initial ?? "A" },
            set: { newLetter in
                if let first = LetteringFont.all.first(where: { $0.initial == newLetter }) {
                    fontName = first.postScript
                }
            }
        )
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                preview
                Divider()
                ScrollView {
                    VStack(alignment: .leading, spacing: 14) {
                        TextField("Your text", text: $text, axis: .vertical)
                            .focused($editing)
                            .font(.system(size: 20, weight: .semibold))
                            .lineLimit(1...4)
                            .padding(12)
                            .background(RoundedRectangle(cornerRadius: 12).fill(Color(.secondarySystemBackground)))
                        fontWheel
                        DisclosureGroup("Style", isExpanded: $showStyle) { styleControls }
                            .padding(12)
                            .background(RoundedRectangle(cornerRadius: 12).fill(Color(.secondarySystemBackground)))
                    }
                    .padding()
                }
            }
            .navigationTitle("Lettering")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") {
                        if let image = rendered {
                            onUse(image)
                            dismiss()
                        }
                    }
                    .bold()
                    .disabled(rendered == nil)
                }
                ToolbarItemGroup(placement: .keyboard) {
                    Spacer()
                    Button("Hide keyboard") { editing = false }
                }
            }
        }
    }

    // MARK: - Sections

    private var preview: some View {
        VStack(spacing: 6) {
            ZStack {
                Color(.systemGray6)
                if let image = rendered {
                    Image(uiImage: image)
                        .resizable()
                        .scaledToFit()
                        .padding(18)
                } else {
                    Text("Type something below").foregroundStyle(.secondary)
                }
            }
            .frame(height: 230)
            if let image = rendered {
                let cap = capitalHeight(image)
                Group {
                    if cap < 0.25 {
                        Label(String(format: "Capitals about %.2f in tall: too small to stitch cleanly", cap),
                              systemImage: "exclamationmark.triangle.fill")
                            .foregroundStyle(.orange)
                    } else {
                        Label(String(format: "Capitals about %.2f in tall when it fills the hoop", cap),
                              systemImage: "ruler")
                            .foregroundStyle(.secondary)
                    }
                }
                .font(.footnote)
                .padding(.bottom, 6)
            }
        }
        .background(Color(.systemGray6))
    }

    private var fontWheel: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Font").font(.headline)
            HStack(spacing: 0) {
                Picker("Letter", selection: letter) {
                    ForEach(LetteringFont.initials, id: \.self) { l in
                        Text(l).font(.title3).tag(l)
                    }
                }
                .pickerStyle(.wheel)
                .frame(width: 64)
                .clipped()
                Picker("Font", selection: $fontName) {
                    ForEach(LetteringFont.all) { f in
                        Text(f.name)  // each font's name, written in that font
                            .font(.custom(f.postScript, size: 22))
                            .lineLimit(1)
                            .minimumScaleFactor(0.5)
                            .tag(f.postScript)
                    }
                }
                .pickerStyle(.wheel)
                .frame(maxWidth: .infinity)
                .clipped()
            }
            .frame(height: 190)
            Text("Bold fonts stitch best. Thin scripts need to be sewn bigger.")
                .font(.footnote).foregroundStyle(.secondary)
        }
    }

    private var styleControls: some View {
        VStack(alignment: .leading, spacing: 12) {
            Picker("Alignment", selection: $alignment) {
                Image(systemName: "text.alignleft").tag(0)
                Image(systemName: "text.aligncenter").tag(1)
                Image(systemName: "text.alignright").tag(2)
            }
            .pickerStyle(.segmented)
            ColorPicker("Thread color", selection: $threadColor, supportsOpacity: false)
            VStack(alignment: .leading) {
                Text("Letter spacing").font(.subheadline)
                Slider(value: $letterSpacing, in: -10...40)
            }
            VStack(alignment: .leading) {
                Text("Line spacing").font(.subheadline)
                Slider(value: $lineSpacing, in: 0...120)
            }
            Text("A little extra letter spacing keeps letters from merging when stitched.")
                .font(.footnote).foregroundStyle(.secondary)
        }
        .padding(.top, 8)
    }
}
