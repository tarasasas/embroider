import SwiftUI
import UIKit

struct HoopLayoutItem: Identifiable {
    let id = UUID()
    let title: String
    let image: UIImage
    let colors: Set<UIColor>
    var center: CGPoint
    var size: CGSize
}

struct HoopLayoutView: View {
    private static let field = CGFloat(Hoop.fourByFour.sizeMM.w)
    private static let margin: CGFloat = 4
    private static let maximumSize = field - 2 * margin

    let pcConfigured: Bool
    let onUse: (UIImage, LetteringStitchStyle, Int, [HoopLayoutItem]) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var items: [HoopLayoutItem]
    @State private var selected: UUID?
    @State private var dragStart: CGPoint?
    @State private var lettering = LetteringDraft(text: "")
    @State private var motif = LetteringDraft(motif: "Star")
    @State private var showLettering = false
    @State private var showMotif = false
    @State private var style: LetteringStitchStyle

    init(items: [HoopLayoutItem], style: LetteringStitchStyle, pcConfigured: Bool,
         onUse: @escaping (UIImage, LetteringStitchStyle, Int, [HoopLayoutItem]) -> Void) {
        self._items = State(initialValue: items)
        self._style = State(initialValue: style)
        self.pcConfigured = pcConfigured
        self.onUse = onUse
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 14) {
                    HStack {
                        Button { showLettering = true } label: { Label("Add text", systemImage: "textformat") }
                        Spacer()
                        Button { showMotif = true } label: { Label("Add motif", systemImage: "star") }
                    }
                    .buttonStyle(.bordered)

                    GeometryReader { geometry in
                        let side = geometry.size.width
                        canvas(side: side)
                    }
                    .aspectRatio(1, contentMode: .fit)

                    if let index = items.firstIndex(where: { $0.id == selected }) {
                        itemControls(item: items[index])
                    } else {
                        Text(items.isEmpty ? "Add text or a motif to start." : "Tap or drag an item to select it.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                    Picker("Stitch style", selection: $style) {
                        ForEach(LetteringStitchStyle.allCases) { Text($0.title).tag($0) }
                    }
                    if style.requiresPC && !pcConfigured {
                        Text("Connect to the PC server to use this stitch style.").font(.footnote)
                    }
                }
                .padding()
                .frame(maxWidth: 520)
                .frame(maxWidth: .infinity)
            }
            .background(Color(.systemGroupedBackground))
            .navigationTitle("4 × 4 Hoop Layout")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { useLayout() }
                        .bold()
                        .disabled(items.isEmpty || (style.requiresPC && !pcConfigured))
                }
            }
            .sheet(isPresented: $showLettering) {
                LetteringView(hoopMM: Hoop.fourByFour.sizeMM, pcConfigured: pcConfigured,
                              draft: $lettering, placingElements: true) { _, _, _, draft in
                    addLettering(draft)
                }
            }
            .sheet(isPresented: $showMotif) {
                LetteringView(hoopMM: Hoop.fourByFour.sizeMM, pcConfigured: pcConfigured,
                              draft: $motif, motifOnly: true, placingElements: true) { image, _, _, draft in
                    insert(image: image, title: draft.motif, colors: [draft.motifColor], height: 21)
                }
            }
        }
    }

    private func canvas(side: CGFloat) -> some View {
        let scale = side / Self.field
        return ZStack(alignment: .topLeading) {
            Rectangle().fill(Color(red: 0.92, green: 0.94, blue: 0.96))
                .onTapGesture { selected = nil }
            Path { path in
                for division in 1...3 {
                    let mark = side * CGFloat(division) / 4
                    path.move(to: CGPoint(x: mark, y: 0))
                    path.addLine(to: CGPoint(x: mark, y: side))
                    path.move(to: CGPoint(x: 0, y: mark))
                    path.addLine(to: CGPoint(x: side, y: mark))
                }
            }
            .stroke(Color.blue.opacity(0.16), lineWidth: 1)
            .allowsHitTesting(false)
            Rectangle()
                .strokeBorder(Color.blue.opacity(0.35), style: StrokeStyle(lineWidth: 1, dash: [4, 4]))
                .padding(Self.margin * scale)
                .allowsHitTesting(false)
            ForEach(items) { item in
                Image(uiImage: item.image)
                    .resizable()
                    .frame(width: item.size.width * scale, height: item.size.height * scale)
                    .overlay(Rectangle().strokeBorder(Color.blue, lineWidth: selected == item.id ? 2 : 0))
                    .padding(5)
                    .contentShape(Rectangle())
                    .position(x: item.center.x * scale, y: item.center.y * scale)
                    .highPriorityGesture(DragGesture(minimumDistance: 0, coordinateSpace: .named("hoopLayoutCanvas"))
                        .onChanged { value in
                            guard let index = items.firstIndex(where: { $0.id == item.id }), scale > 0 else { return }
                            if dragStart == nil {
                                selected = item.id
                                dragStart = items[index].center
                            }
                            if let origin = dragStart {
                                let point = CGPoint(x: origin.x + value.translation.width / scale,
                                                    y: origin.y + value.translation.height / scale)
                                items[index].center = clamped(point, size: items[index].size)
                            }
                        }
                        .onEnded { _ in dragStart = nil })
                    .accessibilityLabel(item.title)
                    .accessibilityAddTraits(.isButton)
                    .accessibilityAction { selected = item.id }
            }
        }
        .frame(width: side, height: side)
        .coordinateSpace(name: "hoopLayoutCanvas")
        .clipped()
        .overlay(Rectangle().strokeBorder(Color.blue.opacity(0.35)).allowsHitTesting(false))
    }

    private func itemControls(item: HoopLayoutItem) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(item.title).font(.headline)
                Spacer()
                Button {
                    insert(image: item.image, title: item.title, colors: item.colors, size: item.size)
                } label: {
                    Image(systemName: "plus.square.on.square").accessibilityLabel("Duplicate item")
                }
                .buttonStyle(.bordered)
                Button(role: .destructive) {
                    items.removeAll { $0.id == item.id }
                    selected = nil
                } label: {
                    Image(systemName: "trash").accessibilityLabel("Remove item")
                }
                .buttonStyle(.bordered)
            }
            dimension("Width", item: item, keyPath: \.width)
            dimension("Height", item: item, keyPath: \.height)
        }
        .padding(12)
        .background(Color(.secondarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 14))
    }

    private func dimension(_ title: String, item: HoopLayoutItem, keyPath: WritableKeyPath<CGSize, CGFloat>) -> some View {
        let value = Binding<Double>(
            get: { Double(items.first { $0.id == item.id }?.size[keyPath: keyPath] ?? item.size[keyPath: keyPath]) },
            set: { value in
                guard let index = items.firstIndex(where: { $0.id == item.id }) else { return }
                items[index].size[keyPath: keyPath] = CGFloat(value)
                items[index].center = clamped(items[index].center, size: items[index].size)
            }
        )
        return HStack {
            Text(title).frame(width: 48, alignment: .leading)
            Slider(value: value, in: 2...Double(Self.maximumSize)) { Text(title) }
            Text(String(format: "%.2f in", value.wrappedValue / 25.4)).monospacedDigit()
        }
        .font(.footnote)
    }

    private func addLettering(_ draft: LetteringDraft) {
        _ = LetteringFont.all
        var glyph = draft
        glyph.motif = ""
        glyph.letterFonts = [:]
        for (index, character) in draft.text.enumerated() where !character.isWhitespace {
            glyph.text = String(character)
            let name = draft.letterFonts[index] ?? draft.fontName
            let font = UIFont(name: name, size: LetteringRenderer.pointSize)
                ?? UIFont.systemFont(ofSize: LetteringRenderer.pointSize, weight: .heavy)
            if let image = LetteringRenderer.render(glyph, font: font, motifOnly: false) {
                let height = image.size.height * 26 / LetteringRenderer.pointSize
                insert(image: image, title: glyph.text, colors: LetteringRenderer.colors(glyph, motifOnly: false),
                       height: height)
            }
        }
    }

    private func insert(image: UIImage, title: String, colors: Set<UIColor>, height: CGFloat) {
        let width = height * image.size.width / image.size.height
        let scale = min(1, Self.maximumSize / max(width, height))
        insert(image: image, title: title, colors: colors,
               size: CGSize(width: max(2, width * scale), height: max(2, height * scale)))
    }

    private func insert(image: UIImage, title: String, colors: Set<UIColor>, size: CGSize) {
        let center = nextPosition(size: size)
        let item = HoopLayoutItem(title: title, image: image, colors: colors, center: center, size: size)
        items.append(item)
        selected = item.id
    }

    private func nextPosition(size: CGSize) -> CGPoint {
        let occupied = items.map { rect(center: $0.center, size: $0.size).insetBy(dx: -2, dy: -2) }
        let start = clamped(.zero, size: size)
        let end = clamped(CGPoint(x: Self.field, y: Self.field), size: size)
        for y in stride(from: start.y, through: end.y, by: 3) {
            for x in stride(from: start.x, through: end.x, by: 3) {
                let center = CGPoint(x: x, y: y)
                let bounds = rect(center: center, size: size)
                if !occupied.contains(where: { $0.intersects(bounds) }) { return center }
            }
        }
        return CGPoint(x: Self.field / 2, y: Self.field / 2)
    }

    private func clamped(_ center: CGPoint, size: CGSize) -> CGPoint {
        let x = Self.margin + size.width / 2
        let y = Self.margin + size.height / 2
        return CGPoint(x: min(max(center.x, x), Self.field - x), y: min(max(center.y, y), Self.field - y))
    }

    private func rect(center: CGPoint, size: CGSize) -> CGRect {
        CGRect(x: center.x - size.width / 2, y: center.y - size.height / 2, width: size.width, height: size.height)
    }

    private func useLayout() {
        let scale: CGFloat = 10
        let side = Self.field * scale
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        let image = UIGraphicsImageRenderer(size: CGSize(width: side, height: side), format: format).image { _ in
            for item in items {
                let box = rect(center: item.center, size: item.size)
                item.image.draw(in: box.applying(CGAffineTransform(scaleX: scale, y: scale)))
            }
        }
        onUse(image, style, Set(items.flatMap(\.colors)).count, items)
        dismiss()
    }
}
