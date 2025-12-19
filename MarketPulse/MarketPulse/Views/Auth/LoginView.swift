import SwiftUI

struct LoginView: View {
    @EnvironmentObject var authViewModel: AuthViewModel
    @State private var email = ""
    @State private var password = ""

    var body: some View {
        NavigationView {
            ZStack {
                // Background gradient
                LinearGradient(
                    colors: [Color.blue.opacity(0.6), Color.purple.opacity(0.6)],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )
                .ignoresSafeArea()

                VStack(spacing: AppConstants.paddingLarge) {
                    Spacer()

                    // Logo and Title
                    VStack(spacing: AppConstants.paddingMedium) {
                        Image(systemName: "chart.line.uptrend.xyaxis")
                            .font(.system(size: 70))
                            .foregroundColor(.white)

                        Text("MarketPulse")
                            .font(.system(size: 40, weight: .bold))
                            .foregroundColor(.white)

                        Text("AI-Powered Value Investing")
                            .font(.headline)
                            .foregroundColor(.white.opacity(0.9))
                    }
                    .padding(.bottom, AppConstants.paddingLarge)

                    // Login Form
                    VStack(spacing: AppConstants.paddingMedium) {
                        TextField("Email", text: $email)
                            .textFieldStyle(RoundedTextFieldStyle())
                            .keyboardType(.emailAddress)
                            .textInputAutocapitalization(.never)

                        SecureField("Password", text: $password)
                            .textFieldStyle(RoundedTextFieldStyle())

                        if let errorMessage = authViewModel.errorMessage {
                            Text(errorMessage)
                                .font(.caption)
                                .foregroundColor(.red)
                                .padding(.horizontal)
                        }

                        Button(action: signIn) {
                            HStack {
                                if authViewModel.isLoading {
                                    ProgressView()
                                        .progressViewStyle(CircularProgressViewStyle(tint: .white))
                                } else {
                                    Text("Sign In")
                                        .fontWeight(.semibold)
                                }
                            }
                            .frame(maxWidth: .infinity)
                            .padding()
                            .background(Color.white.opacity(0.2))
                            .foregroundColor(.white)
                            .cornerRadius(AppConstants.cornerRadiusMedium)
                        }
                        .disabled(authViewModel.isLoading || email.isEmpty || password.isEmpty)

                        // Sign Up Link
                        Button(action: {
                            // TODO: Navigate to sign up
                        }) {
                            Text("Don't have an account? Sign Up")
                                .font(.subheadline)
                                .foregroundColor(.white.opacity(0.9))
                        }
                        .padding(.top, AppConstants.paddingSmall)
                    }
                    .padding(.horizontal, AppConstants.paddingLarge)

                    Spacer()

                    // Footer
                    Text("Secure authentication powered by Supabase")
                        .font(.caption)
                        .foregroundColor(.white.opacity(0.7))
                        .padding(.bottom, AppConstants.paddingLarge)
                }
            }
        }
    }

    private func signIn() {
        Task {
            await authViewModel.signInWithSupabase(email: email, password: password)
        }
    }
}

// MARK: - Custom Text Field Style

struct RoundedTextFieldStyle: TextFieldStyle {
    func _body(configuration: TextField<Self._Label>) -> some View {
        configuration
            .padding()
            .background(Color.white.opacity(0.9))
            .cornerRadius(AppConstants.cornerRadiusMedium)
    }
}

struct LoginView_Previews: PreviewProvider {
    static var previews: some View {
        LoginView()
            .environmentObject(AuthViewModel())
    }
}
