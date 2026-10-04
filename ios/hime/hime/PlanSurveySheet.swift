//
//  PlanSurveySheet.swift
//  hime
//
//  Re-run the goal survey at any time from Settings and have HiMe redesign the
//  plan. Reuses the same adaptive GoalSurveyView / SurveyModel as onboarding,
//  but here a token already exists, so it POSTs the survey with
//  `trigger_now = true` — the backend kicks the plan designer immediately
//  instead of waiting for the next chat reply.
//

import SwiftUI
import Combine

private let kAccent = Color(red: 0.95, green: 0.70, blue: 0.35)

struct PlanSurveySheet: View {
    @Environment(\.dismiss) private var dismiss
    @StateObject private var survey = SurveyModel()
    @State private var submitting = false
    @State private var submitted = false
    @State private var errorMessage: String? = nil

    var body: some View {
        NavigationStack {
            Group {
                if submitted {
                    confirmation
                } else {
                    VStack(spacing: 0) {
                        GoalSurveyView(model: survey)
                        bottomBar
                    }
                }
            }
            .navigationTitle("Redesign Plan")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
        }
    }

    private var bottomBar: some View {
        VStack(spacing: 8) {
            if let errorMessage {
                Text(errorMessage)
                    .font(.footnote)
                    .foregroundColor(.red)
                    .multilineTextAlignment(.center)
            }
            Button {
                if survey.isLast {
                    Task { await submit() }
                } else {
                    withAnimation(.easeInOut(duration: 0.2)) { survey.next() }
                }
            } label: {
                HStack {
                    if submitting {
                        ProgressView().tint(.white)
                    } else {
                        Text(survey.isLast ? (errorMessage == nil ? "Redesign My Plan" : "Retry") : "Next")
                            .font(.headline)
                    }
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 14)
                .background(survey.canProceed ? kAccent : Color.gray.opacity(0.3))
                .foregroundColor(.white)
                .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            }
            .disabled(!survey.canProceed || submitting)
        }
        .padding(.horizontal, 24)
        .padding(.bottom, 12)
    }

    private var confirmation: some View {
        VStack(spacing: 16) {
            Image(systemName: "checkmark.seal.fill")
                .font(.system(size: 52))
                .foregroundColor(kAccent)
            Text("HiMe is redesigning your plan")
                .font(.title3.weight(.bold))
                .multilineTextAlignment(.center)
            Text("Your new check-ins and a fresh plan report will arrive in Chat shortly.")
                .font(.subheadline)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32)
            Button("Done") { dismiss() }
                .font(.headline)
                .padding(.top, 8)
        }
        .padding()
    }

    private func submit() async {
        guard survey.hasAnyAnswer else { dismiss(); return }
        submitting = true
        errorMessage = nil
        let p = survey.payload()
        let ok = await postSurvey(goals: p.goals, answers: p.answers)
        submitting = false
        if ok {
            withAnimation { submitted = true }
        } else {
            // Stay on the survey (answers intact) so the user can retry —
            // showing "HiMe is redesigning your plan" for a request the server
            // never accepted left them waiting for a plan that was never coming.
            errorMessage = String(localized: "Couldn't reach your server. Check the address and token in Settings, then try again.")
        }
    }

    /// POST the survey with `trigger_now` so the backend redesigns immediately.
    /// Returns true only on a 2xx response.
    private func postSurvey(goals: [String], answers: [String: Any]) async -> Bool {
        let cfg = ServerConfig.load()
        guard let url = URL(string: "\(cfg.apiBaseURL)/api/agent/onboarding-survey") else { return false }
        var req = APIClient.request(url, method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: Any] = ["goals": goals, "answers": answers, "trigger_now": true]
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        guard let (_, resp) = try? await URLSession.shared.data(for: req),
              let http = resp as? HTTPURLResponse else { return false }
        return (200...299).contains(http.statusCode)
    }
}
