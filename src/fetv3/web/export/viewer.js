(function () {
  "use strict";

  var HOURS = [
    "08:20–09:50",
    "10:00–11:30",
    "11:40–13:10",
    "13:30–15:00",
    "15:10–16:40",
    "17:00–18:30",
    "18:40–20:10",
    "20:20–21:50"
  ];
  var WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

  var state = { data: null, faculty: "", group: "", week: "" };
  var $ = function (id) { return document.getElementById(id); };

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function parseRange(text) {
    var match = /(\d{2})\.(\d{2})\.(\d{4})\s*-\s*(\d{2})\.(\d{2})\.(\d{4})/.exec(text || "");
    if (!match) { return null; }
    return {
      start: new Date(+match[3], +match[2] - 1, +match[1]),
      end: new Date(+match[6], +match[5] - 1, +match[4])
    };
  }

  function shortDate(date) {
    var d = String(date.getDate()).padStart(2, "0");
    var m = String(date.getMonth() + 1).padStart(2, "0");
    return d + "." + m;
  }

  function fill(select, values, selected) {
    select.innerHTML = "";
    values.forEach(function (value) {
      var option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      if (value === selected) { option.selected = true; }
      select.appendChild(option);
    });
  }

  function readHash() {
    var params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    return {
      faculty: params.get("faculty") || "",
      group: params.get("group") || "",
      week: params.get("week") || ""
    };
  }

  function writeHash() {
    var params = new URLSearchParams();
    if (state.faculty) { params.set("faculty", state.faculty); }
    if (state.group) { params.set("group", state.group); }
    if (state.week) { params.set("week", state.week); }
    history.replaceState(null, "", "#" + params.toString());
    $("link").href = window.location.pathname + "#" + params.toString();
  }

  function weeksOf(group) {
    var schedule = state.data.schedules[group];
    if (!schedule) { return []; }
    return Object.keys(schedule[1]).sort(function (a, b) { return Number(a) - Number(b); });
  }

  function render() {
    var table = $("grid");
    var empty = $("empty");
    table.innerHTML = "";
    if (!state.group || !state.week) {
      empty.classList.remove("hidden");
      table.classList.add("hidden");
      return;
    }
    empty.classList.add("hidden");
    table.classList.remove("hidden");

    var schedule = state.data.schedules[state.group];
    var weekData = schedule[1][state.week];
    if (!weekData) { return; }
    var meta = weekData[0];
    var grid = weekData[1];

    var range = parseRange(meta.per);
    var days = [];
    for (var day = 1; day <= 7; day += 1) {
      var has = grid[String(day)] && Object.keys(grid[String(day)]).length > 0;
      if (day === 7 && !has) { continue; }
      days.push(day);
    }

    var thead = document.createElement("thead");
    var headRow = document.createElement("tr");
    var corner = document.createElement("th");
    corner.textContent = "Время";
    headRow.appendChild(corner);
    days.forEach(function (day) {
      var th = document.createElement("th");
      var label = WEEKDAYS[day - 1];
      if (range) {
        var date = new Date(range.start.getTime());
        date.setDate(date.getDate() + day - 1);
        label += " · " + shortDate(date);
      }
      th.textContent = label;
      headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    table.appendChild(thead);

    var tbody = document.createElement("tbody");
    for (var pair = 1; pair <= HOURS.length; pair += 1) {
      var tr = document.createElement("tr");
      var time = document.createElement("td");
      time.textContent = HOURS[pair - 1];
      tr.appendChild(time);
      days.forEach(function (day) {
        var td = document.createElement("td");
        var items = (grid[String(day)] || {})[String(pair)] || [];
        if (items.length) {
          td.className = "has-lesson";
          items.forEach(function (lesson) {
            var block = document.createElement("div");
            block.className = "lesson";
            var subgroup = lesson.SUBG && lesson.SUBG !== "0"
              ? '<span class="badge">п' + escapeHtml(lesson.SUBG) + "</span>"
              : "";
            block.innerHTML =
              '<div class="lesson__subject">' + escapeHtml(lesson.SUBJECT) + subgroup + "</div>" +
              '<div class="lesson__meta">' + escapeHtml(lesson.SUBJ_TYPE) + " · " +
              escapeHtml(lesson.NAME) + "</div>" +
              (lesson.AUD ? '<div class="lesson__room">ауд. ' + escapeHtml(lesson.AUD) + "</div>" : "");
            td.appendChild(block);
          });
        }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
  }

  function syncSelectors() {
    var faculties = Object.keys(state.data.groups);
    if (!faculties.includes(state.faculty)) { state.faculty = faculties[0] || ""; }
    fill($("faculty"), faculties, state.faculty);

    var groups = Object.keys(state.data.groups[state.faculty] || {});
    if (!groups.includes(state.group)) { state.group = groups[0] || ""; }
    fill($("group"), groups, state.group);

    var weeks = weeksOf(state.group);
    if (!weeks.includes(state.week)) { state.week = weeks[weeks.length - 1] || ""; }
    var options = weeks.map(function (week) {
      var meta = state.data.schedules[state.group][1][week][0];
      return { value: week, label: "Неделя " + week + " · " + meta.per };
    });
    $("week").innerHTML = "";
    options.forEach(function (item) {
      var option = document.createElement("option");
      option.value = item.value;
      option.textContent = item.label;
      if (item.value === state.week) { option.selected = true; }
      $("week").appendChild(option);
    });
    writeHash();
    render();
  }

  function start() {
    fetch("data.json", { cache: "no-store" })
      .then(function (response) {
        if (!response.ok) { throw new Error("data.json: " + response.status); }
        return response.json();
      })
      .then(function (data) {
        state.data = data;
        var hash = readHash();
        state.faculty = hash.faculty;
        state.group = hash.group;
        state.week = hash.week;

        var stats = data.stats || {};
        $("scope").textContent =
          (data.school || "") + " · " + (data.scope || "") +
          " · групп: " + (stats.groups || 0) + ", занятий: " + (stats.lessons || 0);
        $("generated").textContent = (data.generated || "").replace("T", " ");
        $("school").textContent = data.school || "Южный университет (ИУБиП)";
        document.title = "Расписание — " + (data.school || "ИУБиП");
        syncSelectors();
      })
      .catch(function (error) {
        $("scope").textContent = "Не удалось загрузить data.json: " + error.message;
      });
  }

  $("faculty").addEventListener("change", function (event) {
    state.faculty = event.target.value;
    syncSelectors();
  });
  $("group").addEventListener("change", function (event) {
    state.group = event.target.value;
    syncSelectors();
  });
  $("week").addEventListener("change", function (event) {
    state.week = event.target.value;
    writeHash();
    render();
  });
  $("print").addEventListener("click", function () { window.print(); });
  window.addEventListener("hashchange", function () {
    var hash = readHash();
    state.faculty = hash.faculty;
    state.group = hash.group;
    state.week = hash.week;
    syncSelectors();
  });

  start();
})();
