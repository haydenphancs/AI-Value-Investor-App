import SwiftUI

struct ProfileView: View {
    @EnvironmentObject var authViewModel: AuthViewModel
    @StateObject private var viewModel = ProfileViewModel()
    @State private var showingSignOutAlert = false

    var body: some View {
        NavigationView {
            ZStack {
                if viewModel.isLoading {
                    LoadingView(message: "Loading profile...")
                } else {
                    List {
                        // User Info Section
                        Section {
                            if let user = viewModel.user {
                                HStack {
                                    VStack(alignment: .leading, spacing: 4) {
                                        Text(user.fullName ?? "User")
                                            .font(.headline)

                                        Text(user.email)
                                            .font(.caption)
                                            .foregroundColor(.secondary)
                                    }

                                    Spacer()

                                    TierBadge(tier: user.tier)
                                }
                                .padding(.vertical, 8)
                            }
                        }

                        // Usage Section
                        if let usage = viewModel.usage {
                            Section(header: Text("Usage")) {
                                VStack(alignment: .leading, spacing: AppConstants.paddingMedium) {
                                    HStack {
                                        Text("Deep Research Reports")
                                            .font(.subheadline)

                                        Spacer()

                                        if usage.deepResearch.isUnlimited {
                                            Text("Unlimited")
                                                .font(.caption)
                                                .fontWeight(.medium)
                                                .foregroundColor(.green)
                                        } else {
                                            Text("\(usage.deepResearch.used) / \(usage.deepResearch.limit ?? 0)")
                                                .font(.caption)
                                                .fontWeight(.medium)
                                        }
                                    }

                                    if !usage.deepResearch.isUnlimited {
                                        ProgressView(value: usage.deepResearch.progressPercentage)
                                            .tint(usage.deepResearch.remaining ?? 0 > 0 ? .blue : .red)
                                    }

                                    if let resetAt = usage.resetAt {
                                        Text("Resets on \(resetAt.formatted())")
                                            .font(.caption)
                                            .foregroundColor(.secondary)
                                    }
                                }
                                .padding(.vertical, 4)
                            }
                        }

                        // Stats Section
                        if let stats = viewModel.stats {
                            Section(header: Text("Statistics")) {
                                StatRow(label: "Watchlist Stocks", value: "\(stats.watchlistCount)")
                                StatRow(label: "Reports Generated", value: "\(stats.reportsGenerated)")
                                StatRow(label: "Chat Sessions", value: "\(stats.chatSessions)")

                                if let lastActivity = stats.lastActivity {
                                    HStack {
                                        Text("Last Activity")
                                            .foregroundColor(.secondary)
                                        Spacer()
                                        Text(lastActivity.timeAgo())
                                            .foregroundColor(.secondary)
                                    }
                                }
                            }
                        }

                        // Actions Section
                        Section {
                            if let user = viewModel.user, user.tier == .free {
                                Button(action: {
                                    // Navigate to upgrade
                                }) {
                                    HStack {
                                        Image(systemName: "star.fill")
                                            .foregroundColor(.yellow)
                                        Text("Upgrade to Pro")
                                            .foregroundColor(.primary)
                                        Spacer()
                                        Image(systemName: "chevron.right")
                                            .font(.caption)
                                            .foregroundColor(.secondary)
                                    }
                                }
                            }

                            NavigationLink(destination: WatchlistView()) {
                                Label("My Watchlist", systemImage: "star")
                            }

                            NavigationLink(destination: ResearchListView()) {
                                Label("My Reports", systemImage: "doc.text")
                            }
                        }

                        // Settings Section
                        Section {
                            Button(action: {
                                showingSignOutAlert = true
                            }) {
                                HStack {
                                    Text("Sign Out")
                                        .foregroundColor(.red)
                                    Spacer()
                                    Image(systemName: "rectangle.portrait.and.arrow.right")
                                        .foregroundColor(.red)
                                }
                            }
                        }
                    }
                    .refreshable {
                        await viewModel.loadProfile()
                    }
                }
            }
            .navigationTitle("Profile")
            .task {
                await viewModel.loadProfile()
            }
            .alert("Sign Out", isPresented: $showingSignOutAlert) {
                Button("Cancel", role: .cancel) { }
                Button("Sign Out", role: .destructive) {
                    Task {
                        await viewModel.signOut()
                        await authViewModel.signOut()
                    }
                }
            } message: {
                Text("Are you sure you want to sign out?")
            }
        }
    }
}

struct TierBadge: View {
    let tier: UserTier

    var body: some View {
        Text(tier.displayName)
            .font(.caption)
            .fontWeight(.bold)
            .padding(.horizontal, 12)
            .padding(.vertical, 4)
            .background(backgroundColor)
            .foregroundColor(.white)
            .cornerRadius(AppConstants.cornerRadiusSmall)
    }

    private var backgroundColor: Color {
        switch tier {
        case .free: return .gray
        case .pro: return .blue
        case .premium: return .purple
        }
    }
}

struct StatRow: View {
    let label: String
    let value: String

    var body: some View {
        HStack {
            Text(label)
                .foregroundColor(.secondary)
            Spacer()
            Text(value)
                .fontWeight(.medium)
        }
    }
}

struct ProfileView_Previews: PreviewProvider {
    static var previews: some View {
        ProfileView()
            .environmentObject(AuthViewModel())
    }
}
