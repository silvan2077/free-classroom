"use strict";

const byId = (id) => document.getElementById(id);
const weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"];
const panels = ["query", "schedule", "import", "settings"];
const filters = byId("filters");
const weekInput = byId("week");
const startPeriodInput = byId("start-period");
const endPeriodInput = byId("end-period");
const resultContent = byId("result-content");
const scheduleForm = byId("schedule-filters");
const scheduleContent = byId("schedule-content");
const editDialog = byId("edit-dialog");
const editForm = byId("edit-form");
const importForm = byId("import-form");
const fileInput = byId("csv-files");
const previewButton = byId("preview-button");
const importButton = byId("import-button");
const settingsForm = byId("settings-form");
const resetDialog = byId("reset-dialog");

let state;
let files = [];
let csvEditIndex;
let previewToken = null;
let validPreviewIndexes = [];
let importBusy = false;
let querySequence = 0;
let scheduleSequence = 0;
let previewSequence = 0;
let editSelection;

function node(tag, content, className) {
  const element = document.createElement(tag);
  if (content !== undefined) element.textContent = content;
  if (className) element.className = className;
  return element;
}

function notice(id, message = "", tone = "") {
  const element = byId(id);
  element.textContent = message;
  element.className = tone ? `notice ${tone}` : "notice";
  element.hidden = !message;
}

async function api(path, options) {
  const response = await fetch(path, options);
  const payload = await response.json().catch(() => null);
  if (!response.ok || !payload) {
    throw new Error(payload?.error || `请求失败，请重试（${response.status}）。`);
  }
  return payload;
}

function jsonRequest(method, value) {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) };
}

function showPanel() {
  const requested = location.hash.slice(1);
  const selected = panels.includes(requested) ? requested : "query";
  for (const panel of panels) {
    byId(`${panel}-panel`).hidden = panel !== selected;
  }
  for (const link of document.querySelectorAll("[data-panel]")) {
    if (link.dataset.panel === selected) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  if (selected === "schedule") updateRoomSchedule();
}

function groupLabel(group) {
  return `第 ${group.replace("-", "–")} 节`;
}

function groupPeriods(group) {
  const [first, last = first] = group.split("-").map(Number);
  return Array.from({ length: last - first + 1 }, (_, index) => first + index);
}

function fillEndPeriods(previous) {
  endPeriodInput.disabled = !startPeriodInput.value;
  if (endPeriodInput.disabled) {
    endPeriodInput.replaceChildren(new Option("—", ""));
    return;
  }
  const start = Number(startPeriodInput.value);
  const configured = new Set(state.settings.groups.flatMap(groupPeriods));
  const choices = [];
  for (let period = start; configured.has(period); period += 1) {
    choices.push(new Option(`第 ${period} 节`, period));
  }
  endPeriodInput.replaceChildren(...choices);
  endPeriodInput.value = choices.some((option) => option.value === previous) ? previous : start;
}

function weekDate(week, weekday) {
  const date = new Date(`${state.settings.start}T12:00:00`);
  date.setDate(date.getDate() + (week - 1) * 7 + weekday - 1);
  return date;
}

const dateFormat = new Intl.DateTimeFormat("zh-CN", { month: "long", day: "numeric" });

function selectedValues(container) {
  return Array.from(byId(container).querySelectorAll("input:checked"), (input) => input.value);
}

function renderOptions(container, values, selected, singular) {
  const list = byId(container);
  list.replaceChildren();
  if (!values.length) {
    list.append(node("p", `暂无可选${singular}`, "help"));
    return;
  }
  for (const value of values) {
    const label = node("label", undefined, "check-option");
    const checkbox = node("input");
    checkbox.type = "checkbox";
    checkbox.value = value;
    checkbox.checked = selected.includes(value);
    label.append(checkbox, node("span", value));
    list.append(label);
  }
}

function fillWeeks(select, previous) {
  select.replaceChildren(new Option("选择教学周", ""));
  for (let week = 1; week <= state.settings.weeks; week += 1) {
    const range = `${dateFormat.format(weekDate(week, 1))} – ${dateFormat.format(weekDate(week, 7))}`;
    select.add(new Option(`第 ${week} 周 · ${range}`, week));
  }
  select.value = previous !== null && Number(previous) <= state.settings.weeks ? previous : (state.week ?? "");
}

function applyState(next, preserveQuery = false, returnToToday = false, renamedFrom = null) {
  const previousWeek = weekInput.value;
  const previousStart = startPeriodInput.value;
  const previousEnd = endPeriodInput.value;
  const previousWeekday = filters.elements.weekday.value;
  const selectedRooms = selectedValues("rooms").map((room) => room === renamedFrom ? next.renamed_room : room);
  const selectedCourses = selectedValues("courses");
  state = next;

  const preserveDate = preserveQuery && !returnToToday;
  fillWeeks(weekInput, preserveDate ? previousWeek : null);

  const { room, week, weekday } = scheduleForm.elements;
  const previousRoom = room.value === renamedFrom ? next.renamed_room : room.value;
  const previousDay = weekday.value;
  fillWeeks(week, preserveQuery ? week.value : null);
  room.replaceChildren(...state.rooms.map((name) => new Option(name, name)));
  if (preserveQuery && state.rooms.includes(previousRoom)) room.value = previousRoom;
  room.disabled = !state.rooms.length;
  const renameButton = byId("room-rename-open");
  if (renameButton) renameButton.disabled = room.disabled;
  weekday.replaceChildren(...weekdays.map((name, index) => new Option(name, index + 1)));
  weekday.value = preserveQuery && previousDay ? previousDay : state.weekday;

  const periods = state.settings.groups.flatMap(groupPeriods);
  startPeriodInput.replaceChildren(new Option("全天", ""), ...periods.map((period) => new Option(`第 ${period} 节`, period)));
  if (preserveDate && periods.includes(Number(previousStart))) startPeriodInput.value = previousStart;
  fillEndPeriods(preserveDate ? previousEnd : "");
  filters.elements.weekday.value = preserveDate && previousWeekday ? previousWeekday : state.weekday;
  byId("today-button").disabled = false;

  renderOptions("rooms", state.rooms, preserveQuery ? selectedRooms : [], "教室");
  renderOptions("courses", state.courses, preserveQuery ? selectedCourses : [], "课程");
  byId("term-start").value = state.settings.start;
  byId("term-weeks").value = state.settings.weeks;
  byId("term-groups").value = state.settings.groups.join(",");
  updateQuery();
  updateRoomSchedule();
}

function renderResults(groups) {
  const results = document.createDocumentFragment();
  let total = 0;
  for (const { group, rooms } of groups) {
    const section = node("section", undefined, "card result-group");
    const heading = node("div", undefined, "group-heading");
    const free = rooms.filter(({ lessons }) => lessons.length === 0).length;
    const matching = rooms.length - free;
    total += rooms.length;
    heading.append(node("h3", groupLabel(group)), node("span", matching ? `${free} 间空闲 · ${matching} 间课程命中` : `${free} 间空闲`, "group-count"));
    section.append(heading);
    if (!rooms.length) {
      section.append(node("p", "没有符合条件的教室。", "group-empty"));
    } else {
      const list = node("ul", undefined, "room-list");
      for (const { room, lessons } of rooms) {
        const item = node("li", undefined, "room-result");
        const line = node("div", undefined, "room-line");
        line.append(node("strong", room), node("span", lessons.length ? "课程命中" : "空闲", `pill ${lessons.length ? "pill-course" : "pill-free"}`));
        item.append(line);
        for (const { course, periods } of lessons) {
          item.append(node("p", `${course} · 第 ${periods.join("、")} 节`, "lesson"));
        }
        list.append(item);
      }
      section.append(list);
    }
    results.append(section);
  }
  resultContent.replaceChildren(results);
  byId("result-status").textContent = `共 ${total} 条结果`;
}

async function updateQuery() {
  if (!state) return;
  const sequence = ++querySequence;
  const week = Number(weekInput.value);
  const weekday = Number(filters.elements.weekday.value);
  const roomValues = selectedValues("rooms");
  const courseValues = selectedValues("courses");
  byId("room-summary").textContent = roomValues.length ? `已选 ${roomValues.length} 间` : "全部教室";
  byId("course-summary").textContent = courseValues.length ? `已选 ${courseValues.length} 门` : "只查空闲";
  byId("result-status").textContent = "";
  byId("query-date").textContent = week && weekday
    ? `第 ${week} 周 · ${dateFormat.format(weekDate(week, weekday))} · ${weekdays[weekday - 1]}`
    : state.week === null ? "今天不在当前学期的教学周内。" : "请选择教学周。";

  if (!week || !weekday) {
    resultContent.replaceChildren(node("p", "选择教学周和星期后查看教室。", "card placeholder"));
    return;
  }
  if (!state.rooms.length) {
    const empty = node("div", undefined, "card placeholder");
    const link = node("a", "导入课表", "button button-primary");
    link.href = "#import";
    empty.append(node("p", "当前学期还没有教室课表。"), link);
    resultContent.replaceChildren(empty);
    return;
  }

  const params = new URLSearchParams({ week, weekday, revision: state.revision });
  if (startPeriodInput.value) params.set("periods", `${startPeriodInput.value}-${endPeriodInput.value}`);
  for (const room of roomValues) params.append("room", room);
  for (const course of courseValues) params.append("course", course);
  resultContent.replaceChildren(node("p", "正在查询…", "card placeholder"));
  try {
    const response = await api(`/api/query?${params}`);
    if (sequence === querySequence) renderResults(response.groups);
  } catch (error) {
    if (sequence === querySequence) {
      const message = node("p", error.message, "notice error");
      message.setAttribute("role", "alert");
      resultContent.replaceChildren(message);
    }
  }
}

function openCourseEditor(selection, group, periods, current) {
  editSelection = { ...selection, group, previous: periods.map((period) => current.get(period) ?? "") };
  byId("edit-context").textContent = `${selection.room} · 第 ${selection.week} 周 · ${weekdays[selection.weekday - 1]} · ${groupLabel(group)}`;
  byId("edit-periods").replaceChildren(...periods.map((period, index) => {
    const label = node("label", undefined, "field");
    const input = node("input");
    input.type = "text";
    input.value = editSelection.previous[index];
    input.placeholder = "课程名";
    label.append(node("span", `第 ${period} 节`), input);
    return label;
  }));
  notice("edit-error");
  editDialog.showModal();
}

function renderRoomSchedule(groups, selection) {
  scheduleContent.replaceChildren(...groups.map(({ group, rooms }) => {
    const lessons = rooms[0].lessons;
    const periods = groupPeriods(group);
    const occupied = new Map(lessons.flatMap(({ course, periods }) => periods.map((period) => [period, course])));
    const section = node("section", undefined, "card result-group");
    const heading = node("div", undefined, "group-heading");
    const edit = node("button", lessons.length ? "编辑课程" : "添加课程", "button button-outline");
    edit.type = "button";
    edit.setAttribute("aria-label", `${groupLabel(group)}${edit.textContent}`);
    edit.addEventListener("click", () => openCourseEditor(selection, group, periods, occupied));
    heading.append(node("h3", groupLabel(group)), edit);
    section.append(heading, renderScheduleLessons(lessons, periods));
    return section;
  }));
}

function renderScheduleLessons(lessons, periods, compact = false) {
  const list = node("ul", undefined, "schedule-lessons");
  const occupied = new Set(lessons.flatMap((lesson) => lesson.periods));
  const free = periods.filter((period) => !occupied.has(period));
  const items = [...lessons];
  if (free.length) items.push({ course: "", periods: free });
  for (const item of items) {
    const line = node("li");
    if (!compact || items.length > 1) {
      line.append(node("span", `第 ${item.periods.join("、")} 节`, "help"));
    }
    line.append(node("span", item.course || "空闲", item.course ? undefined : "pill pill-free"));
    list.append(line);
  }
  return list;
}

function renderRoomWeek(groups, week) {
  const container = node("div", undefined, "card week-table-scroll");
  container.tabIndex = 0;
  container.setAttribute("role", "region");
  container.setAttribute("aria-label", "教室周课表");
  const table = node("table", undefined, "week-table");
  table.setAttribute("aria-labelledby", "schedule-results-title");
  const header = table.createTHead().insertRow();
  const groupHeader = node("th", "节次");
  groupHeader.scope = "col";
  header.append(groupHeader);
  weekdays.forEach((name, index) => {
    const weekday = index + 1;
    const cell = node("th");
    cell.scope = "col";
    const button = node("button", name, "week-day-button");
    button.type = "button";
    button.setAttribute("aria-label", `查看${name}课表`);
    button.append(node("span", dateFormat.format(weekDate(week, weekday)), "help"));
    button.addEventListener("click", () => {
      scheduleForm.elements.view.value = "day";
      scheduleForm.elements.weekday.value = weekday;
      updateRoomSchedule();
      scheduleForm.elements.weekday.focus();
    });
    cell.append(button);
    header.append(cell);
  });

  const body = table.createTBody();
  for (const { group, days } of groups) {
    const row = body.insertRow();
    const label = node("th", groupLabel(group));
    label.scope = "row";
    row.append(label);
    const periods = groupPeriods(group);
    for (const lessons of days) row.insertCell().append(renderScheduleLessons(lessons, periods, true));
  }
  container.append(table);
  scheduleContent.replaceChildren(container);
}

async function updateRoomSchedule() {
  if (!state || byId("schedule-panel").hidden) return;
  const sequence = ++scheduleSequence;
  const fields = scheduleForm.elements;
  const room = fields.room.value;
  const week = Number(fields.week.value);
  const weekday = Number(fields.weekday.value);
  const weekly = fields.view.value === "week";
  byId("schedule-layout").classList.toggle("week-layout", weekly);
  byId("schedule-weekday-field").hidden = weekly;
  byId("schedule-results-title").textContent = weekly ? "周课表" : "全天课表";
  const dayLabel = (day) => dateFormat.format(weekDate(week, day));
  byId("schedule-date").textContent = week
    ? `第 ${week} 周 · ${weekly ? `${dayLabel(1)} – ${dayLabel(7)}` : `${dayLabel(weekday)} · ${weekdays[weekday - 1]}`}` : "";
  if (!room || !week) {
    scheduleContent.replaceChildren(node("p", room ? "请选择教学周。" : "请先导入教室课表。", "card placeholder"));
    return;
  }
  const selection = { room, week, weekday, revision: state.revision };
  const params = new URLSearchParams({ room, week, revision: state.revision });
  if (!weekly) params.set("weekday", weekday);
  scheduleContent.replaceChildren(node("p", "正在加载课表…", "card placeholder"));
  try {
    const endpoint = weekly ? "room-week" : "room-schedule";
    const { groups } = await api(`/api/${endpoint}?${params}`);
    if (sequence !== scheduleSequence) return;
    if (weekly) renderRoomWeek(groups, week);
    else renderRoomSchedule(groups, selection);
  } catch (error) {
    if (sequence === scheduleSequence) {
      scheduleContent.replaceChildren(node("p", error.message, "notice error"));
    }
  }
}

function clearPreview() {
  previewSequence += 1;
  previewToken = null;
  validPreviewIndexes = [];
  importButton.disabled = true;
  byId("import-preview").hidden = true;
  byId("preview-list").replaceChildren();
}

function setImportBusy(busy) {
  importBusy = busy;
  fileInput.disabled = busy;
  for (const input of byId("import-rows").querySelectorAll("input, button")) {
    const fullTerm = input.classList.contains("week-range") && byId(`scope-${input.dataset.index}`).checked;
    input.disabled = busy || fullTerm;
  }
  previewButton.disabled = busy || !files.length;
  importButton.disabled = busy || !previewToken;
}

function renderImportRows(drafts = []) {
  const rows = byId("import-rows");
  rows.replaceChildren();
  files.forEach((file, index) => {
    const draft = drafts[index];
    const row = node("fieldset", undefined, "file-row");
    row.append(node("legend", file.name));

    const roomLabel = node("label", undefined, "field");
    const room = node("input");
    room.id = `room-${index}`;
    room.type = "text";
    room.required = true;
    room.value = draft?.room ?? file.name.replace(/\.csv$/i, "");
    room.placeholder = "例如 教3404（西）";
    roomLabel.append(node("span", "教室编号"), room);

    const weekLabel = node("label", undefined, "field");
    const weeks = node("input", undefined, "week-range");
    weeks.id = `weeks-${index}`;
    weeks.type = "text";
    weeks.dataset.index = index;
    weeks.placeholder = "例如 7-10";
    weeks.required = true;
    weeks.value = draft?.weeks ?? "";
    weekLabel.append(node("span", "替换周次"), weeks);

    const scopeLabel = node("label", undefined, "check-option scope-option");
    const scope = node("input");
    scope.type = "checkbox";
    scope.id = `scope-${index}`;
    scope.checked = draft?.wholeTerm ?? true;
    scope.addEventListener("change", () => {
      weeks.disabled = scope.checked;
      if (!scope.checked) weeks.focus();
    });
    scopeLabel.append(scope, node("span", "整个学期"));
    const remove = node("button", "移除", "button button-outline remove-file");
    remove.type = "button";
    remove.setAttribute("aria-label", `移除 ${file.name}`);
    remove.addEventListener("click", () => {
      const hadPreview = !byId("import-preview").hidden;
      removeImportFiles(new Set([index]));
      clearPreview();
      notice("import-status", hadPreview && files.length ? "已移除文件，请重新预览剩余文件。" : "已移除文件。");
      const remaining = byId("import-rows").querySelectorAll(".remove-file");
      (remaining[Math.min(index, remaining.length - 1)] ?? fileInput).focus();
    });
    row.append(roomLabel, weekLabel, scopeLabel, remove);
    rows.append(row);
  });
  setImportBusy(importBusy);
}

function importDraft(index) {
  return {
    room: byId(`room-${index}`).value,
    weeks: byId(`weeks-${index}`).value,
    wholeTerm: byId(`scope-${index}`).checked,
  };
}

function removeImportFiles(indexes) {
  const drafts = [];
  files = files.filter((_file, index) => {
    if (indexes.has(index)) return false;
    drafts.push(importDraft(index));
    return true;
  });
  fileInput.value = "";
  renderImportRows(drafts);
}

function formatWeeks(weeks) {
  const ranges = [];
  let start;
  let end;
  for (const week of weeks) {
    if (start !== undefined && week !== end + 1) {
      ranges.push(start === end ? String(start) : `${start}–${end}`);
      start = undefined;
    }
    if (start === undefined) start = week;
    end = week;
  }
  if (start !== undefined) ranges.push(start === end ? String(start) : `${start}–${end}`);
  return ranges.join("、");
}

function renderPreview(response) {
  const list = byId("preview-list");
  const items = document.createDocumentFragment();
  validPreviewIndexes = [];
  for (const [index, file] of response.files.entries()) {
    const item = node("article", undefined, "preview-item");
    item.append(node("h3", file.file));
    const description = file.room ? `${file.room}${file.weeks.length ? ` · 替换第 ${formatWeeks(file.weeks)} 周` : ""}` : "未填写教室编号";
    item.append(node("p", description, "help"));
    if (file.errors.length) {
      const errors = node("ul", undefined, "preview-errors");
      for (const { line, message } of file.errors) {
        errors.append(node("li", line ? `第 ${line} 行 · ${message}` : message));
      }
      item.append(errors);
      if (byId("csv-edit-dialog")) {
        const edit = node("button", "在线修改 CSV", "button button-outline");
        edit.type = "button";
        edit.addEventListener("click", () => openCsvEditor(index, errors, edit));
        item.append(edit);
      }
    } else {
      validPreviewIndexes.push(index);
      item.append(node("p", `写入 ${file.count} 条单节排课，替换 ${file.old.length} 条旧记录。`));
      if (!file.count) {
        item.append(node("p", file.new_room ? "将登记教室，本次替换范围内没有排课。" : "将清空所选周次的排课。", "notice caution"));
      }
      if (file.old.length) {
        const details = node("details", undefined, "old-records");
        const records = node("ul");
        for (const { week, weekday, period, course } of file.old) {
          records.append(node("li", `第 ${week} 周 · ${weekdays[weekday - 1]} · 第 ${period} 节 · ${course}`));
        }
        details.append(node("summary", "查看将被替换的记录"), records);
        item.append(details);
      }
    }
    items.append(item);
  }
  list.replaceChildren(items);
  previewToken = response.token;
  const valid = validPreviewIndexes.length;
  importButton.textContent = valid < response.files.length && valid ? `导入 ${valid} 份有效文件` : "确认导入";
  byId("import-preview").hidden = false;
  notice("import-status", valid ? `${valid} 份文件可导入。` : "请修正预览中的问题后重新预览。", valid ? "" : "error");
}

async function openCsvEditor(index, errors, button) {
  const file = files[index];
  const dialog = byId("csv-edit-dialog");
  button.disabled = true;
  try {
    const bytes = await file.arrayBuffer();
    if (!button.isConnected || files[index] !== file || importBusy || dialog.open) return;
    let content;
    let encodingNote = "";
    try {
      content = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    } catch {
      content = new TextDecoder("gb18030").decode(bytes);
      encodingNote = "文件不是有效的 UTF-8，已尝试按 GB18030 读取。请检查文字，保存时会转为 UTF-8。";
    }
    csvEditIndex = index;
    byId("csv-edit-name").textContent = file.name;
    byId("csv-edit-issues").replaceChildren(...Array.from(errors.children, (issue) => issue.cloneNode(true)));
    byId("csv-edit-text").value = content;
    const encoding = byId("csv-edit-encoding");
    encoding.textContent = encodingNote;
    encoding.hidden = !encodingNote;
    dialog.showModal();
  } catch {
    notice("import-status", `无法读取「${file.name}」，请重新添加该文件。`, "error");
  } finally {
    button.disabled = false;
  }
}

function settingsValue() {
  return {
    start: byId("term-start").value,
    weeks: Number(byId("term-weeks").value),
    groups: byId("term-groups").value.split(/[,，]/).map((value) => value.trim()),
  };
}

window.addEventListener("hashchange", showPanel);
byId("csv-edit-cancel")?.addEventListener("click", () => byId("csv-edit-dialog").close());
byId("csv-edit-form")?.addEventListener("submit", (event) => {
  event.preventDefault();
  const original = files[csvEditIndex];
  const content = byId("csv-edit-text").value;
  files[csvEditIndex] = new File([content], original.name, { type: "text/csv;charset=utf-8" });
  byId("csv-edit-dialog").close();
  clearPreview();
  importForm.requestSubmit();
});
filters.addEventListener("submit", (event) => event.preventDefault());
startPeriodInput.addEventListener("change", () => fillEndPeriods(endPeriodInput.value));
filters.addEventListener("change", updateQuery);
scheduleForm.addEventListener("submit", (event) => event.preventDefault());
scheduleForm.addEventListener("change", updateRoomSchedule);

const renameDialog = byId("room-rename-dialog");
const renameForm = byId("room-rename-form");
let roomToRename;
byId("room-rename-open")?.addEventListener("click", () => {
  roomToRename = scheduleForm.elements.room.value;
  if (!roomToRename) return;
  byId("room-rename-value").value = roomToRename;
  notice("room-rename-error");
  renameDialog.showModal();
});
byId("room-rename-cancel")?.addEventListener("click", () => renameDialog.close());
renameDialog?.addEventListener("cancel", (event) => {
  if (byId("room-rename-cancel").disabled) event.preventDefault();
});
renameForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = byId("room-rename-value").value.trim();
  const controls = [...renameForm.elements];
  controls.forEach((control) => { control.disabled = true; });
  notice("room-rename-error");
  try {
    const next = await api("/api/room-name", jsonRequest("PUT", { room: roomToRename, name }));
    if (roomToRename !== next.renamed_room) {
      files.forEach((_file, index) => {
        const input = byId(`room-${index}`);
        if (input.value.trim() === roomToRename) input.value = next.renamed_room;
      });
      if (previewToken) notice("import-status", "教室名称已修改，请重新预览待导入文件。");
      clearPreview();
    }
    applyState(next, true, false, roomToRename);
    renameDialog.close();
  } catch (error) {
    notice("room-rename-error", error.message, "error");
  } finally {
    controls.forEach((control) => { control.disabled = false; });
  }
});

byId("schedule-refresh").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    applyState(await api("/api/state"), true);
  } catch (error) {
    scheduleContent.replaceChildren(node("p", error.message, "notice error"));
  } finally {
    button.disabled = false;
  }
});

byId("edit-cancel").addEventListener("click", () => editDialog.close());
editDialog.addEventListener("cancel", (event) => {
  if (byId("edit-cancel").disabled) event.preventDefault();
});
editForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const inputs = [...byId("edit-periods").querySelectorAll("input")];
  const payload = { ...editSelection, courses: inputs.map((input) => input.value.trim()) };
  const controls = [...editForm.elements];
  for (const control of controls) control.disabled = true;
  notice("edit-error");
  try {
    const next = await api("/api/room-schedule", jsonRequest("PUT", payload));
    editDialog.close();
    if (previewToken) notice("import-status", "课表已修改，请重新预览待导入文件。");
    clearPreview();
    applyState(next, true);
  } catch (error) {
    notice("edit-error", error.message, "error");
    updateRoomSchedule();
  } finally {
    for (const control of controls) control.disabled = false;
  }
});
byId("today-button").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    applyState(await api("/api/state"), true, true);
  } catch (error) {
    const message = node("p", error.message, "notice error");
    message.setAttribute("role", "alert");
    resultContent.replaceChildren(message);
  } finally {
    button.disabled = false;
  }
});

fileInput.addEventListener("change", () => {
  const selected = Array.from(fileInput.files);
  fileInput.value = "";
  if (!selected.length) return;
  const added = selected.filter((file) => /\.csv$/i.test(file.name));
  const rejected = selected.length - added.length;
  if (!added.length) {
    notice("import-status", "请选择扩展名为 .csv 的文件。", "error");
    return;
  }
  const drafts = files.map((_file, index) => importDraft(index));
  const hadPreview = Boolean(previewToken);
  files.push(...added);
  renderImportRows(drafts);
  clearPreview();
  const message = [`已追加 ${added.length} 份 CSV。`];
  if (rejected) message.push(`已跳过 ${rejected} 份非 CSV 文件。`);
  if (hadPreview) message.push("请重新预览待导入文件。");
  notice("import-status", message.join(""), rejected ? "caution" : "success");
});
byId("import-rows").addEventListener("input", () => {
  clearPreview();
  notice("import-status");
});
importForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!files.length || importBusy) return;
  clearPreview();
  const sequence = previewSequence;
  notice("import-status", "正在校验文件…");
  const body = new FormData();
  files.forEach((file, index) => {
    body.append("file", file);
    body.append("room", byId(`room-${index}`).value.trim());
    body.append("weeks", byId(`scope-${index}`).checked ? "all" : byId(`weeks-${index}`).value.trim());
  });
  setImportBusy(true);
  try {
    const response = await api("/api/preview", { method: "POST", body });
    if (sequence === previewSequence) renderPreview(response);
    else notice("import-status", "学期设置已变更，请重新预览。", "caution");
  } catch (error) {
    notice("import-status", error.message, "error");
  } finally {
    setImportBusy(false);
  }
});

importButton.addEventListener("click", async () => {
  if (!previewToken || importBusy) return;
  const submittedIndexes = validPreviewIndexes;
  setImportBusy(true);
  try {
    const response = await api("/api/import", jsonRequest("POST", { token: previewToken }));
    const importedIndexes = new Set(response.files.flatMap((file, index) => file.imported ? [submittedIndexes[index]] : []));
    const imported = importedIndexes.size;
    const errors = response.files.filter((file) => !file.imported).map((file) => `${file.file} · ${file.error}`);
    const remaining = files.length - imported;
    const status = remaining ? `已导入 ${imported} 份文件，${remaining} 份待重新预览。` : `已导入 ${imported} 份文件。`;
    const summary = [status, ...errors].join("\n");
    if (imported) {
      removeImportFiles(importedIndexes);
      try {
        applyState(await api("/api/state"), true);
      } catch (error) {
        notice("import-status", `${summary}\n刷新查询数据失败，重新加载页面即可查看已导入的课表。`, "caution");
        return;
      }
    }
    notice("import-status", summary, remaining ? "caution" : "success");
  } catch (error) {
    notice("import-status", error.message, "error");
  } finally {
    clearPreview();
    setImportBusy(false);
  }
});

settingsForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const value = settingsValue();
  const controls = [...settingsForm.elements, byId("reset-open")];
  for (const control of controls) control.disabled = true;
  try {
    applyState(await api("/api/settings", jsonRequest("PUT", value)), true);
    clearPreview();
    notice("settings-status", "学期设置已保存。", "success");
  } catch (error) {
    notice("settings-status", error.message, "error");
  } finally {
    for (const control of controls) control.disabled = false;
  }
});

byId("reset-open").addEventListener("click", () => {
  if (!settingsForm.reportValidity()) return;
  notice("reset-error");
  const { start, weeks } = settingsValue();
  byId("reset-description").textContent = `将清空现有教室和课表，并开始 ${start} 起的 ${weeks} 周新学期。`;
  resetDialog.showModal();
});

resetDialog.addEventListener("cancel", (event) => {
  if (byId("reset-confirm").disabled) event.preventDefault();
});

byId("reset-confirm").addEventListener("click", async () => {
  const confirm = byId("reset-confirm");
  const cancel = byId("reset-cancel");
  confirm.disabled = true;
  cancel.disabled = true;
  notice("reset-error");
  try {
    applyState(await api("/api/reset", jsonRequest("POST", settingsValue())));
    clearPreview();
    resetDialog.close();
    notice("settings-status", "已更换学期，现有课表已清空。", "success");
  } catch (error) {
    notice("reset-error", error.message, "error");
  } finally {
    confirm.disabled = false;
    cancel.disabled = false;
  }
});

showPanel();
api("/api/state").then(applyState).catch((error) => {
  notice("startup-error", error.message, "error");
  resultContent.replaceChildren();
});
