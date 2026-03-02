// ChatView.swift - Message list with streaming text and input field.
// Assistant messages render Markdown (headers, bold, italic, code, links, lists).
//
// Uses shared ChatMessageList for the message area. Manages its own input
// layout because of voice recording buttons (unique to main chat).

import SwiftUI

struct ChatView: View {
    @Environment(GivaViewModel.self) private var viewModel
    @FocusState private var isInputFocused: Bool

    var body: some View {
        @Bindable var viewModel = viewModel

        VStack(spacing: 0) {
            // Message list — fill available space so input stays pinned at bottom
            ChatMessageList(
                messages: viewModel.messages,
                isLoadingModel: viewModel.isLoadingModel,
                emptyIcon: viewModel.serverPhase == .syncing
                    ? "arrow.triangle.2.circlepath" : "bubble.left.and.text.bubble.right",
                emptyTitle: viewModel.serverPhase == .syncing
                    ? "Syncing your data...\nYou'll be able to chat once setup completes."
                    : "Ask Giva anything about your emails,\ncalendar, or tasks.",
                pendingConfirmation: viewModel.pendingConfirmation,
                onApproveAgent: { viewModel.approveAgent(jobId: $0) },
                onDismissAgent: { viewModel.dismissAgent(jobId: $0) }
            )
            .layoutPriority(1)

            Divider()

            // Input field — custom layout for voice button support
            HStack(spacing: 8) {
                if viewModel.isRecording, let voice = viewModel.voiceService {
                    // Recording indicator: red dot + animated level bars + transcript
                    HStack(spacing: 3) {
                        Circle()
                            .fill(.red)
                            .frame(width: 8, height: 8)

                        // 5 animated level bars
                        ForEach(0..<5, id: \.self) { i in
                            RoundedRectangle(cornerRadius: 1)
                                .fill(.red.opacity(0.7))
                                .frame(
                                    width: 3,
                                    height: audioBarHeight(index: i, level: voice.audioLevel)
                                )
                                .animation(.easeInOut(duration: 0.1), value: voice.audioLevel)
                        }

                        // Progressive transcript
                        if !voice.currentTranscription.isEmpty {
                            Text(voice.currentTranscription)
                                .font(.caption2)
                                .foregroundColor(.secondary)
                                .lineLimit(1)
                                .truncationMode(.head)
                        } else if voice.state == .finishing {
                            HStack(spacing: 3) {
                                ProgressView()
                                    .controlSize(.small)
                                Text("Processing...")
                                    .font(.caption2)
                                    .foregroundColor(.secondary)
                            }
                        } else {
                            Text("Listening...")
                                .font(.caption2)
                                .foregroundColor(.secondary)
                        }

                        Spacer()

                        // Cancel button
                        Button(action: { viewModel.cancelVoiceInput() }) {
                            Image(systemName: "xmark.circle.fill")
                                .font(.system(size: 14))
                                .foregroundColor(.secondary)
                        }
                        .buttonStyle(.plain)
                        .help("Cancel recording")
                    }

                    // Dictate mode: show send button so user can send early
                    if viewModel.voiceMode == .dictate {
                        Button(action: { viewModel.sendMessage() }) {
                            Image(systemName: "arrow.up.circle.fill")
                                .font(.system(size: 18))
                                .foregroundColor(.accentColor)
                        }
                        .buttonStyle(.plain)
                        .disabled(voice.currentTranscription.trimmingCharacters(in: .whitespaces).isEmpty)
                        .help("Send message")
                    }
                } else {
                    // Text input — grows with content, scrolls beyond max height
                    GrowingTextInput(
                        text: $viewModel.currentInput,
                        placeholder: viewModel.isOnboarding ? "Answer..." : "Ask Giva...",
                        isFocused: $isInputFocused,
                        isDisabled: !viewModel.isChatEnabled || viewModel.isStreaming,
                        onSubmit: { viewModel.sendMessage() }
                    )

                    if viewModel.isStreaming {
                        Button(action: { viewModel.cancelStreaming() }) {
                            Image(systemName: "stop.circle.fill")
                                .font(.system(size: 18))
                                .foregroundColor(.red)
                        }
                        .buttonStyle(.plain)
                        .help("Stop generating")
                    } else {
                        // Dictate button (mic)
                        Button(action: { viewModel.startVoiceInput(mode: .dictate) }) {
                            Image(systemName: "mic.fill")
                                .font(.system(size: 16))
                                .foregroundColor(.secondary)
                        }
                        .buttonStyle(.plain)
                        .help("Dictate — transcribe to text field")

                        // Full voice button (waveform)
                        Button(action: { viewModel.startVoiceInput(mode: .fullVoice) }) {
                            Image(systemName: "waveform")
                                .font(.system(size: 16))
                                .foregroundColor(.secondary)
                        }
                        .buttonStyle(.plain)
                        .help("Voice mode — auto-send with voice response")

                        // Send button
                        Button(action: { viewModel.sendMessage() }) {
                            Image(systemName: "arrow.up.circle.fill")
                                .font(.system(size: 18))
                                .foregroundColor(.accentColor)
                        }
                        .buttonStyle(.plain)
                        .disabled(viewModel.currentInput.trimmingCharacters(in: .whitespaces).isEmpty)
                        .help("Send message")
                    }
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
        }
        .onAppear {
            isInputFocused = true
        }
    }
}

// MARK: - Audio Level Bar Helper

/// Compute the height of an individual level bar for the recording indicator.
/// Each bar has a slightly different base offset to create a visual wave effect.
private func audioBarHeight(index: Int, level: Float) -> CGFloat {
    let baseHeight: CGFloat = 4
    let maxHeight: CGFloat = 16
    let offset = Float(index) * 0.15  // stagger bars
    let adjusted = min(1.0, max(0.0, level + offset - 0.1))
    return baseHeight + CGFloat(adjusted) * (maxHeight - baseHeight)
}

// MARK: - Message Bubble

struct MessageBubble: View {
    let message: ChatMessage
    var isLoadingModel: Bool = false
    @State private var showThinking = false

    /// Internal markers stripped before display.
    private static let internalMarkers = ["[NEEDS_AGENT]"]

    /// Message content with internal markers stripped.
    private var displayContent: String {
        var text = message.content
        for marker in Self.internalMarkers {
            text = text.replacingOccurrences(of: marker, with: "")
        }
        return text.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    @ViewBuilder
    var body: some View {
        // System messages render as subtle inline text
        if message.role == "system" {
            HStack(spacing: 6) {
                Text(message.content)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .italic()
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 2)
        } else {

        HStack(alignment: .top) {
            if message.role == "user" {
                Spacer(minLength: 60)
            }

            VStack(alignment: message.role == "user" ? .trailing : .leading, spacing: 4) {
                // Thinking pane (assistant messages only)
                if message.role != "user" && !message.thinkingContent.isEmpty {
                    ThinkingPane(
                        content: message.thinkingContent,
                        isThinking: message.isThinking,
                        isExpanded: $showThinking
                    )
                }

                // Main content bubble
                if !displayContent.isEmpty || message.isStreaming {
                    Group {
                        if message.role == "user" {
                            // User messages: plain text
                            Text(displayContent)
                                .font(.system(size: 13))
                        } else {
                            // Assistant messages: Markdown rendering
                            MarkdownText(displayContent)
                                .font(.system(size: 13))
                        }
                    }
                    .textSelection(.enabled)
                    .lineLimit(nil)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(
                        message.role == "user"
                            ? Color.accentColor.opacity(0.15)
                            : Color(nsColor: .controlBackgroundColor)
                    )
                    .cornerRadius(10)
                }

                if message.isStreaming {
                    HStack(spacing: 4) {
                        ProgressView()
                            .controlSize(.mini)
                        if isLoadingModel {
                            Text("Loading AI model...")
                                .font(.caption2)
                                .foregroundColor(.secondary)
                        } else if message.isThinking {
                            Text("Thinking...")
                                .font(.caption2)
                                .foregroundColor(.secondary)
                        } else {
                            Text("Generating...")
                                .font(.caption2)
                                .foregroundColor(.secondary)
                        }
                    }
                }
            }

            if message.role != "user" {
                Spacer(minLength: 40)
            }
        }

        } // end else (non-system)
    }
}

// MARK: - Markdown Text

/// Renders Markdown content with block-level visual styling.
///
/// Parses markdown into blocks (headers, paragraphs, lists, code blocks, blockquotes)
/// and renders each with appropriate typography. Inline formatting (bold, italic, code,
/// links) uses `AttributedString`. Falls back gracefully during streaming when markdown
/// syntax is incomplete.
struct MarkdownText: View {
    let source: String

    init(_ source: String) {
        self.source = source
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            let blocks = Self.parseBlocks(source)
            ForEach(Array(blocks.enumerated()), id: \.offset) { _, block in
                blockView(block)
            }
        }
    }

    // MARK: - Block Types

    private enum Block {
        case heading(level: Int, text: String)
        case paragraph(text: String)
        case bulletItem(text: String)
        case numberedItem(number: String, text: String)
        case codeBlock(lines: [String])
        case blockquote(text: String)
        case table(headers: [String], rows: [[String]])
        case divider
    }

    // MARK: - Block Rendering

    @ViewBuilder
    private func blockView(_ block: Block) -> some View {
        switch block {
        case .heading(let level, let text):
            Self.inlineMarkdown(text)
                .font(Self.headingFont(level))
                .fontWeight(.semibold)
                .padding(.top, level == 1 ? 6 : 3)

        case .paragraph(let text):
            Self.inlineMarkdown(text)

        case .bulletItem(let text):
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text("•")
                    .foregroundColor(.secondary)
                Self.inlineMarkdown(text)
            }
            .padding(.leading, 8)

        case .numberedItem(let number, let text):
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Text(number)
                    .foregroundColor(.secondary)
                    .monospacedDigit()
                Self.inlineMarkdown(text)
            }
            .padding(.leading, 8)

        case .codeBlock(let lines):
            Text(lines.joined(separator: "\n"))
                .font(.system(size: 12, design: .monospaced))
                .padding(8)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color(nsColor: .textBackgroundColor).opacity(0.5))
                .cornerRadius(6)
                .textSelection(.enabled)

        case .blockquote(let text):
            HStack(spacing: 8) {
                RoundedRectangle(cornerRadius: 1)
                    .fill(Color.accentColor.opacity(0.5))
                    .frame(width: 3)
                Self.inlineMarkdown(text)
                    .foregroundColor(.secondary)
            }
            .padding(.leading, 4)

        case .table(let headers, let rows):
            VStack(alignment: .leading, spacing: 0) {
                // Header row
                HStack(spacing: 0) {
                    ForEach(Array(headers.enumerated()), id: \.offset) { _, header in
                        Self.inlineMarkdown(header)
                            .font(.system(size: 12, weight: .semibold))
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 4)
                    }
                }
                .background(Color.secondary.opacity(0.08))

                Divider()

                // Data rows
                ForEach(Array(rows.enumerated()), id: \.offset) { rowIdx, row in
                    HStack(spacing: 0) {
                        ForEach(Array(row.enumerated()), id: \.offset) { _, cell in
                            Self.inlineMarkdown(cell)
                                .font(.system(size: 12))
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(.horizontal, 6)
                                .padding(.vertical, 3)
                        }
                    }
                    if rowIdx < rows.count - 1 {
                        Divider().opacity(0.5)
                    }
                }
            }
            .background(Color(nsColor: .textBackgroundColor).opacity(0.3))
            .cornerRadius(6)
            .padding(.vertical, 2)

        case .divider:
            Divider()
                .padding(.vertical, 2)
        }
    }

    // MARK: - Inline Markdown (bold, italic, code, links)

    private static func inlineMarkdown(_ text: String) -> Text {
        let options = AttributedString.MarkdownParsingOptions(
            interpretedSyntax: .inlineOnlyPreservingWhitespace
        )
        if let attributed = try? AttributedString(markdown: text, options: options) {
            return Text(attributed)
        }
        return Text(text)
    }

    private static func headingFont(_ level: Int) -> Font {
        switch level {
        case 1: return .system(size: 18, weight: .bold)
        case 2: return .system(size: 16, weight: .semibold)
        case 3: return .system(size: 14, weight: .semibold)
        default: return .system(size: 13, weight: .semibold)
        }
    }

    // MARK: - Table Helpers

    private static func isTableSeparator(_ line: String) -> Bool {
        let stripped = line.replacingOccurrences(of: " ", with: "")
        guard stripped.contains("|"), stripped.contains("-") else { return false }
        let cells = stripped.split(separator: "|", omittingEmptySubsequences: true)
        return !cells.isEmpty && cells.allSatisfy { cell in
            cell.allSatisfy { $0 == "-" || $0 == ":" }
        }
    }

    private static func parseTableRow(_ line: String) -> [String] {
        let parts = line.split(separator: "|", omittingEmptySubsequences: false)
            .map { String($0).trimmingCharacters(in: .whitespaces) }
        var result = Array(parts)
        if result.first?.isEmpty == true { result.removeFirst() }
        if result.last?.isEmpty == true { result.removeLast() }
        return result
    }

    // MARK: - Block Parser

    /// Parse markdown source into an array of typed blocks.
    ///
    /// Handles: headings (`#`–`####`), bullet lists (`- `, `* `), numbered lists (`1. `),
    /// fenced code blocks (``` ``` ```), blockquotes (`> `), horizontal rules (`---`),
    /// and paragraph text. Consecutive non-block lines merge into a single paragraph.
    private static func parseBlocks(_ text: String) -> [Block] {
        var blocks: [Block] = []
        // Pre-process: convert common HTML tags to markdown equivalents
        let cleaned = text
            .replacingOccurrences(of: "<br\\s*/?>", with: "\n", options: .regularExpression)
            .replacingOccurrences(of: "</?p>", with: "\n", options: .regularExpression)
        let lines = cleaned.components(separatedBy: "\n")
        var index = 0
        var paragraphBuffer: [String] = []

        func flushParagraph() {
            if !paragraphBuffer.isEmpty {
                let merged = paragraphBuffer.joined(separator: "\n")
                    .trimmingCharacters(in: .whitespacesAndNewlines)
                if !merged.isEmpty {
                    blocks.append(.paragraph(text: merged))
                }
                paragraphBuffer = []
            }
        }

        while index < lines.count {
            let line = lines[index]
            let trimmed = line.trimmingCharacters(in: .whitespaces)

            // Empty line → flush paragraph
            if trimmed.isEmpty {
                flushParagraph()
                index += 1
                continue
            }

            // Fenced code block
            if trimmed.hasPrefix("```") {
                flushParagraph()
                index += 1
                var codeLines: [String] = []
                while index < lines.count {
                    let codeLine = lines[index]
                    if codeLine.trimmingCharacters(in: .whitespaces).hasPrefix("```") {
                        index += 1
                        break
                    }
                    codeLines.append(codeLine)
                    index += 1
                }
                blocks.append(.codeBlock(lines: codeLines))
                continue
            }

            // Horizontal rule
            if trimmed == "---" || trimmed == "***" || trimmed == "___" {
                flushParagraph()
                blocks.append(.divider)
                index += 1
                continue
            }

            // Markdown table (header row + separator row + data rows)
            if trimmed.contains("|") {
                let nextIndex = index + 1
                if nextIndex < lines.count {
                    let nextTrimmed = lines[nextIndex]
                        .trimmingCharacters(in: .whitespaces)
                    if Self.isTableSeparator(nextTrimmed) {
                        flushParagraph()
                        let headers = Self.parseTableRow(trimmed)
                        var dataRows: [[String]] = []
                        var rowIndex = nextIndex + 1
                        while rowIndex < lines.count {
                            let rowLine = lines[rowIndex]
                                .trimmingCharacters(in: .whitespaces)
                            guard rowLine.contains("|"), !rowLine.isEmpty else { break }
                            dataRows.append(Self.parseTableRow(rowLine))
                            rowIndex += 1
                        }
                        blocks.append(.table(headers: headers, rows: dataRows))
                        index = rowIndex
                        continue
                    }
                }
            }

            // Headings
            if let match = trimmed.prefixMatch(of: /^(#{1,4})\s+(.+)/) {
                flushParagraph()
                let level = match.1.count
                let content = String(match.2)
                blocks.append(.heading(level: level, text: content))
                index += 1
                continue
            }

            // Bullet list item
            if let match = trimmed.prefixMatch(of: /^[-*+]\s+(.+)/) {
                flushParagraph()
                blocks.append(.bulletItem(text: String(match.1)))
                index += 1
                continue
            }

            // Numbered list item
            if let match = trimmed.prefixMatch(of: /^(\d+[.)]\s+)(.+)/) {
                flushParagraph()
                blocks.append(.numberedItem(number: String(match.1).trimmingCharacters(in: .whitespaces),
                                            text: String(match.2)))
                index += 1
                continue
            }

            // Blockquote
            if trimmed.hasPrefix("> ") {
                flushParagraph()
                let content = String(trimmed.dropFirst(2))
                blocks.append(.blockquote(text: content))
                index += 1
                continue
            }

            // Regular text → accumulate into paragraph
            paragraphBuffer.append(trimmed)
            index += 1
        }

        flushParagraph()
        return blocks
    }
}

// MARK: - Thinking Pane

struct ThinkingPane: View {
    let content: String
    let isThinking: Bool
    @Binding var isExpanded: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Toggle header
            Button(action: { withAnimation(.easeInOut(duration: 0.2)) { isExpanded.toggle() } }) {
                HStack(spacing: 4) {
                    Image(systemName: isExpanded ? "chevron.down" : "chevron.right")
                        .font(.system(size: 9, weight: .semibold))
                        .foregroundColor(.secondary)
                        .frame(width: 12)

                    Image(systemName: "brain")
                        .font(.system(size: 10))
                        .foregroundColor(.purple.opacity(0.8))

                    Text(isThinking ? "Thinking..." : "Thought process")
                        .font(.system(size: 11, weight: .medium))
                        .foregroundColor(.secondary)

                    if isThinking {
                        ProgressView()
                            .controlSize(.mini)
                            .scaleEffect(0.7)
                    }

                    Spacer()
                }
                .padding(.horizontal, 8)
                .padding(.vertical, 5)
            }
            .buttonStyle(.plain)

            // Expandable content
            if isExpanded {
                Text(content)
                    .font(.system(size: 11))
                    .foregroundColor(.secondary)
                    .textSelection(.enabled)
                    .lineLimit(nil)
                    .padding(.horizontal, 8)
                    .padding(.bottom, 6)
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
        .background(Color.purple.opacity(0.05), in: RoundedRectangle(cornerRadius: 8))
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(Color.purple.opacity(0.15), lineWidth: 0.5)
        )
        // Auto-expand while actively thinking
        .onChange(of: isThinking) { _, newValue in
            if newValue {
                withAnimation(.easeInOut(duration: 0.2)) { isExpanded = true }
            }
        }
        .onAppear {
            // Start expanded if currently thinking
            if isThinking { isExpanded = true }
        }
    }
}

// MARK: - Chat History View (read-only past messages)

/// Displays past messages for a specific date. Read-only — no input field.
struct ChatHistoryView: View {
    let dateString: String
    @Environment(GivaViewModel.self) private var viewModel
    @State private var messages: [ChatMessage] = []
    @State private var isLoading = true

    var body: some View {
        VStack(spacing: 0) {
            // Date header
            HStack {
                Image(systemName: "clock.arrow.circlepath")
                    .foregroundColor(.secondary)
                Text(displayDate)
                    .font(.headline)
                Spacer()
                Text("\(messages.count) messages")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .background(Color.secondary.opacity(0.05))

            Divider()

            if isLoading {
                ProgressView("Loading messages...")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if messages.isEmpty {
                ContentUnavailableView(
                    "No Messages",
                    systemImage: "bubble.left",
                    description: Text("No conversation messages found for this date.")
                )
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 8) {
                        ForEach(messages) { message in
                            MessageBubble(message: message)
                                .id(message.id)
                        }
                    }
                    .padding(12)
                }
            }
        }
        .task {
            isLoading = true
            guard let api = viewModel.apiService else {
                isLoading = false
                return
            }
            do {
                let response = try await api.getConversationMessages(date: dateString)
                messages = ChatMessage.fromHistory(
                    response.messages.map { (role: $0.role, content: $0.content, type: $0.type) }
                )
            } catch {
                // Non-critical
            }
            isLoading = false
        }
    }

    private var displayDate: String {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        guard let date = formatter.date(from: dateString) else { return dateString }

        if Calendar.current.isDateInToday(date) { return "Today" }
        if Calendar.current.isDateInYesterday(date) { return "Yesterday" }

        let display = DateFormatter()
        display.dateStyle = .long
        return display.string(from: date)
    }
}

// MARK: - Previews

#Preview("Chat — Empty") {
    let vm = GivaViewModel()
    ChatView()
        .environment(vm)
        .frame(width: 600, height: 500)
}

#Preview("Chat — With Messages") {
    let vm = GivaViewModel()
    vm.messages = [
        ChatMessage(role: "assistant", content: "Hi there! I'm Giva — your AI assistant."),
        ChatMessage(role: "user", content: "What meetings do I have today?"),
        ChatMessage(role: "assistant", content: "You have 3 meetings today:\n\n1. **Standup** at 9:00 AM\n2. **Design Review** at 11:30 AM\n3. **1:1 with Sarah** at 2:00 PM"),
    ]
    vm.serverPhase = .operational
    return ChatView()
        .environment(vm)
        .frame(width: 600, height: 500)
}
