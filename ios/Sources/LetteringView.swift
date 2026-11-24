import SwiftUI
import UIKit

/// A font offered on the lettering page. Only fonts installed on the phone are shown.
struct LetteringFont: Identifiable, Hashable {
    let name: String        // shown to the user
    let postScript: String  // UIFont name
    let group: String
    var id: String { postScript }

    static let all: [LetteringFont] = [
        LetteringFont(name: "Avenir Heavy", postScript: "AvenirNext-Heavy", group: "Bold"),
        LetteringFont(name: "Futura Bold", postScript: "Futura-Bold", group: "Bold"),
        LetteringFont(name: "Helvetica Bold", postScript: "Helvetica-Bold", group: "Bold"),
        LetteringFont(name: "Rounded", postScript: "ArialRoundedMTBold", group: "Bold"),
        LetteringFont(name: "Gill Sans Bold", postScript: "GillSans-Bold", group: "Bold"),
        LetteringFont(name: "Copperplate", postScript: "Copperplate-Bold", group: "Bold"),
        LetteringFont(name: "Georgia Bold", postScript: "Georgia-Bold", group: "Serif"),
        LetteringFont(name: "Baskerville Bold", postScript: "Baskerville-Bold", group: "Serif"),
        LetteringFont(name: "Rockwell Bold", postScript: "Rockwell-Bold", group: "Serif"),
        LetteringFont(name: "Didot Bold", postScript: "Didot-Bold", group: "Serif"),
        LetteringFont(name: "Typewriter", postScript: "AmericanTypewriter-Bold", group: "Serif"),
        LetteringFont(name: "Snell Script", postScript: "SnellRoundhand-Black", group: "Script"),
        LetteringFont(name: "Bradley Hand", postScript: "BradleyHandITCTT-Bold", group: "Script"),
        LetteringFont(name: "Noteworthy", postScript: "Noteworthy-Bold", group: "Script"),
        LetteringFont(name: "Chalkboard", postScript: "ChalkboardSE-Bold", group: "Fun"),
        LetteringFont(name: "Marker Felt", postScript: "MarkerFelt-Wide", group: "Fun"),
        LetteringFont(name: "Papyrus", postScript: "Papyrus-Condensed", group: "Fun"),
    ].filter { UIFont(name: $0.postScript, size: 12) != nil }
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

struct LetteringView: View {
    /// The hoop's sewing field in mm; the text is sized to fit it with a margin.
    let hoopMM: (w: Double, h: Double)
    let onUse: (UIImage) -> Void

    @Environment(\.dismiss) private var dismiss
    @AppStorage("letteringText") private var text = "Your text"
    @AppStorage("letteringFont") private var fontName = "AvenirNext-Heavy"
    @State private var alignment = 1  // 0 left, 1 center, 2 right
    @State private var letterSpacing = 4.0
    @State private var lineSpacing = 20.0
    @State private var threadColor = Color.black
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

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    preview
                    textBox
                    fontGallery
                    styleControls
                }
                .padding()
            }
            .background(Color(.systemGroupedBackground))
            .navigationTitle("Lettering")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Use text") {
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
                    Button("Done") { editing = false }
                }
            }
        }
    }

    // MARK: - Sections

    private var preview: some View {
        VStack(alignment: .leading, spacing: 8) {
            ZStack {
                RoundedRectangle(cornerRadius: 16)
                    .fill(Color(red: 0.93, green: 0.91, blue: 0.88))  // fabric
                if let image = rendered {
                    Image(uiImage: image)
                        .resizable()
                        .scaledToFit()
                        .padding(12)
                } else {
                    Text("Type something below").foregroundStyle(.secondary)
                }
            }
            .frame(height: 180)
            .overlay(RoundedRectangle(cornerRadius: 16).stroke(Color.black.opacity(0.08)))

            if let image = rendered {
                let cap = capitalHeight(image)
                if cap < 0.25 {
                    Label(String(format: "Capital letters about %.2f in tall: too small to stitch cleanly. Use fewer words, more lines, or a bigger hoop.", cap),
                          systemImage: "exclamationmark.triangle.fill")
                        .font(.footnote)
                        .foregroundStyle(.orange)
                } else {
                    Label(String(format: "Capital letters about %.2f in tall when it fills the hoop.", cap),
                          systemImage: "ruler")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    private var textBox: some View {
        card(title: "Text") {
            TextEditor(text: $text)
                .focused($editing)
                .font(.system(size: 20, weight: .semibold))
                .frame(minHeight: 70, maxHeight: 120)
                .scrollContentBackground(.hidden)
            Text("Press Return for a new line.").font(.footnote).foregroundStyle(.secondary)
        }
    }

    private var fontGallery: some View {
        card(title: "Font") {
            ForEach(["Bold", "Serif", "Script", "Fun"], id: \.self) { group in
                let fonts = LetteringFont.all.filter { $0.group == group }
                if !fonts.isEmpty {
                    Text(group.uppercased()).font(.caption2.weight(.semibold)).foregroundStyle(.secondary)
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 10) {
                            ForEach(fonts) { f in fontChip(f) }
                        }
                        .padding(.vertical, 2)
                    }
                }
            }
            Text("Bold fonts stitch best. Thin scripts need to be sewn bigger.")
                .font(.footnote).foregroundStyle(.secondary)
        }
    }

    private func fontChip(_ f: LetteringFont) -> some View {
        let selected = f.postScript == fontName
        return Button {
            fontName = f.postScript
        } label: {
            VStack(spacing: 4) {
                Text("Abc").font(.custom(f.postScript, size: 26)).lineLimit(1)
                Text(f.name).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
            }
            .frame(width: 96, height: 70)
            .background(RoundedRectangle(cornerRadius: 12).fill(selected ? Color.accentColor.opacity(0.15) : Color(.secondarySystemGroupedBackground)))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(selected ? Color.accentColor : Color.black.opacity(0.08), lineWidth: selected ? 2 : 1))
        }
        .buttonStyle(.plain)
        .foregroundStyle(.primary)
    }

    private var styleControls: some View {
        card(title: "Style") {
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
    }

    private func card<Content: View>(title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title).font(.headline)
            content()
        }
        .padding()
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 16).fill(Color(.secondarySystemGroupedBackground)))
    }
}
