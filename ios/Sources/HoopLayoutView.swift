import SwiftUI
import UIKit

struct HoopLayoutItem: Identifiable, Equatable {
    let id = UUID()
    let title: String
    let image: UIImage
    let colors: Set<UIColor>
    let isLetter: Bool
    var center: CGPoint
    var size: CGSize
}

struct HoopLayoutView: View {
    private static let field = CGFloat(Hoop.fourByFour.sizeMM.w)
    private static let margin: CGFloat = 4
    private static let maximumSize = field - 2 * margin

    private struct LayoutState {
        let items: [HoopLayoutItem]
        let selected: UUID?
        let lettersSelected: Bool
        let style: LetteringStitchStyle
    }

    let pcConfigured: Bool
    let onUse: (UIImage, LetteringStitchStyle, Int, [HoopLayoutItem]) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var items: [HoopLayoutItem]
    @State private var selected: UUID?
    @State private var lettersSelected = false
    @State private var undoHistory: [LayoutState] = []
    @State private var moveStart: LayoutState?
    @State private var resizeStart: LayoutState?
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
                    Button("Select all letters") {
                        lettersSelected = true
                        selected = nil
                    }
                    .disabled(!items.contains(where: \.isLetter))

                    GeometryReader { geometry in
                        let side = geometry.size.width
                        canvas(side: side)
                    }
                    .aspectRatio(1, contentMode: .fit)

                    if !selectedItems(in: layoutState).isEmpty {
                        itemControls
                    } else {
                        Text(items.isEmpty ? "Add text or a motif to start." : "Tap or drag an item to select it.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                    Picker("Stitch style", selection: Binding(get: { style }, set: { newStyle in
                        let before = layoutState
                        style = newStyle
                        recordUndo(from: before)
                    })) {
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
                ToolbarItem(placement: .primaryAction) {
                    Button(action: undo) { Label("Undo", systemImage: "arrow.uturn.backward") }
                        .keyboardShortcut("z", modifiers: .command)
                        .disabled(undoHistory.isEmpty)
                }
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
                    let before = layoutState
                    insert(image: image, title: draft.motif, colors: [draft.motifColor], height: 21)
                    recordUndo(from: before)
                }
            }
        }
    }

    private func canvas(side: CGFloat) -> some View {
        let scale = side / Self.field
        return ZStack(alignment: .topLeading) {
            Rectangle().fill(Color(red: 0.92, green: 0.94, blue: 0.96))
                .onTapGesture { select(nil) }
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
                    .padding(5)
                    .contentShape(Rectangle())
                    .position(x: item.center.x * scale, y: item.center.y * scale)
                    .highPriorityGesture(DragGesture(minimumDistance: 0, coordinateSpace: .named("hoopLayoutCanvas"))
                        .onChanged { value in
                            guard let index = items.firstIndex(where: { $0.id == item.id }), scale > 0 else { return }
                            if moveStart == nil {
                                if !lettersSelected || !items[index].isLetter { select(item.id) }
                                moveStart = layoutState
                            }
                            if let before = moveStart {
                                moveSelection(from: before, dx: value.translation.width / scale,
                                              dy: value.translation.height / scale)
                            }
                        }
                        .onEnded { _ in
                            if let before = moveStart { recordUndo(from: before) }
                            moveStart = nil
                        })
                    .accessibilityLabel(item.title)
                    .accessibilityAddTraits(.isButton)
                    .accessibilityAction { select(item.id) }
            }
            let selection = selectedItems(in: layoutState)
            if !selection.isEmpty {
                let box = bounds(of: selection)
                Rectangle().strokeBorder(Color.blue, lineWidth: 2)
                    .frame(width: box.width * scale, height: box.height * scale)
                    .position(x: box.midX * scale, y: box.midY * scale)
                    .allowsHitTesting(false)
            }
        }
        .frame(width: side, height: side)
        .coordinateSpace(name: "hoopLayoutCanvas")
        .clipped()
        .overlay(Rectangle().strokeBorder(Color.blue.opacity(0.35)).allowsHitTesting(false))
    }

    private var itemControls: some View {
        let item = items.first { $0.id == selected }
        return VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(lettersSelected ? "All letters" : item?.title ?? "").font(.headline)
                Spacer()
                if let item {
                    Button {
                        let before = layoutState
                        insert(image: item.image, title: item.title, colors: item.colors, size: item.size,
                               isLetter: item.isLetter)
                        recordUndo(from: before)
                    } label: {
                        Image(systemName: "plus.square.on.square").accessibilityLabel("Duplicate item")
                    }
                    .buttonStyle(.bordered)
                    Button(role: .destructive) {
                        let before = layoutState
                        items.removeAll { $0.id == item.id }
                        select(nil)
                        recordUndo(from: before)
                    } label: {
                        Image(systemName: "trash").accessibilityLabel("Remove item")
                    }
                    .buttonStyle(.bordered)
                }
            }
            dimension("Width", sizeKey: \.width, centerKey: \.x)
            dimension("Height", sizeKey: \.height, centerKey: \.y)
        }
        .padding(12)
        .background(Color(.secondarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 14))
    }

    private func dimension(_ title: String, sizeKey: WritableKeyPath<CGSize, CGFloat>,
                           centerKey: WritableKeyPath<CGPoint, CGFloat>) -> some View {
        let current = selectedItems(in: layoutState)
        let extent = bounds(of: current).size[keyPath: sizeKey]
        let smallest = current.map { $0.size[keyPath: sizeKey] }.min() ?? 2
        let minimum = min(Self.maximumSize, max(2, 2 * extent / smallest))
        let value = Binding<Double>(
            get: { Double(bounds(of: selectedItems(in: layoutState)).size[keyPath: sizeKey]) },
            set: { value in
                let before = resizeStart == nil ? layoutState : nil
                resizeSelection(to: CGFloat(value), sizeKey: sizeKey, centerKey: centerKey)
                if let before { recordUndo(from: before) }
            }
        )
        return HStack {
            Text(title).frame(width: 48, alignment: .leading)
            Slider(value: value, in: Double(min(minimum, Self.maximumSize - 0.001))...Double(Self.maximumSize),
                   onEditingChanged: { editing in
                if editing {
                    resizeStart = layoutState
                } else if let before = resizeStart {
                    resizeStart = nil
                    recordUndo(from: before)
                }
            }) { Text(title) }
            .disabled(minimum >= Self.maximumSize)
            Text(String(format: "%.2f in", value.wrappedValue / 25.4)).monospacedDigit()
        }
        .font(.footnote)
    }

    private func addLettering(_ draft: LetteringDraft) {
        let before = layoutState
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
                       height: height, isLetter: true)
            }
        }
        recordUndo(from: before)
    }

    private var layoutState: LayoutState {
        LayoutState(items: items, selected: selected, lettersSelected: lettersSelected, style: style)
    }

    private func recordUndo(from before: LayoutState) {
        if items != before.items || style != before.style { undoHistory.append(before) }
    }

    private func undo() {
        guard let before = undoHistory.popLast() else { return }
        moveStart = nil
        resizeStart = nil
        items = before.items
        selected = before.selected
        lettersSelected = before.lettersSelected
        style = before.style
    }

    private func select(_ item: UUID?) {
        lettersSelected = false
        selected = item
    }

    private func selectedItems(in state: LayoutState) -> [HoopLayoutItem] {
        state.items.filter { state.lettersSelected ? $0.isLetter : $0.id == state.selected }
    }

    private func bounds(of selection: [HoopLayoutItem]) -> CGRect {
        selection.reduce(CGRect.null) { $0.union(rect(center: $1.center, size: $1.size)) }
    }

    private func moveSelection(from before: LayoutState, dx: CGFloat, dy: CGFloat) {
        let originals = selectedItems(in: before)
        let box = bounds(of: originals)
        let x = dx == 0 ? 0 : min(max(dx, Self.margin - box.minX), Self.field - Self.margin - box.maxX)
        let y = dy == 0 ? 0 : min(max(dy, Self.margin - box.minY), Self.field - Self.margin - box.maxY)
        for original in originals {
            guard let index = items.firstIndex(where: { $0.id == original.id }) else { continue }
            items[index].center = CGPoint(x: original.center.x + x, y: original.center.y + y)
        }
    }

    private func resizeSelection(to size: CGFloat, sizeKey: WritableKeyPath<CGSize, CGFloat>,
                                 centerKey: WritableKeyPath<CGPoint, CGFloat>) {
        let originals = selectedItems(in: layoutState)
        guard !originals.isEmpty else { return }
        let box = bounds(of: originals)
        let extent = box.size[keyPath: sizeKey]
        guard abs(size - extent) > 0.000_001 else { return }
        let center = CGPoint(x: box.midX, y: box.midY)[keyPath: centerKey]
        let newCenter = min(max(center, Self.margin + size / 2), Self.field - Self.margin - size / 2)
        let factor = size / extent
        for original in originals {
            guard let index = items.firstIndex(where: { $0.id == original.id }) else { continue }
            items[index].center[keyPath: centerKey] = newCenter + (original.center[keyPath: centerKey] - center) * factor
            items[index].size[keyPath: sizeKey] = original.size[keyPath: sizeKey] * factor
        }
    }

    private func insert(image: UIImage, title: String, colors: Set<UIColor>, height: CGFloat, isLetter: Bool = false) {
        let width = height * image.size.width / image.size.height
        let scale = min(1, Self.maximumSize / max(width, height))
        insert(image: image, title: title, colors: colors,
               size: CGSize(width: max(2, width * scale), height: max(2, height * scale)), isLetter: isLetter)
    }

    private func insert(image: UIImage, title: String, colors: Set<UIColor>, size: CGSize, isLetter: Bool) {
        let center = nextPosition(size: size)
        let item = HoopLayoutItem(title: title, image: image, colors: colors, isLetter: isLetter,
                                  center: center, size: size)
        items.append(item)
        select(item.id)
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
