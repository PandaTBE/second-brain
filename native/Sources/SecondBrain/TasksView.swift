import SwiftUI
import Combine
import AppKit
import SecondBrainCore

struct TasksView: View {
    @ObservedObject var model: TasksViewModel
    @ViewState private var title = ""
    @ViewState private var kind: TaskKind = .todo
    @ViewState private var stat: TaskStat = .strength
    @ViewState private var difficulty = 1
    @ViewState private var pendingDelete: TrackerTask?
    private let dayCheck = Timer.publish(every: 60, on: .main, in: .common).autoconnect()

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                VStack(alignment: .leading, spacing: 5) {
                    Text("Задачи").font(.title2.weight(.semibold))
                    Text("Обычные задачи и ежедневные привычки. Опыт начисляется за выполнение.")
                        .font(.callout).foregroundStyle(.secondary)
                }
                Spacer()
                if model.isLoading || !model.action.isEmpty { ProgressView().controlSize(.small).accessibilityLabel("Обновление задач") }
                Button { Task { await model.load() } } label: { Image(systemName: "arrow.clockwise") }
                    .disabled(model.busy).help("Обновить задачи").accessibilityLabel("Обновить задачи")
            }
            if let player = model.board?.player { playerPanel(player) }
            creationForm
            if let notice = model.notice {
                Label(notice, systemImage: "sparkles").foregroundStyle(.green)
            }
            if let error = model.error {
                HStack {
                    Label(error, systemImage: "exclamationmark.circle").foregroundStyle(.orange).textSelection(.enabled)
                    Spacer()
                    Button("Повторить загрузку") { Task { await model.load() } }.disabled(model.busy)
                }
            }
            if model.isRestoring {
                ProgressView("Восстанавливаем задачи из резервной копии…").frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let board = model.board {
                HStack {
                    Text("Сегодня · \(board.day)")
                    Spacer()
                    Text("Ежедневные задачи начинаются заново в полночь.")
                }.font(.caption).foregroundStyle(.secondary)
                if board.tasks.isEmpty {
                    ContentUnavailableView("Пока нет задач", systemImage: "checklist",
                                           description: Text("Добавьте первую задачу или ежедневную привычку выше."))
                } else {
                    kanban(board)
                }
            } else if model.isLoading {
                ProgressView("Загружаем задачи…").frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ContentUnavailableView("Задачи недоступны", systemImage: "checklist",
                                       description: Text("Обновите доску, чтобы загрузить задачи из локального сервиса."))
            }
        }
        .padding(22)
        .alert("Удалить задачу?", isPresented: Binding(get: { pendingDelete != nil }, set: { if !$0 { pendingDelete = nil } }),
               presenting: pendingDelete) { task in
            Button("Отмена", role: .cancel) { pendingDelete = nil }
            Button("Удалить", role: .destructive) {
                pendingDelete = nil
                Task { await model.delete(task.id) }
            }
        } message: { task in
            Text("«\(task.title)» исчезнет с доски. Уже полученный опыт сохранится.")
        }
        .task { await model.load() }
        .onReceive(dayCheck) { _ in Task { await model.reloadIfDayChanged() } }
        .onReceive(NotificationCenter.default.publisher(for: .NSCalendarDayChanged)) { _ in
            Task { await model.reloadIfDayChanged() }
        }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            Task { await model.reloadIfDayChanged() }
        }
        .onReceive(NSWorkspace.shared.notificationCenter.publisher(for: NSWorkspace.didWakeNotification)) { _ in
            Task { await model.reloadIfDayChanged() }
        }
        .onChange(of: model.restoreEpoch) { _, _ in
            pendingDelete = nil; title = ""; kind = .todo; stat = .strength; difficulty = 1
        }
    }

    private func playerPanel(_ player: TaskPlayer) -> some View {
        GroupBox("Система") {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text("Уровень \(player.level)").font(.headline)
                    Text("Ранг \(player.rank)").font(.headline).foregroundStyle(.secondary)
                    Spacer()
                    Text("\(player.xp) / \(player.nextLevelXp) XP").monospacedDigit()
                }
                ProgressView(value: player.levelProgress)
                    .accessibilityLabel("Опыт до следующего уровня")
                    .accessibilityValue("\(player.xp) из \(player.nextLevelXp), уровень \(player.level)")
                HStack(alignment: .top, spacing: 12) {
                    ForEach(TaskStat.allCases) { stat in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(stat.title).font(.caption).foregroundStyle(.secondary)
                            Text("\(player.stats[stat])").font(.title3.weight(.medium)).monospacedDigit()
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .accessibilityElement(children: .combine)
                    }
                }
            }.padding(8)
        }
    }

    private var creationForm: some View {
        GroupBox("Новая задача") {
            VStack(alignment: .leading, spacing: 10) {
                TextField("Название задачи", text: $title).textFieldStyle(.roundedBorder)
                    .accessibilityLabel("Название новой задачи").onSubmit { create() }
                HStack(spacing: 12) {
                    Picker("Тип", selection: $kind) {
                        ForEach(TaskKind.allCases) { value in Text(value.title).tag(value) }
                    }.labelsHidden().pickerStyle(.menu)
                    Picker("Характеристика", selection: $stat) {
                        ForEach(TaskStat.allCases) { value in Text(value.title).tag(value) }
                    }.labelsHidden().pickerStyle(.menu)
                    Picker("Сложность", selection: $difficulty) {
                        Text("Лёгкая").tag(1)
                        Text("Средняя").tag(2)
                        Text("Сложная").tag(3)
                    }.labelsHidden().pickerStyle(.menu)
                    Button("Добавить", action: create).buttonStyle(.borderedProminent)
                        .disabled(title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }.padding(8)
        }.disabled(model.busy || model.board == nil)
    }

    private func create() {
        let submittedTitle = title
        let submittedKind = kind, submittedStat = stat, submittedDifficulty = difficulty
        let lifetime = model.restoreEpoch
        Task {
            guard lifetime == model.restoreEpoch else { return }
            let created = await model.create(title: submittedTitle, kind: submittedKind, stat: submittedStat, difficulty: submittedDifficulty)
            if created && lifetime == model.restoreEpoch && title == submittedTitle { title = "" }
        }
    }

    private func kanban(_ board: TaskBoard) -> some View {
        GeometryReader { geometry in
            ScrollView(.horizontal) {
                HStack(alignment: .top, spacing: 12) {
                    ForEach(TaskStatus.allCases) { status in
                        column(status, tasks: board.tasks.filter { $0.status == status })
                            .frame(width: max(210, (geometry.size.width - 24) / 3))
                    }
                }
            }
        }
    }

    private func column(_ status: TaskStatus, tasks: [TrackerTask]) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(status.title).font(.headline).accessibilityAddTraits(.isHeader)
                Spacer()
                Text("\(tasks.count)").foregroundStyle(.secondary).accessibilityLabel("Задач: \(tasks.count)")
            }
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 10) {
                    if tasks.isEmpty {
                        Text("Нет задач").font(.callout).foregroundStyle(.secondary).frame(maxWidth: .infinity, minHeight: 60)
                    }
                    ForEach(tasks) { task in taskCard(task) }
                }.padding(2)
            }
        }
        .padding(12).frame(maxHeight: .infinity)
        .background(.quaternary, in: RoundedRectangle(cornerRadius: 10))
        .contentShape(Rectangle())
        .dropDestination(for: String.self) { ids, _ in
            guard !model.busy, ids.count == 1, let id = ids.first,
                  model.board?.tasks.contains(where: { $0.id == id && $0.status != status }) == true else { return false }
            Task { await model.move(id, to: status) }
            return true
        }
    }

    private func taskCard(_ task: TrackerTask) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(task.title).font(.body.weight(.medium)).fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
            if task.kind == .daily {
                Label("Ежедневно", systemImage: "repeat").font(.caption).foregroundStyle(.secondary)
            }
            Text("\(task.stat.title) · \(difficultyTitle(task.difficulty))").font(.caption).foregroundStyle(.secondary)
            HStack {
                Text("+\(task.xp) XP").font(.caption.weight(.medium)).monospacedDigit()
                Spacer()
                if task.status != .todo { moveButton(task, to: task.status == .done ? .doing : .todo, symbol: "arrow.left") }
                if task.status != .done { moveButton(task, to: task.status == .todo ? .doing : .done, symbol: "arrow.right") }
                Button(role: .destructive) { pendingDelete = task } label: { Image(systemName: "trash") }
                    .help("Удалить задачу").accessibilityLabel("Удалить «\(task.title)»")
            }
        }
        .padding(12).frame(maxWidth: .infinity, alignment: .leading)
        .background(.background, in: RoundedRectangle(cornerRadius: 8))
        .overlay { RoundedRectangle(cornerRadius: 8).stroke(.separator, lineWidth: 0.5) }
        .draggable(task.id)
        .contextMenu {
            ForEach(TaskStatus.allCases.filter { $0 != task.status }) { status in
                Button(status.title) { Task { await model.move(task.id, to: status) } }
            }
            Divider()
            Button("Удалить…", role: .destructive) { pendingDelete = task }
        }
        .disabled(model.busy)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("\(task.title), \(task.status.title)")
    }

    private func moveButton(_ task: TrackerTask, to status: TaskStatus, symbol: String) -> some View {
        Button { Task { await model.move(task.id, to: status) } } label: { Image(systemName: symbol) }
            .help(status.title).accessibilityLabel("Переместить «\(task.title)»: \(status.title)")
    }

    private func difficultyTitle(_ value: Int) -> String {
        switch value { case 1: "Лёгкая"; case 2: "Средняя"; default: "Сложная" }
    }
}
