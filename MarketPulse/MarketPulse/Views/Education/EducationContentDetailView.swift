import SwiftUI

struct EducationContentDetailView: View {
    @StateObject private var viewModel: EducationDetailViewModel
    @State private var navigateToChat = false

    init(contentId: String) {
        _viewModel = StateObject(wrappedValue: EducationDetailViewModel(contentId: contentId))
    }

    var body: some View {
        ZStack {
            if viewModel.isLoading {
                LoadingView(message: "Loading content...")
            } else if let content = viewModel.content {
                ScrollView {
                    VStack(alignment: .leading, spacing: AppConstants.paddingLarge) {
                        // Cover Image
                        if let coverUrl = content.coverImageUrl {
                            AsyncImage(url: URL(string: coverUrl)) { image in
                                image
                                    .resizable()
                                    .scaledToFit()
                            } placeholder: {
                                Rectangle()
                                    .fill(Color(.systemGray5))
                                    .aspectRatio(2/3, contentMode: .fit)
                                    .overlay(ProgressView())
                            }
                            .frame(maxWidth: .infinity)
                            .frame(height: 300)
                            .clipped()
                            .cornerRadius(AppConstants.cornerRadiusMedium)
                        }

                        // Header
                        VStack(alignment: .leading, spacing: AppConstants.paddingSmall) {
                            // Type Badge
                            Text(content.type.displayName)
                                .font(.caption)
                                .fontWeight(.medium)
                                .padding(.horizontal, 12)
                                .padding(.vertical, 6)
                                .background(content.type == .book ? Color.blue.opacity(0.2) : Color.green.opacity(0.2))
                                .foregroundColor(content.type == .book ? .blue : .green)
                                .cornerRadius(AppConstants.cornerRadiusSmall)

                            // Title
                            Text(content.title)
                                .font(.title)
                                .fontWeight(.bold)

                            // Author and Year
                            HStack {
                                if let author = content.author {
                                    Text(author)
                                        .font(.headline)
                                        .foregroundColor(.secondary)
                                }

                                if let year = content.publicationYear {
                                    Text("•")
                                        .foregroundColor(.secondary)
                                    Text("\(year)")
                                        .font(.headline)
                                        .foregroundColor(.secondary)
                                }
                            }

                            // Processing Status
                            if !content.isProcessed {
                                HStack(spacing: 8) {
                                    ProgressView()
                                    Text("Content is being indexed for chat...")
                                        .font(.caption)
                                        .foregroundColor(.secondary)
                                }
                                .padding()
                                .background(Color.yellow.opacity(0.1))
                                .cornerRadius(AppConstants.cornerRadiusSmall)
                            }
                        }

                        // Summary
                        if let summary = content.summary {
                            VStack(alignment: .leading, spacing: AppConstants.paddingSmall) {
                                Text("Summary")
                                    .font(.headline)

                                Text(summary)
                                    .font(.body)
                                    .foregroundColor(.secondary)
                            }
                            .padding()
                            .background(Color(.secondarySystemBackground))
                            .cornerRadius(AppConstants.cornerRadiusMedium)
                        }

                        // Topics
                        if let topics = content.topics, !topics.isEmpty {
                            VStack(alignment: .leading, spacing: AppConstants.paddingSmall) {
                                Text("Topics")
                                    .font(.headline)

                                FlowLayout(spacing: 8) {
                                    ForEach(topics, id: \.self) { topic in
                                        Text(topic)
                                            .font(.subheadline)
                                            .padding(.horizontal, 12)
                                            .padding(.vertical, 6)
                                            .background(Color(.systemGray5))
                                            .cornerRadius(AppConstants.cornerRadiusSmall)
                                    }
                                }
                            }
                        }

                        // Metadata
                        VStack(alignment: .leading, spacing: AppConstants.paddingSmall) {
                            Text("Details")
                                .font(.headline)

                            HStack {
                                Text("Content Chunks:")
                                    .foregroundColor(.secondary)
                                Spacer()
                                Text("\(content.chunkCount)")
                                    .fontWeight(.medium)
                            }

                            if let sourceUrl = content.sourceUrl {
                                Link(destination: URL(string: sourceUrl)!) {
                                    HStack {
                                        Text("Source")
                                            .foregroundColor(.secondary)
                                        Spacer()
                                        Image(systemName: "arrow.up.right.square")
                                        Text("View Original")
                                    }
                                    .foregroundColor(.accentColor)
                                }
                            }
                        }
                        .padding()
                        .background(Color(.secondarySystemBackground))
                        .cornerRadius(AppConstants.cornerRadiusMedium)

                        // Full Text (if available)
                        if let fullText = content.fullText {
                            VStack(alignment: .leading, spacing: AppConstants.paddingSmall) {
                                Text("Full Text")
                                    .font(.headline)

                                Text(fullText)
                                    .font(.body)
                            }
                            .padding()
                            .background(Color(.secondarySystemBackground))
                            .cornerRadius(AppConstants.cornerRadiusMedium)
                        }

                        // Chat Button
                        Button(action: {
                            Task {
                                await viewModel.startChat()
                                if viewModel.createdChatSession != nil {
                                    navigateToChat = true
                                }
                            }
                        }) {
                            Label("Start Chat About This Content", systemImage: "bubble.left.and.bubble.right.fill")
                                .fontWeight(.semibold)
                                .foregroundColor(.white)
                                .frame(maxWidth: .infinity)
                                .padding()
                                .background(content.isProcessed ? Color.green : Color.gray)
                                .cornerRadius(AppConstants.cornerRadiusMedium)
                        }
                        .disabled(!content.isProcessed)

                        if !content.isProcessed {
                            Text("Chat will be available once content is fully processed")
                                .font(.caption)
                                .foregroundColor(.secondary)
                                .multilineTextAlignment(.center)
                                .frame(maxWidth: .infinity)
                        }
                    }
                    .padding()
                }
            } else if viewModel.errorMessage != nil {
                EmptyStateView(
                    icon: "exclamationmark.triangle",
                    title: "Error",
                    message: viewModel.errorMessage ?? "Failed to load content"
                )
            }
        }
        .navigationTitle("Content Detail")
        .navigationBarTitleDisplayMode(.inline)
        .background(
            NavigationLink(
                destination: viewModel.createdChatSession.map { session in
                    ChatConversationView(sessionId: session.id)
                },
                isActive: $navigateToChat
            ) {
                EmptyView()
            }
            .hidden()
        )
        .task {
            await viewModel.loadContent()
        }
    }
}

// Simple flow layout for topics
struct FlowLayout: Layout {
    var spacing: CGFloat = 8

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let result = FlowResult(
            in: proposal.replacingUnspecifiedDimensions().width,
            subviews: subviews,
            spacing: spacing
        )
        return result.size
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let result = FlowResult(
            in: bounds.width,
            subviews: subviews,
            spacing: spacing
        )
        for (index, subview) in subviews.enumerated() {
            subview.place(at: CGPoint(x: bounds.minX + result.frames[index].minX,
                                     y: bounds.minY + result.frames[index].minY),
                         proposal: ProposedViewSize(result.frames[index].size))
        }
    }

    struct FlowResult {
        var frames: [CGRect] = []
        var size: CGSize = .zero

        init(in maxWidth: CGFloat, subviews: Subviews, spacing: CGFloat) {
            var currentX: CGFloat = 0
            var currentY: CGFloat = 0
            var lineHeight: CGFloat = 0

            for subview in subviews {
                let size = subview.sizeThatFits(.unspecified)

                if currentX + size.width > maxWidth && currentX > 0 {
                    currentX = 0
                    currentY += lineHeight + spacing
                    lineHeight = 0
                }

                frames.append(CGRect(origin: CGPoint(x: currentX, y: currentY), size: size))
                currentX += size.width + spacing
                lineHeight = max(lineHeight, size.height)
            }

            self.size = CGSize(width: maxWidth, height: currentY + lineHeight)
        }
    }
}

struct EducationContentDetailView_Previews: PreviewProvider {
    static var previews: some View {
        EducationContentDetailView(contentId: "sample-id")
    }
}
