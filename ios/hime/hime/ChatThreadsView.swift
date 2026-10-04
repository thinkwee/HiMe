//
//  ChatThreadsView.swift
//  hime
//
//  Multi-conversation UI for in-app chat:
//    * `ChatScreen`  — the pushed Chat destination. Hosts the current thread's
//      `ChatView` and a sheet with the thread list; switching threads swaps the
//      conversation in place (no deeper navigation stack).
//    * `ChatThreadListView` — main thread pinned on top, other threads with
//      preview / relative time / unread dot, swipe to rename / archive / delete,
//      and an "Archived" section.
//
//  State (thread list, unread, the shared event stream, per-thread view models)
//  lives in `ChatHub`.
//

import SwiftUI

// MARK: - Chat screen

struct ChatScreen: View {
    @ObservedObject private var hub = ChatHub.shared
    @State private var threadId: String
    @State private var showList = false
    @State private var creating = false

    /// `threadId` defaults to the permanent main thread.
    init(threadId: String = chatMainThreadId) {
        _threadId = State(initialValue: threadId)
    }

    private var supported: Bool { hub.threadsSupported == true }
    /// Old servers have a single conversation: always show main.
    private var activeId: String { hub.threadsSupported == false ? chatMainThreadId : threadId }

    var body: some View {
        let id = activeId
        let showThreads: (() -> Void)? = supported ? { showList = true } : nil
        let startThread: (() -> Void)? = supported ? { newThread() } : nil
        ChatView(
            vm: hub.viewModel(for: id),
            connection: hub.connection,
            title: supported ? hub.title(for: id) : String(localized: "Chat"),
            onShowThreads: showThreads,
            onNewThread: startThread,
            hasUnreadElsewhere: supported && hub.threads.contains { $0.id != id && hub.isUnread($0) }
        )
        .id(id)
        .sheet(isPresented: $showList) {
            ChatThreadListView(currentId: $threadId)
        }
        .onReceive(hub.removedThread) { removed in
            if removed == threadId { threadId = chatMainThreadId }
        }
    }

    /// Start a fresh conversation — unless the one on screen is already empty.
    private func newThread() {
        let id = activeId
        if id != chatMainThreadId {
            let vm = hub.viewModel(for: id)
            if vm.didLoadHistory && vm.rows.isEmpty { return }
        }
        guard !creating else { return }
        creating = true
        Task {
            if let t = await hub.createThread() { threadId = t.id }
            creating = false
        }
    }
}

// MARK: - Thread list

struct ChatThreadListView: View {
    @ObservedObject private var hub = ChatHub.shared
    @Environment(\.dismiss) private var dismiss
    /// The thread shown in the chat screen; the list changes it on selection.
    @Binding var currentId: String

    @State private var showArchived = false
    @State private var renameTarget: ChatThread?
    @State private var showRename = false
    @State private var renameText = ""
    @State private var deleteTarget: ChatThread?
    @State private var showDelete = false

    var body: some View {
        NavigationStack {
            List {
                Section {
                    ForEach(hub.threads) { thread in
                        threadRow(thread)
                    }
                }
                Section {
                    Button {
                        withAnimation { showArchived.toggle() }
                        if showArchived { Task { await hub.loadArchived() } }
                    } label: {
                        HStack {
                            Label("Archived", systemImage: "archivebox")
                                .foregroundColor(HimeColor.ink2)
                            Spacer()
                            Image(systemName: showArchived ? "chevron.up" : "chevron.down")
                                .font(.footnote)
                                .foregroundColor(.secondary)
                        }
                    }
                    if showArchived {
                        if hub.archived.isEmpty {
                            Text("No archived chats")
                                .font(.footnote)
                                .foregroundColor(.secondary)
                        }
                        ForEach(hub.archived) { thread in
                            threadRow(thread)
                        }
                    }
                }
            }
            .listStyle(.insetGrouped)
            .scrollContentBackground(.hidden)
            .background(HimeColor.paper)
            .navigationTitle("Chats")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button("Done") { dismiss() }
                }
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button { createThread() } label: {
                        Image(systemName: "square.and.pencil")
                    }
                    .accessibilityLabel(Text("New chat"))
                }
            }
            .refreshable {
                await hub.loadThreads()
                if showArchived { await hub.loadArchived() }
            }
            .task { await hub.loadThreads() }
            .alert("Rename chat", isPresented: $showRename) {
                TextField("Title", text: $renameText)
                Button("Cancel", role: .cancel) {}
                Button("Save") {
                    if let t = renameTarget {
                        let text = renameText
                        Task { await hub.rename(t.id, to: text) }
                    }
                }
            }
            .confirmationDialog("Delete this chat?", isPresented: $showDelete, titleVisibility: .visible) {
                Button("Delete", role: .destructive) {
                    if let t = deleteTarget { Task { await hub.delete(t.id) } }
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("Its messages are removed. Hime keeps what it has learned about you.")
            }
            .alert("Something went wrong",
                   isPresented: Binding(get: { hub.actionError != nil },
                                        set: { if !$0 { hub.actionError = nil } })) {
                Button("OK", role: .cancel) {}
            } message: {
                Text(hub.actionError ?? "")
            }
        }
    }

    @ViewBuilder
    private func threadRow(_ thread: ChatThread) -> some View {
        let isCurrent = thread.id == currentId
        Button {
            currentId = thread.id
            dismiss()
        } label: {
            ChatThreadRow(thread: thread,
                          title: hub.title(for: thread.id),
                          unread: hub.isUnread(thread) && !isCurrent,
                          isCurrent: isCurrent)
                .equatable()
        }
        .buttonStyle(.plain)
        .listRowBackground(isCurrent ? HimeColor.cream : HimeColor.card)
        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
            if !thread.isMain {
                Button(role: .destructive) {
                    deleteTarget = thread
                    showDelete = true
                } label: {
                    Label("Delete", systemImage: "trash")
                }
                if thread.archived {
                    Button {
                        Task { await hub.setArchived(thread.id, false) }
                    } label: {
                        Label("Unarchive", systemImage: "tray.and.arrow.up")
                    }
                    .tint(HimeColor.accent)
                } else {
                    Button {
                        if thread.id == currentId { currentId = chatMainThreadId }
                        Task { await hub.setArchived(thread.id, true) }
                    } label: {
                        Label("Archive", systemImage: "archivebox")
                    }
                    .tint(HimeColor.idleStrong)
                }
            }
        }
        .swipeActions(edge: .leading, allowsFullSwipe: false) {
            if !thread.isMain {
                Button {
                    renameTarget = thread
                    renameText = thread.title
                    showRename = true
                } label: {
                    Label("Rename", systemImage: "pencil")
                }
                .tint(HimeColor.accentStrong)
            }
        }
    }

    private func createThread() {
        Task {
            if let t = await hub.createThread() {
                currentId = t.id
                dismiss()
            }
        }
    }
}

// MARK: - Row

/// One thread row. A pure value view (`Equatable`) so a message arriving in one
/// thread re-renders only that row.
private struct ChatThreadRow: View, Equatable {
    let thread: ChatThread
    let title: String
    let unread: Bool
    let isCurrent: Bool

    private static let relative: RelativeDateTimeFormatter = {
        let f = RelativeDateTimeFormatter()
        f.unitsStyle = .abbreviated
        return f
    }()

    private var subtitle: String {
        if thread.isMain { return String(localized: "Reports & reminders") }
        guard let content = thread.last?.content else { return "" }
        return content.replacingOccurrences(of: "\n", with: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var timeText: String {
        guard let date = thread.last?.date ?? thread.updatedAt else { return "" }
        if abs(date.timeIntervalSinceNow) < 60 { return String(localized: "now") }
        return Self.relative.localizedString(for: date, relativeTo: Date())
    }

    var body: some View {
        HStack(spacing: 12) {
            leadingIcon
            VStack(alignment: .leading, spacing: 3) {
                HStack(alignment: .firstTextBaseline) {
                    Text(title)
                        .font(.subheadline.weight(.semibold))
                        .foregroundColor(HimeColor.ink)
                        .lineLimit(1)
                    Spacer(minLength: 8)
                    Text(timeText)
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                HStack(spacing: 8) {
                    Text(subtitle)
                        .font(.footnote)
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                    Spacer(minLength: 0)
                    if unread {
                        Circle().fill(HimeColor.accent)
                            .frame(width: 9, height: 9)
                            .accessibilityLabel(Text("Unread"))
                    }
                }
            }
        }
        .padding(.vertical, 4)
        .contentShape(Rectangle())
    }

    @ViewBuilder
    private var leadingIcon: some View {
        if thread.isMain {
            HimeAvatar(size: 40)
        } else {
            ZStack {
                Circle().fill(HimeColor.cream)
                Image(systemName: thread.archived ? "archivebox" : "bubble.left")
                    .font(.system(size: 16))
                    .foregroundColor(HimeColor.accentStrong)
            }
            .frame(width: 40, height: 40)
        }
    }
}
