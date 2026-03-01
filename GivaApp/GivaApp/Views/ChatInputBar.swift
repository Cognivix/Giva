// ChatInputBar.swift - Reusable multiline chat input with send/stop buttons.
//
// Used by ChatView, TaskChatView, TaskDetailView, and GoalsWindowView.
// Wraps GrowingTextInput with consistent send/stop button behavior.
// Voice input is NOT included — ChatView adds its own voice buttons alongside this.

import SwiftUI

// MARK: - Chat Input Bar

/// Reusable chat input bar: multiline text field + send/stop buttons.
///
/// Parameters:
/// - `text`: Binding to the current input text.
/// - `placeholder`: Placeholder text shown when empty.
/// - `isDisabled`: Disables the text field (e.g. chat not enabled).
/// - `isStreaming`: When true, shows stop button instead of send.
/// - `onSubmit`: Called when user presses Enter or taps send.
/// - `onStop`: Called when user taps stop button during streaming.
struct ChatInputBar: View {
    @Binding var text: String
    var placeholder: String = "Ask Giva..."
    var isDisabled: Bool = false
    var isStreaming: Bool = false
    var onSubmit: () -> Void
    var onStop: (() -> Void)? = nil
    @FocusState private var isFocused: Bool

    var body: some View {
        HStack(spacing: 8) {
            GrowingTextInput(
                text: $text,
                placeholder: placeholder,
                isFocused: $isFocused,
                isDisabled: isDisabled,
                onSubmit: onSubmit
            )

            if isStreaming {
                Button(action: { onStop?() }) {
                    Image(systemName: "stop.circle.fill")
                        .font(.system(size: 18))
                        .foregroundColor(.red)
                }
                .buttonStyle(.plain)
                .help("Stop generating")
            } else {
                Button(action: onSubmit) {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.system(size: 18))
                        .foregroundColor(.accentColor)
                }
                .buttonStyle(.plain)
                .disabled(text.trimmingCharacters(in: .whitespaces).isEmpty)
                .help("Send message")
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
    }
}

// MARK: - Growing Text Input

/// A text input that grows with its content and scrolls beyond a maximum height.
/// Uses `TextEditor` for proper multiline editing + Enter-to-send / Shift+Enter for newline.
struct GrowingTextInput: View {
    @Binding var text: String
    let placeholder: String
    var isFocused: FocusState<Bool>.Binding
    let isDisabled: Bool
    let onSubmit: () -> Void

    /// Approximate line height for 13pt system font.
    private let lineHeight: CGFloat = 18
    /// Minimum input height (single line + padding).
    private let minHeight: CGFloat = 28
    /// Maximum input height before scrolling (~10 lines).
    private let maxHeight: CGFloat = 200

    var body: some View {
        ZStack(alignment: .topLeading) {
            // Placeholder
            if text.isEmpty {
                Text(placeholder)
                    .font(.system(size: 13))
                    .foregroundColor(.secondary.opacity(0.5))
                    .padding(.horizontal, 5)
                    .padding(.top, 4)
                    .allowsHitTesting(false)
            }

            TextEditor(text: $text)
                .font(.system(size: 13))
                .scrollContentBackground(.hidden)
                .focused(isFocused)
                .disabled(isDisabled)
                .frame(
                    minHeight: minHeight,
                    maxHeight: maxHeight
                )
                .fixedSize(horizontal: false, vertical: true)
                .onKeyPress(.return, phases: .down) { press in
                    if press.modifiers.isEmpty {
                        // Enter alone → send
                        onSubmit()
                        return .handled
                    }
                    // Shift+Enter, Option+Enter, etc. → insert newline (default)
                    return .ignored
                }
        }
    }
}
