"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const tg = window.Telegram?.WebApp;
const initData = tg?.initData || "";
const demo = !initData && new URLSearchParams(location.search).get("demo") === "1";
const state = { summary: null, exercises: [], history: [], tab: "today", saving: false, pending: null, progressRun: 0 };
const fmt = (value) => new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 }).format(value || 0);
const dayKey = (date = new Date()) => { const parts = new Intl.DateTimeFormat("en", { timeZone: "Europe/Moscow", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(date); const get = (type) => parts.find((part) => part.type === type).value; return get("year") + "-" + get("month") + "-" + get("day"); };
const dateLabel = (date, options = { day: "numeric", month: "long" }) => new Intl.DateTimeFormat("ru-RU", { timeZone: "Europe/Moscow", ...options }).format(new Date(date.length === 10 ? date + "T12:00:00+03:00" : date));
const icon = (name) => { const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg"); const use = document.createElementNS(svg.namespaceURI, "use"); use.setAttribute("href", "#i-" + name); svg.append(use); return svg; };
const el = (tag, className, text) => { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; };
const uuid = () => crypto.randomUUID?.() || "10000000-1000-4000-8000-100000000000".replace(/[018]/g, (digit) => (digit ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> digit / 4).toString(16));

tg?.ready();
tg?.expand();
try { tg?.setHeaderColor("#111418"); tg?.setBackgroundColor("#111418"); } catch (_) { /* Older Telegram clients. */ }

class ApiError extends Error { constructor(message, status) { super(message); this.status = status; } }
async function api(url, options = {}) {
  if (demo) return demoApi(url, options);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(url, { ...options, signal: controller.signal, headers: { "X-Telegram-Init-Data": initData, ...(options.body ? { "Content-Type": "application/json" } : {}) } });
    let data;
    try { data = await response.json(); } catch (_) { throw new ApiError("Сервер временно недоступен. Попробуйте ещё раз.", response.status); }
    if (!response.ok) throw new ApiError(typeof data.detail === "string" ? data.detail : "Проверьте название упражнения, вес и повторения.", response.status);
    return data;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError("Не удалось связаться с сервером. Проверьте интернет и повторите.", 0);
  } finally { clearTimeout(timeout); }
}

function showNotice(error) {
  $("#notice-text").textContent = error.message;
  $("#retry").hidden = error.status === 401;
  $("#notice").hidden = false;
}
let toastTimeout;
function toast(message) {
  clearTimeout(toastTimeout);
  $("#toast").textContent = message;
  $("#toast").hidden = false;
  toastTimeout = setTimeout(() => { $("#toast").hidden = true; }, 4200);
}

async function loadData() {
  $("#main").setAttribute("aria-busy", "true");
  $("#notice").hidden = true;
  try {
    const [summary, exercises, history] = await Promise.all([api("/api/summary"), api("/api/exercises"), api("/api/history")]);
    Object.assign(state, { summary, exercises, history });
    renderToday(); renderHistory(); renderExerciseOptions();
    $("#screens").hidden = false; $("#navigation").hidden = false;
    $("#welcome").hidden = true;
    if (state.tab === "progress") await renderProgress();
  } catch (error) { showNotice(error); }
  finally { $("#loading").hidden = true; $("#main").setAttribute("aria-busy", "false"); }
}

function emptyState(title, description, action) {
  const node = el("div", "empty"); node.append(el("h3", "", title), el("p", "", description));
  if (action) { const button = el("button", "text-button", "Записать первое упражнение"); button.type = "button"; button.addEventListener("click", () => openRecord()); node.append(button); }
  return node;
}

function renderToday() {
  const summary = state.summary;
  const name = summary.user?.first_name;
  $("#today-heading").textContent = name ? name + ", твоя тренировка" : "Твоя тренировка";
  $("#avatar").textContent = (name || "G").slice(0, 1).toUpperCase();
  $("#today-date").textContent = dateLabel(new Date().toISOString(), { weekday: "long", day: "numeric", month: "long" }).toUpperCase();
  $("#week-count").textContent = fmt(summary.week_sessions);
  $("#total-count").textContent = fmt(summary.total_sessions);
  $("#today-sets").textContent = fmt(summary.today?.sets);
  $("#today-volume").textContent = fmt(summary.today?.volume);
  $("#hero-description").textContent = summary.today ? "Сегодня уже " + fmt(summary.today.sets) + " подходов. Продолжай в своём темпе." : "Запиши первый подход. И двигайся к своему следующему рекорду.";
  const strip = $("#week-strip"); strip.replaceChildren();
  const workoutDays = new Set(state.history.map((workout) => workout.date));
  for (let index = 6; index >= 0; index--) {
    const date = new Date(Date.now() - index * 86400000), key = dayKey(date), done = workoutDays.has(key);
    const item = el("div", "week-day" + (done ? " done" : "") + (index === 0 ? " today" : ""));
    item.title = dateLabel(date.toISOString()) + (done ? " — была тренировка" : " — без тренировки");
    item.append(el("span", "", dateLabel(date.toISOString(), { weekday: "short" })), el("div", "", done ? "✓" : dateLabel(date.toISOString(), { day: "numeric" })));
    strip.append(item);
  }
  renderExercises();
}

function filteredExercises(exercises, query) {
  const normalize = (value) => value.trim().toLocaleLowerCase("ru").replace(/ё/g, "е");
  const search = normalize(query);
  return exercises.filter((exercise) => normalize(exercise.exercise).includes(search));
}

function renderExercises() {
  const list = $("#exercise-list"); list.replaceChildren();
  const exercises = filteredExercises(state.exercises, $("#exercise-search").value);
  $("#exercise-count").textContent = fmt(state.exercises.length);
  if (!state.exercises.length) list.append(emptyState("Первый подход — начало прогресса", "Добавь упражнение и начни свой дневник.", true));
  else if (!exercises.length) list.append(emptyState("Пока не нашли упражнение", "Попробуй другое название или добавь своё упражнение.", false));
  for (const exercise of exercises) {
    const row = el("button", "exercise-row"); row.type = "button";
    const badge = el("span", "exercise-icon"); badge.append(icon("gym"));
    const copy = el("span", "exercise-copy");
    copy.append(el("strong", "", exercise.exercise), el("small", "", exercise.sets_count ? "Всего подходов: " + fmt(exercise.sets_count) + (exercise.last_date ? " · " + dateLabel(exercise.last_date) : "") : "Готово к первой тренировке"));
    const arrow = icon("chevron"); arrow.classList.add("row-arrow");
    row.append(badge, copy, arrow); row.addEventListener("click", () => openRecord(exercise.exercise)); list.append(row);
  }
}

function renderHistory() {
  const list = $("#history-list"); list.replaceChildren();
  if (!state.history.length) { list.append(emptyState("Твоя история ещё впереди", "Сохранённые тренировки появятся здесь.", true)); return; }
  for (const workout of state.history) {
    const details = el("details", "history-item"), summary = el("summary"), copy = el("div");
    copy.append(el("h3", "", dateLabel(workout.date, { day: "numeric", month: "long", year: "numeric" })), el("p", "", workout.exercises.join(" · ")));
    const volume = el("div", "history-volume"); volume.append(el("strong", "", fmt(workout.volume) + " кг"), el("span", "", fmt(workout.sets) + " подходов · раскрыть")); summary.append(copy, volume);
    const entries = el("div", "history-entries");
    for (const entry of workout.entries) { const row = el("div", "history-entry"); row.append(el("span", "", entry.exercise), el("span", "", fmt(entry.weight) + " кг × " + fmt(entry.reps))); entries.append(row); }
    details.append(summary, entries); list.append(details);
  }
}

function renderExerciseOptions() {
  const selected = $("#progress-exercise").value;
  $("#exercise-options").replaceChildren(); $("#progress-exercise").replaceChildren();
  for (const item of state.exercises) {
    const option = el("option", "", item.exercise); option.value = item.exercise;
    $("#exercise-options").append(option.cloneNode(true)); $("#progress-exercise").append(option);
  }
  if (state.exercises.some((item) => item.exercise === selected)) $("#progress-exercise").value = selected;
  $("#progress-exercise").disabled = !state.exercises.length;
}

async function renderProgress() {
  const run = ++state.progressRun, exercise = $("#progress-exercise").value, container = $("#progress-content");
  container.replaceChildren();
  if (!exercise) { container.append(emptyState("У каждого рекорда есть начало", "Запиши тренировку, чтобы увидеть свой прогресс.", true)); return; }
  container.append(el("p", "chart-subtitle", "Загружаем прогресс…"));
  try {
    const data = await api("/api/exercise/history?exercise=" + encodeURIComponent(exercise));
    if (run !== state.progressRun) return;
    container.replaceChildren();
    if (!data.sessions.length) { container.append(emptyState("Пока нет записанных подходов", "Запиши первую тренировку для этого упражнения.", true)); return; }
    const stats = el("div", "progress-stats");
    for (const [label, value] of [["Максимальный вес", fmt(data.max_weight) + " кг"], ["Тренировок на графике", fmt(data.sessions.length)], ["Общий объём", fmt(data.total_volume) + " кг"]]) {
      const card = el("div", "progress-stat"); card.append(el("span", "", label), el("strong", "", value)); stats.append(card);
    }
    const card = el("section", "chart-card"); card.append(el("h2", "", "Сильнее с каждой тренировкой"), el("p", "chart-subtitle", "Максимальный вес за тренировку · последние 20 тренировок"), progressChart(data.sessions));
    container.append(stats, card);
    const table = el("div", "history-list"); table.style.marginTop = "20px";
    for (const point of [...data.sessions].reverse()) {
      const row = el("div", "history-item"), content = el("div", "history-entry"); content.style.padding = "17px";
      content.append(el("span", "", dateLabel(point.date)), el("span", "", fmt(point.top_weight) + " кг · " + fmt(point.sets) + " подходов")); row.append(content); table.append(row);
    }
    container.append(table);
  } catch (error) { if (run === state.progressRun) { container.replaceChildren(emptyState("Не удалось загрузить прогресс", error.message)); const retry = el("button", "text-button", "Повторить"); retry.addEventListener("click", renderProgress); container.append(retry); } }
}

function progressChart(points) {
  const ns = "http://www.w3.org/2000/svg", svg = document.createElementNS(ns, "svg");
  svg.classList.add("progress-chart"); svg.setAttribute("viewBox", "0 0 620 240"); svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Максимальный вес: " + points.map((point) => dateLabel(point.date) + ", " + fmt(point.top_weight) + " кг").join("; "));
  const add = (tag, attrs, text) => { const node = document.createElementNS(ns, tag); for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value); if (text !== undefined) node.textContent = text; svg.append(node); return node; };
  const max = Math.max(...points.map((point) => point.top_weight), 1) * 1.15;
  const x = (index) => points.length === 1 ? 330 : 52 + index * 548 / (points.length - 1);
  const y = (weight) => 194 - weight / max * 165;
  for (let index = 0; index <= 3; index++) { const weight = max * index / 3, pos = y(weight); add("line", { x1: 52, x2: 600, y1: pos, y2: pos, class: "chart-grid" }); add("text", { x: 0, y: pos + 4 }, fmt(weight)); }
  add("polyline", { points: points.map((point, index) => x(index) + "," + y(point.top_weight)).join(" "), class: "chart-line" });
  points.forEach((point, index) => { const circle = add("circle", { cx: x(index), cy: y(point.top_weight), r: 5 }); const title = document.createElementNS(ns, "title"); title.textContent = dateLabel(point.date) + ": " + fmt(point.top_weight) + " кг"; circle.append(title); });
  const labels = [...new Set([0, Math.floor((points.length - 1) / 2), points.length - 1])];
  for (const index of labels) add("text", { x: x(index), y: 226, "text-anchor": index === 0 && points.length > 1 ? "start" : index === points.length - 1 && points.length > 1 ? "end" : "middle" }, dateLabel(points[index].date, { day: "numeric", month: "short" }));
  return svg;
}

function setTab(tab) {
  state.tab = tab;
  for (const screen of $$(".screen")) screen.hidden = screen.id !== "screen-" + tab;
  for (const button of $$("[data-tab]")) { const active = button.dataset.tab === tab; button.classList.toggle("active", active); if (active) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current"); }
  if (tab === "progress") renderProgress();
  window.scrollTo({ top: 0, behavior: "instant" });
}

function openRecord(exercise = "") {
  state.pending = null;
  $("#record-form").reset(); $("#exercise-name").value = exercise;
  $("#set-rows").replaceChildren(); $("#form-error").hidden = true;
  const last = state.history.flatMap((workout) => workout.entries).find((entry) => entry.exercise === exercise);
  $("#last-entry").textContent = last ? "Последний подход: " + fmt(last.weight) + " кг × " + fmt(last.reps) : "Выбери знакомое упражнение или введи новое название.";
  for (let index = 0; index < 3; index++) addSet(last?.weight ?? "", last?.reps ?? "");
  $("#record-dialog").showModal();
  tg?.BackButton?.show();
  setTimeout(() => (exercise ? $(".weight-input") : $("#exercise-name")).focus(), 50);
}

function addSet(weight = "", reps = "") {
  if ($$(".set-row").length >= 30) { toast("Максимум 30 подходов за одну запись"); return; }
  const row = el("div", "set-row"), number = el("span", "set-number");
  const weightInput = el("input", "weight-input"); weightInput.type = "number"; weightInput.inputMode = "decimal"; weightInput.min = "0"; weightInput.max = "2000"; weightInput.step = "0.1"; weightInput.placeholder = "0"; weightInput.required = true; weightInput.value = weight;
  const repsInput = el("input", "reps-input"); repsInput.type = "number"; repsInput.inputMode = "numeric"; repsInput.min = "1"; repsInput.max = "1000"; repsInput.step = "1"; repsInput.placeholder = "10"; repsInput.required = true; repsInput.value = reps;
  const remove = el("button", "remove-set"); remove.type = "button"; remove.append(icon("close"));
  remove.addEventListener("click", () => { row.remove(); renumber(); updateVolume(); });
  row.append(number, weightInput, repsInput, remove); $("#set-rows").append(row); renumber(); updateVolume();
}
function renumber() { const rows = $$(".set-row"); rows.forEach((row, index) => { $(".set-number", row).textContent = index + 1; $(".weight-input", row).setAttribute("aria-label", "Вес в кг, подход " + (index + 1)); $(".reps-input", row).setAttribute("aria-label", "Повторения, подход " + (index + 1)); $(".remove-set", row).setAttribute("aria-label", "Удалить подход " + (index + 1)); $(".remove-set", row).disabled = rows.length <= 1; }); }
function updateVolume() { const total = $$(".set-row").reduce((sum, row) => sum + Number($(".weight-input", row).value) * Number($(".reps-input", row).value), 0); $("#form-volume").textContent = fmt(total) + " кг"; }
function closeRecord() { if (!state.saving) $("#record-dialog").close(); }

async function saveRecord(event) {
  event.preventDefault(); if (state.saving) return;
  const exercise = $("#exercise-name").value.trim();
  const entries = $$(".set-row").map((row) => ({ weight: Number($(".weight-input", row).value), reps: Number($(".reps-input", row).value) }));
  if (!exercise || !entries.length || entries.some((entry) => !Number.isInteger(entry.reps) || entry.reps < 1 || entry.reps > 1000 || !Number.isFinite(entry.weight) || entry.weight < 0 || entry.weight > 2000)) {
    $("#form-error").textContent = "Укажи упражнение, вес от 0 до 2000 кг и целые повторения от 1 до 1000."; $("#form-error").hidden = false; return;
  }
  const fingerprint = JSON.stringify({ exercise, entries });
  if (!state.pending || state.pending.fingerprint !== fingerprint) state.pending = { fingerprint, request_id: uuid() };
  state.saving = true; $("#form-error").hidden = true;
  const controls = $$("input,button", $("#record-form")); controls.forEach((control) => { control.disabled = true; });
  $("#save-record span").textContent = "Сохраняем…";
  try {
    await api("/api/records", { method: "POST", body: JSON.stringify({ exercise, entries, request_id: state.pending.request_id }) });
    state.pending = null; $("#record-dialog").close();
    toast(demo ? "Сохранено в демодневнике этого браузера" : "Подходы сохранены. Время на отдых!");
    tg?.HapticFeedback?.notificationOccurred("success");
    startTimer(); setTab("today"); await loadData();
  } catch (error) { $("#form-error").textContent = error.message; $("#form-error").hidden = false; }
  finally { state.saving = false; controls.forEach((control) => { control.disabled = false; }); renumber(); $("#save-record span").textContent = "Сохранить подходы"; }
}

const timer = { duration: 90, remaining: 90, end: 0, interval: null };
function renderTimer() {
  const seconds = Math.max(0, Math.ceil(timer.remaining));
  const label = String(Math.floor(seconds / 60)).padStart(2, "0") + ":" + String(seconds % 60).padStart(2, "0");
  $("#timer-display").textContent = label;
  $("#timer-toggle").textContent = timer.interval ? "Пауза" : seconds < timer.duration && seconds > 0 ? "Продолжить" : "Начать отдых";
  $("#rest-remaining").textContent = label;
  $("#rest-dock").hidden = !(timer.interval || (seconds < timer.duration && seconds > 0));
  $("#rest-pause").textContent = timer.interval ? "Пауза" : "Продолжить";
}
function stopTimer() { if (timer.interval) timer.remaining = Math.max(0, (timer.end - Date.now()) / 1000); clearInterval(timer.interval); timer.interval = null; renderTimer(); }
function tickTimer() {
  timer.remaining = Math.max(0, (timer.end - Date.now()) / 1000); renderTimer();
  if (timer.remaining <= 0) { stopTimer(); toast("Отдых закончен. Готов к следующему подходу?"); tg?.HapticFeedback?.notificationOccurred("success"); }
}
function startTimer(resume = false) { stopTimer(); if (!resume || timer.remaining <= 0) timer.remaining = timer.duration; timer.end = Date.now() + timer.remaining * 1000; timer.interval = setInterval(tickTimer, 250); renderTimer(); }

// Demo is isolated from the authenticated API and never sends fake Telegram users.
let demoStore;
function seedDemo() {
  const records = [];
  for (let session = 0; session < 5; session++) {
    const stamp = new Date(Date.now() - (session * 3 + 1) * 86400000).toISOString(), day = dayKey(new Date(stamp));
    for (const [exercise, weight] of [["Жим лёжа", 70 - session * 2.5], ["Приседания со штангой", 85 - session * 2.5], ["Тяга верхнего блока", 50 - session * 2.5]]) {
      for (let set = 0; set < 3; set++) records.push({ exercise, weight, reps: 10 - set, date: stamp, day });
    }
  }
  return { records, submissions: [] };
}
function readDemo() { if (!demoStore) { try { const stored = JSON.parse(localStorage.getItem("gym-demo-v1")); if (Array.isArray(stored?.records) && Array.isArray(stored?.submissions)) demoStore = stored; } catch (_) {} demoStore ||= seedDemo(); } return demoStore; }
function demoSessions(records) {
  const days = new Map(); for (const record of records) { if (!days.has(record.day)) days.set(record.day, []); days.get(record.day).push(record); }
  return [...days].sort(([a], [b]) => b.localeCompare(a)).map(([date, entries]) => ({ date, entries, sets: entries.length, volume: entries.reduce((sum, entry) => sum + entry.weight * entry.reps, 0), exercises: [...new Set(entries.map((entry) => entry.exercise))] }));
}
async function demoApi(url, options) {
  const store = readDemo(), workouts = demoSessions(store.records), parsed = new URL(url, location.origin);
  if (parsed.pathname === "/api/records") {
    const payload = JSON.parse(options.body);
    if (store.submissions.includes(payload.request_id)) return { status: "ok", duplicate: true, added: 0 };
    store.records.unshift(...payload.entries.map((entry) => ({ ...entry, exercise: payload.exercise, date: new Date().toISOString(), day: dayKey() })));
    store.submissions.push(payload.request_id);
    try { localStorage.setItem("gym-demo-v1", JSON.stringify(store)); } catch (_) { toast("Демо сохранено на время этого просмотра"); }
    return { status: "ok", added: payload.entries.length };
  }
  if (parsed.pathname === "/api/history") return workouts.slice(0, 30);
  if (parsed.pathname === "/api/exercises") return [...new Set(store.records.map((entry) => entry.exercise))].map((exercise) => ({ exercise, sets_count: store.records.filter((entry) => entry.exercise === exercise).length, last_date: store.records.find((entry) => entry.exercise === exercise).date }));
  if (parsed.pathname === "/api/summary") {
    const today = dayKey(), weekStart = dayKey(new Date(Date.now() - 6 * 86400000));
    return { user: { first_name: "" }, total_sessions: workouts.length, week_sessions: workouts.filter((workout) => workout.date >= weekStart && workout.date <= today).length, last_workout: store.records[0]?.date, today: workouts.find((workout) => workout.date === today) || null };
  }
  if (parsed.pathname === "/api/exercise/history") {
    const records = store.records.filter((entry) => entry.exercise === parsed.searchParams.get("exercise"));
    return { max_weight: Math.max(0, ...records.map((entry) => entry.weight)), total_volume: records.reduce((sum, entry) => sum + entry.weight * entry.reps, 0), sessions: demoSessions(records).reverse().slice(-20).map((workout) => ({ date: workout.date, top_weight: Math.max(...workout.entries.map((entry) => entry.weight)), sets: workout.sets, volume: workout.volume })) };
  }
  throw new ApiError("Неизвестный запрос деморежима", 404);
}

$("#try-demo").addEventListener("click", () => { location.search = "?demo=1"; });
$("#retry").addEventListener("click", loadData);
$("#home").addEventListener("click", () => { if (state.summary) setTab("today"); });
$$("[data-tab]").forEach((button) => button.addEventListener("click", () => setTab(button.dataset.tab)));
$$("[data-quick-tab]").forEach((button) => button.addEventListener("click", () => setTab(button.dataset.quickTab)));
$("#nav-record").addEventListener("click", () => openRecord());
$("#start-workout").addEventListener("click", () => openRecord());
$("#new-exercise").addEventListener("click", () => openRecord($("#exercise-search").value.trim()));
$("#exercise-search").addEventListener("input", renderExercises);
$("#progress-exercise").addEventListener("change", renderProgress);
$("#add-set").addEventListener("click", () => { const last = $$(".set-row").at(-1); addSet(last ? $(".weight-input", last).value : "", last ? $(".reps-input", last).value : ""); });
$("#record-form").addEventListener("input", updateVolume);
$("#record-form").addEventListener("submit", saveRecord);
$("#close-dialog").addEventListener("click", closeRecord);
$("#record-dialog").addEventListener("cancel", (event) => { if (state.saving) event.preventDefault(); });
$("#record-dialog").addEventListener("close", () => tg?.BackButton?.hide());
tg?.BackButton?.onClick(closeRecord);
$("#timer-toggle").addEventListener("click", () => timer.interval ? stopTimer() : startTimer(true));
$("#rest-pause").addEventListener("click", () => timer.interval ? stopTimer() : startTimer(true));
$("#open-rest").addEventListener("click", () => { setTab("today"); $(".timer-card").scrollIntoView({ behavior: "smooth", block: "center" }); });
$("#timer-reset").addEventListener("click", () => { stopTimer(); timer.remaining = timer.duration; renderTimer(); });
$$("[data-seconds]").forEach((button) => button.addEventListener("click", () => { stopTimer(); timer.duration = Number(button.dataset.seconds); timer.remaining = timer.duration; $$("[data-seconds]").forEach((preset) => { const active = preset === button; preset.classList.toggle("selected", active); preset.setAttribute("aria-pressed", String(active)); }); renderTimer(); }));
document.addEventListener("visibilitychange", () => { if (!document.hidden && timer.interval) tickTimer(); });
renderTimer();
if (demo || initData) { $("#demo-banner").hidden = !demo; loadData(); }
else { $("#loading").hidden = true; $("#welcome").hidden = false; $("#main").setAttribute("aria-busy", "false"); }
