// ModelSetupView.swift - Model selection and download wizard.

import SwiftUI

struct ModelSetupView: View {
    @ObservedObject var viewModel: GivaViewModel
    @State private var selectedAssistant: String = ""
    @State private var selectedFilter: String = ""
    @State private var showCustomize = false

    var body: some View {
        VStack(spacing: 0) {
            // Header
            header
                .padding()

            Divider()

            // Content
            ScrollView {
                VStack(spacing: 16) {
                    if viewModel.isSettingUpModels {
                        loadingState
                    } else if let models = viewModel.availableModels {
                        modelSelectionContent(models)
                    } else if let error = viewModel.modelSetupError {
                        errorState(error)
                    } else {
                        initialState
                    }
                }
                .padding()
            }

            Divider()

            // Footer
            footer
                .padding()
        }
        .frame(width: 420, height: 520)
        .task {
            // Only fetch if we don't already have model data (avoids
            // re-showing the spinner every time the popover reopens).
            if viewModel.availableModels == nil && !viewModel.isSettingUpModels {
                await viewModel.fetchAvailableModels()
            }
        }
    }

    // MARK: - Header

    private var header: some View {
        VStack(spacing: 8) {
            Image(systemName: "cpu")
                .font(.system(size: 32))
                .foregroundStyle(.blue)

            Text("Choose Your AI Models")
                .font(.title2.bold())

            if let hw = viewModel.availableModels?.hardware {
                Text("\(hw.chip) \u{2022} \(hw.ramGb) GB RAM")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 4)
                    .background(.fill.tertiary, in: Capsule())
            }
        }
    }

    // MARK: - States

    private var loadingState: some View {
        VStack(spacing: 12) {
            ProgressView()
                .controlSize(.large)
            Text("Discovering compatible models...")
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(.vertical, 40)
    }

    private var initialState: some View {
        VStack(spacing: 12) {
            ProgressView()
            Text("Connecting to server...")
                .foregroundStyle(.secondary)
        }
        .padding(.vertical, 40)
    }

    private func errorState(_ error: String) -> some View {
        VStack(spacing: 12) {
            Image(systemName: "exclamationmark.triangle")
                .font(.title)
                .foregroundStyle(.orange)
            Text(error)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            Button("Retry") {
                Task { await viewModel.fetchAvailableModels() }
            }
        }
        .padding(.vertical, 40)
    }

    // MARK: - Model Selection Content

    private func modelSelectionContent(_ models: AvailableModelsResponse) -> some View {
        VStack(spacing: 16) {
            // Recommendation
            recommendationCard(models.recommended, models: models)

            // Use Recommended button
            if !viewModel.isDownloadingModels {
                Button(action: {
                    selectedAssistant = models.recommended.assistant
                    selectedFilter = models.recommended.filter
                    viewModel.selectAndDownloadModels(
                        assistant: models.recommended.assistant,
                        filter: models.recommended.filter
                    )
                }) {
                    Label("Use Recommended", systemImage: "sparkles")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
            }

            // Download progress
            if viewModel.isDownloadingModels {
                downloadProgressView
            }

            // Customize section
            if !viewModel.isDownloadingModels {
                customizeSection(models)
            }
        }
        .onAppear {
            selectedAssistant = models.recommended.assistant
            selectedFilter = models.recommended.filter
        }
    }

    private func recommendationCard(_ rec: ModelRecommendation, models: AvailableModelsResponse) -> some View {
        let status = isRecommendedDownloaded(models)
        return VStack(alignment: .leading, spacing: 8) {
            Label("Recommended for your Mac", systemImage: "sparkles")
                .font(.headline)
                .foregroundStyle(.blue)

            Text(rec.reasoning)
                .font(.callout)
                .foregroundStyle(.secondary)

            Divider()

            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 4) {
                        Text("Assistant")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        if status.assistant {
                            Image(systemName: "checkmark.circle.fill")
                                .font(.caption2)
                                .foregroundStyle(.green)
                        }
                    }
                    Text(rec.assistant.replacingOccurrences(of: "mlx-community/", with: ""))
                        .font(.callout.bold())
                }
                Spacer()
                VStack(alignment: .trailing, spacing: 2) {
                    HStack(spacing: 4) {
                        Text("Filter")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        if status.filter {
                            Image(systemName: "checkmark.circle.fill")
                                .font(.caption2)
                                .foregroundStyle(.green)
                        }
                    }
                    Text(rec.filter.replacingOccurrences(of: "mlx-community/", with: ""))
                        .font(.callout.bold())
                }
            }

            if status.assistant && status.filter {
                Label("Both models already downloaded", systemImage: "checkmark.circle")
                    .font(.caption)
                    .foregroundStyle(.green)
            }
        }
        .padding()
        .background(.fill.tertiary, in: RoundedRectangle(cornerRadius: 10))
    }

    private var downloadProgressView: some View {
        VStack(spacing: 8) {
            ForEach(Array(viewModel.downloadProgress.keys.sorted()), id: \.self) { modelId in
                let percent = viewModel.downloadProgress[modelId] ?? 0
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Text(modelId.replacingOccurrences(of: "mlx-community/", with: ""))
                            .font(.caption)
                        Spacer()
                        Text(percent >= 100 ? "Done" : "\(Int(percent))%")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    ProgressView(value: min(percent, 100), total: 100)
                        .tint(percent >= 100 ? .green : .blue)
                }
            }

            if viewModel.downloadProgress.values.allSatisfy({ $0 >= 100 }) {
                Label("Models ready!", systemImage: "checkmark.circle.fill")
                    .foregroundStyle(.green)
                    .padding(.top, 4)
            }
        }
        .padding()
        .background(.fill.tertiary, in: RoundedRectangle(cornerRadius: 10))
    }

    /// Assistant candidates: exclude embedding/coder models, downloaded first then by size.
    private func assistantModels(_ models: AvailableModelsResponse) -> [ModelInfo] {
        models.compatibleModels
            .filter { !$0.modelId.lowercased().contains("embedding") }
            .sorted {
                if $0.isDownloaded != $1.isDownloaded { return $0.isDownloaded }
                return $0.sizeGb > $1.sizeGb
            }
    }

    /// Filter candidates: small models only, downloaded first then by size.
    private func filterModels(_ models: AvailableModelsResponse) -> [ModelInfo] {
        models.compatibleModels
            .filter { $0.sizeGb <= 10 && !$0.modelId.lowercased().contains("embedding") }
            .sorted {
                if $0.isDownloaded != $1.isDownloaded { return $0.isDownloaded }
                return $0.sizeGb < $1.sizeGb
            }
    }

    private func customizeSection(_ models: AvailableModelsResponse) -> some View {
        DisclosureGroup("Customize", isExpanded: $showCustomize) {
            VStack(spacing: 12) {
                // Assistant model picker
                VStack(alignment: .leading, spacing: 4) {
                    Text("Assistant Model")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Picker("", selection: $selectedAssistant) {
                        ForEach(assistantModels(models)) { model in
                            Text(modelPickerLabel(model))
                                .tag(model.modelId)
                        }
                    }
                    .labelsHidden()
                }

                // Filter model picker
                VStack(alignment: .leading, spacing: 4) {
                    Text("Filter Model")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Picker("", selection: $selectedFilter) {
                        ForEach(filterModels(models)) { model in
                            Text(modelPickerLabel(model))
                                .tag(model.modelId)
                        }
                    }
                    .labelsHidden()
                }

                Button("Download Custom Selection") {
                    viewModel.selectAndDownloadModels(
                        assistant: selectedAssistant,
                        filter: selectedFilter
                    )
                }
                .disabled(selectedAssistant.isEmpty || selectedFilter.isEmpty)
            }
            .padding(.top, 8)
        }
    }

    private func modelPickerLabel(_ model: ModelInfo) -> String {
        let downloaded = model.isDownloaded ? " [ready]" : ""
        return "\(model.displayName) (\(model.sizeString))\(downloaded)"
    }

    private func isRecommendedDownloaded(_ models: AvailableModelsResponse) -> (assistant: Bool, filter: Bool) {
        let aDownloaded = models.compatibleModels.first {
            $0.modelId == models.recommended.assistant
        }?.isDownloaded ?? false
        let fDownloaded = models.compatibleModels.first {
            $0.modelId == models.recommended.filter
        }?.isDownloaded ?? false
        return (aDownloaded, fDownloaded)
    }

    // MARK: - Footer

    private var footer: some View {
        HStack {
            Button("Skip for now") {
                viewModel.skipModelSetup()
            }
            .foregroundStyle(.secondary)

            Spacer()

            if viewModel.downloadProgress.values.allSatisfy({ $0 >= 100 })
                && !viewModel.downloadProgress.isEmpty {
                Button("Continue") {
                    viewModel.isModelSetupNeeded = false
                }
                .buttonStyle(.borderedProminent)
            }
        }
    }
}
