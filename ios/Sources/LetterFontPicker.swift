import SwiftUI
import UIKit

/// Edits font choices locally until the user applies them to the design.
struct LetterFontPicker: View {
    private static let defaultTarget = -1
    static let allLetters = -2

    let draft: LetteringDraft
    let onApply: (String, [Int: String]) -> Void

    @Environment(\.dismiss) private var dismiss
    @FocusState private var searching: Bool
    @State private var selectedLetter: Int
    @State private var baseFont: String
    @State private var letterFonts: [Int: String]
    @State private var category = "All"
    @State private var search = ""

    init(draft: LetteringDraft, selection: Int?, onApply: @escaping (String, [Int: String]) -> Void) {
        self.draft = draft
        self.onApply = onApply
        self._selectedLetter = State(initialValue: selection ?? Self.defaultTarget)
        let fallback = LetteringFont.all.first?.id ?? draft.fontName
        let available = Set(LetteringFont.all.map(\.id))
        self._baseFont = State(initialValue: available.contains(draft.fontName) ? draft.fontName : fallback)
        self._letterFonts = State(initialValue: draft.letterFonts.filter { available.contains($0.value) })
    }

    private var editingDefault: Bool { selectedLetter < 0 }
    private var activeFont: String { letterFonts[selectedLetter] ?? baseFont }
    private var fontSelection: Binding<String> {
        let target = selectedLetter
        return Binding(
            get: { target < 0 ? baseFont : letterFonts[target] ?? "" },
            set: { name in
                if target < 0 {
                    baseFont = name
                    if target == Self.allLetters { letterFonts.removeAll() }
                } else {
                    letterFonts[target] = name.isEmpty ? nil : name
                }
            }
        )
    }

    var body: some View {
        let characters = Array(draft.text)
        let sample = draft.text.trimmingCharacters(in: .whitespacesAndNewlines)
        let previewText = selectedLetter == Self.allLetters ? (sample.isEmpty ? "Aa" : sample)
            : editingDefault ? "Aa" : String(characters[selectedLetter])
        NavigationStack {
            GeometryReader { geometry in
                ScrollView {
                    VStack(spacing: 16) {
                        preview(character: previewText,
                                height: min(340, max(164, geometry.size.height * 0.44)))
                        controls(characters: characters)
                    }
                    .frame(maxWidth: 580)
                    .padding(16)
                    .frame(maxWidth: .infinity)
                }
                .scrollDismissesKeyboard(.interactively)
            }
            .background(Color(.systemGroupedBackground))
            .navigationTitle("Letter Fonts")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button(selectedLetter == Self.allLetters ? "Apply to all" : "Apply") {
                        onApply(baseFont, selectedLetter == Self.allLetters ? [:] : letterFonts)
                        dismiss()
                    }
                    .bold()
                }
                ToolbarItemGroup(placement: .keyboard) {
                    Spacer()
                    Button("Show picker") { searching = false }
                }
            }
        }
    }

    private func preview(character: String, height: CGFloat) -> some View {
        var sample = draft
        sample.text = character
        sample.letterFonts = [:]
        sample.shape = .straight
        sample.motif = ""
        let font = UIFont(name: activeFont, size: LetteringRenderer.pointSize)
            ?? UIFont.systemFont(ofSize: LetteringRenderer.pointSize)
        let image = LetteringRenderer.render(sample, font: font, motifOnly: false)
        let name = LetteringFont.all.first { $0.id == activeFont }?.name ?? font.familyName

        return VStack(spacing: 0) {
            ZStack {
                Color(red: 0.85, green: 0.89, blue: 0.94)
                if let image {
                    Image(uiImage: image)
                        .resizable()
                        .scaledToFit()
                        .frame(maxWidth: 230, maxHeight: max(108, height - 60))
                        .padding(18)
                }
            }
            .frame(height: height)
            Text(name)
                .font(.subheadline.weight(.semibold))
                .frame(maxWidth: .infinity)
                .padding(12)
                .background(Color(.secondarySystemGroupedBackground))
        }
        .clipShape(RoundedRectangle(cornerRadius: 22))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Preview of \(character) in \(name)")
    }

    private func controls(characters: [Character]) -> some View {
        let query = search.trimmingCharacters(in: .whitespacesAndNewlines)
        let matches = LetteringFont.all.filter {
            (category == "All" || $0.category == category)
                && (query.isEmpty || $0.name.localizedCaseInsensitiveContains(query))
        }
        // Keep a valid wheel selection even when a filter hides the current font.
        let current = LetteringFont.all.first { $0.id == fontSelection.wrappedValue }
        let visible = current.map { font in
            matches.contains(where: { $0.id == font.id }) ? matches : [font] + matches
        } ?? matches

        return VStack(spacing: 8) {
            HStack {
                Image(systemName: "magnifyingglass")
                    .foregroundStyle(.secondary)
                    .accessibilityHidden(true)
                TextField("Search fonts", text: $search)
                    .focused($searching)
                    .submitLabel(.search)
                    .onSubmit { searching = false }
                Picker("Font category", selection: $category) {
                    ForEach(LetteringFont.categories, id: \.self) { Text($0).tag($0) }
                }
                .pickerStyle(.menu)
            }
            .padding(.horizontal, 12)
            .frame(minHeight: 44)
            .background(Color(.tertiarySystemFill), in: RoundedRectangle(cornerRadius: 12))

            GeometryReader { geometry in
                let leftWidth = min(100, geometry.size.width * 0.32)
                HStack(spacing: 8) {
                    VStack(spacing: 0) {
                        Text("Letter").font(.caption).foregroundStyle(.secondary)
                        Picker("Letter", selection: $selectedLetter) {
                            Text("All letters").tag(Self.allLetters)
                            Text("Default").tag(Self.defaultTarget)
                            ForEach(characters.indices.filter { !characters[$0].isWhitespace }, id: \.self) { index in
                                Text(String(characters[index]))
                                    .font(.title3)
                                    .accessibilityLabel("Character \(index + 1), \(String(characters[index]))")
                                    .tag(index)
                            }
                        }
                        .pickerStyle(.wheel)
                        .frame(height: 154)
                        .clipped()
                    }
                    .frame(width: leftWidth)
                    .clipped()
                    .background(Color.accentColor.opacity(0.07), in: RoundedRectangle(cornerRadius: 12))

                    VStack(spacing: 0) {
                        Text("Font").font(.caption).foregroundStyle(.secondary)
                        Picker("Font", selection: fontSelection) {
                            if !editingDefault { Text("Use default font").tag("") }
                            ForEach(visible) { font in
                                Text(font.name)
                                    .font(.custom(font.postScript, size: 21))
                                    .lineLimit(1)
                                    .minimumScaleFactor(0.55)
                                    .tag(font.id)
                            }
                        }
                        .pickerStyle(.wheel)
                        .id(selectedLetter)
                        .frame(height: 154)
                        .clipped()
                    }
                    .frame(width: geometry.size.width - leftWidth - 8)
                    .clipped()
                }
            }
            .frame(height: 178)

            if matches.isEmpty {
                Text("No matching fonts").font(.footnote).foregroundStyle(.secondary)
            }
        }
        .padding(12)
        .background(Color(.secondarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 18))
    }
}
