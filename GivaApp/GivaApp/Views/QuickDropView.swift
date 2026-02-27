// QuickDropView.swift - Quick-drop floating text field for fast prompt capture.
//
// Invoked via Option+Space. Enter submits in background (fire-and-forget),
// Cmd+Enter navigates to full UI with the prompt in the input field.

import SwiftUI

struct QuickDropView: View {
    @Environment(GivaViewModel.self) private var viewModel
    @Environment(\.openWindow) private var openWindow

    @State private var text = ""
    @FocusState private var isFocused: Bool

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                Image(systemName: "sparkle")
                    .font(.system(size: 16))
                    .foregroundColor(.accentColor)

                TextField("Ask Giva anything...", text: $text)
                    .textFieldStyle(.plain)
                    .font(.system(size: 16))
                    .focused($isFocused)
                    .onSubmit { submitBackground() }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 14)

            Divider()

            HStack(spacing: 16) {
                keyHint("\u{23CE}", "Send in background")
                keyHint("\u{2318}\u{23CE}", "Open in chat")
                keyHint("esc", "Dismiss")
                Spacer()
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 6)
            .background(Color.secondary.opacity(0.05))
        }
        .frame(width: 500)
        .onAppear { isFocused = true }
        .onExitCommand { closeWindow() }
        .background {
            // Hidden button to capture Cmd+Enter
            Button("") { submitToChat() }
                .keyboardShortcut(.return, modifiers: .command)
                .frame(width: 0, height: 0)
                .opacity(0)
        }
    }

    // MARK: - Key Hint

    private func keyHint(_ key: String, _ label: String) -> some View {
        HStack(spacing: 4) {
            Text(key)
                .font(.system(size: 10, design: .monospaced))
                .padding(.horizontal, 4)
                .padding(.vertical, 1)
                .background(RoundedRectangle(cornerRadius: 3).fill(Color.secondary.opacity(0.15)))
            Text(label)
                .font(.system(size: 10))
        }
        .foregroundColor(.secondary)
    }

    // MARK: - Actions

    private func submitBackground() {
        let prompt = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !prompt.isEmpty, viewModel.isChatEnabled else { return }
        viewModel.currentInput = prompt
        viewModel.sendMessage()
        closeWindow()
    }

    private func submitToChat() {
        let prompt = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !prompt.isEmpty else { return }
        viewModel.currentInput = prompt
        openWindow(id: "main-window")
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) {
            NSApp.activate(ignoringOtherApps: true)
        }
        closeWindow()
    }

    private func closeWindow() {
        text = ""
        for window in NSApp.windows where window.title == "Quick Drop" {
            window.close()
            return
        }
        NSApp.keyWindow?.close()
    }
}
