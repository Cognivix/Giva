// BootstrapView.swift - Setup progress screen.
//
// Renders state from BootstrapManager, which mirrors the server's bootstrap
// state machine.  Covers both the initial setup script (pre-server) and
// the server-driven bootstrap (model downloads, config, validation).

import SwiftUI

struct BootstrapView: View {
    @ObservedObject var bootstrap: BootstrapManager

    @State private var dotCount = 0
    private let dotTimer = Timer.publish(every: 0.5, on: .main, in: .common).autoconnect()

    var body: some View {
        VStack(spacing: 0) {
            Spacer()

            // Animated icon
            TimelineView(.animation(minimumInterval: 1.0 / 30.0)) { context in
                let angle = context.date.timeIntervalSinceReferenceDate
                    .truncatingRemainder(dividingBy: 2) / 2 * 360
                ZStack {
                    Circle()
                        .stroke(
                            AngularGradient(
                                gradient: Gradient(colors: [.accentColor.opacity(0.1), .accentColor]),
                                center: .center
                            ),
                            lineWidth: 3
                        )
                        .frame(width: 70, height: 70)
                        .rotationEffect(.degrees(angle))

                    Image(systemName: phaseIcon)
                        .font(.system(size: 28))
                        .foregroundColor(.accentColor)
                }
            }
            .frame(width: 76, height: 76)
            .padding(.bottom, 24)

            // Title
            Text(phaseTitle)
                .font(.system(size: 16, weight: .semibold))
                .foregroundColor(.primary)
                .padding(.bottom, 4)

            // Subtitle
            Text(bootstrap.displayMessage)
                .font(.system(size: 12))
                .foregroundColor(.secondary)
                .padding(.bottom, 12)

            // Download progress (if downloading)
            if !bootstrap.downloadProgress.isEmpty {
                downloadProgressSection
                    .padding(.horizontal, 24)
                    .padding(.bottom, 12)
            }

            // Error display
            if let error = bootstrap.errorMessage {
                VStack(spacing: 8) {
                    Text(error)
                        .font(.system(size: 11))
                        .foregroundColor(.red)
                        .multilineTextAlignment(.center)
                        .lineLimit(4)
                        .padding(.horizontal, 24)

                    Button("Retry") {
                        Task { await bootstrap.retry() }
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.small)
                }
                .padding(.bottom, 16)
            }

            // Log output (setup script phase)
            if !bootstrap.logLines.isEmpty {
                ScrollViewReader { proxy in
                    ScrollView {
                        VStack(alignment: .leading, spacing: 2) {
                            ForEach(Array(bootstrap.logLines.enumerated()), id: \.offset) { i, line in
                                Text(line)
                                    .font(.system(size: 10, design: .monospaced))
                                    .foregroundColor(.secondary)
                                    .id(i)
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(8)
                    }
                    .frame(maxHeight: 100)
                    .background(Color(nsColor: .controlBackgroundColor))
                    .cornerRadius(6)
                    .padding(.horizontal, 24)
                    .onChange(of: bootstrap.logLines.count) { _, _ in
                        if let last = bootstrap.logLines.indices.last {
                            proxy.scrollTo(last, anchor: .bottom)
                        }
                    }
                }
            }

            Spacer()

            // Footer
            HStack {
                Text("This only happens once")
                    .font(.system(size: 10))
                    .foregroundColor(.secondary.opacity(0.6))

                Spacer()

                Button("Quit") {
                    NSApplication.shared.terminate(nil)
                }
                .buttonStyle(.plain)
                .font(.system(size: 10))
                .foregroundColor(.secondary)
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 12)
        }
        .frame(width: 420, height: 520)
        .onReceive(dotTimer) { _ in
            dotCount = (dotCount + 1) % 4
        }
    }

    // MARK: - Download Progress

    private var downloadProgressSection: some View {
        VStack(spacing: 8) {
            ForEach(Array(bootstrap.downloadProgress.keys.sorted()), id: \.self) { modelId in
                if let progress = bootstrap.downloadProgress[modelId] {
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Text(modelId.replacingOccurrences(of: "mlx-community/", with: ""))
                                .font(.caption)
                            Spacer()
                            if progress.percent < 0 {
                                if let mb = progress.downloadedMb {
                                    Text("\(Int(mb)) MB")
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                            } else if progress.percent >= 100 {
                                Text("Done")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            } else {
                                Text("\(Int(progress.percent))%")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        if progress.percent < 0 {
                            ProgressView()
                                .controlSize(.small)
                        } else {
                            ProgressView(value: min(progress.percent, 100), total: 100)
                                .tint(progress.percent >= 100 ? .green : .blue)
                        }
                    }
                }
            }
        }
        .padding()
        .background(.fill.tertiary, in: RoundedRectangle(cornerRadius: 10))
    }

    // MARK: - Phase Display

    private var phaseTitle: String {
        if bootstrap.isReady {
            return "All set!"
        }
        if bootstrap.errorMessage != nil {
            return "Oops"
        }
        return "Cooking" + String(repeating: ".", count: dotCount)
    }

    private var phaseIcon: String {
        if bootstrap.isReady { return "checkmark.circle.fill" }
        if bootstrap.errorMessage != nil { return "exclamationmark.triangle.fill" }

        let state = bootstrap.serverStatus?.state ?? ""
        switch state {
        case "downloading_default_model", "downloading_user_models":
            return "arrow.down.circle"
        case "awaiting_model_selection":
            return "cpu"
        case "validating":
            return "checkmark.shield"
        case "ready":
            return "checkmark.circle.fill"
        case "failed":
            return "exclamationmark.triangle.fill"
        default:
            if bootstrap.isSettingUp {
                return "shippingbox"
            }
            return "bolt.fill"
        }
    }
}
