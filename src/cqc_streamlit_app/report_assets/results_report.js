(function () {
  "use strict";
  const DATA = JSON.parse(document.getElementById("run-data").textContent);
  const $ = (sel) => document.querySelector(sel);
  function el(tag, attrs, ...kids) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "text") node.textContent = v;
      else if (k === "cls") node.className = v;
      else node.setAttribute(k, v);
    }
    for (const kid of kids) if (kid != null) node.append(kid);
    return node;
  }
  function pre(text, cls) {
    return el("pre", { cls: cls || "wrap", text: text === "" ? "(nothing)" : text });
  }
  function codeBlock(text) {
    const node = el("pre", { cls: "code" });
    for (const line of text.replace(/\n$/, "").split("\n"))
      node.append(el("span", { cls: "l", text: line || " " }));
    return node;
  }
  function diffBlock(text) {
    const node = el("pre", { cls: "diff" });
    for (const line of text.split("\n")) {
      const cls =
        line.startsWith("+") && !line.startsWith("+++")
          ? "a"
          : line.startsWith("-") && !line.startsWith("---")
            ? "d"
            : "";
      node.append(el("span", { cls: cls, text: line + "\n" }));
    }
    return node;
  }
  // The app's side-by-side view (diff_view.py): one row per aligned line on each side, blank hatched
  // filler rows keep the panes level, and the two panes scroll together, vertically and sideways.
  function sideBySide(side) {
    const wrap = el("div");
    wrap.append(el("p", { cls: "sbs-sum", role: "status", text: side.summary }));
    const key = el("details", { cls: "sbs-key" }, el("summary", { text: "How to read this" }));
    const ul = el("ul");
    for (const t of [
      "- is in the expected output, + is in the student output. A marker on both sides means the two lines " +
        "were matched as the same line of output; the tag says how they differ.",
      "Tags: spacing, case, punctuation, number format, decimal places, value, wording, moved line, blank " +
        "line. Differing words inside a matched line are underlined; spacing-only rows are grey.",
      "Hatched rows are blank here because the other side has an extra line. Both panes scroll together.",
    ])
      ul.append(el("li", { text: t }));
    key.append(ul);
    wrap.append(key);
    const grid = el("div", { cls: "sbs" });
    const panes = [];
    for (const [title, idx, nIdx, cls, mark, spoken] of [
      ["Expected output", 2, 1, "rm", "-", 6],
      ["Student output", 4, 3, "add", "+", 7],
    ]) {
      const rows = el("div", { cls: "rows" });
      const pane = el("div", { cls: "pane", role: "region", tabindex: "0", "aria-label": title }, rows);
      const frag = document.createDocumentFragment();
      for (const r of side.rows) {
        const v = r[idx],
          k = r[0],
          q = r[5];
        const row = el("div", { cls: "r" }),
          g = el("span", { cls: "g" }),
          n = el("span", { cls: "n" }),
          m = el("span", { cls: "m", "aria-hidden": "true" }),
          c = el("span", { cls: "c", "aria-hidden": "true" }),
          t = el("span", { cls: "t" });
        if (v === null) {
          row.className += " fl";
          row.setAttribute("aria-hidden", "true");
        } else {
          n.append(el("span", { cls: "sr", text: "Line " }), String(r[nIdx]));
          if (k !== "eq") {
            row.className += " " + (k === "chg" && q === "spacing" ? "mu" : cls);
            m.textContent = mark;
            t.append(el("span", { cls: "sr", text: r[spoken] }));
            if (q) c.textContent = DATA.diff_tags[q] || q;
          }
          if (typeof v === "string") t.append(v);
          else for (const [piece, hi] of v) t.append(el("span", { cls: hi ? "w" : "", text: piece }));
        }
        g.append(n, m, c);
        row.append(g, t);
        frag.append(row);
      }
      rows.append(frag);
      panes.push(pane);
      grid.append(el("div", {}, el("h4", { text: title }), pane));
    }
    // Only the pane the user is driving pushes its position, so the two never fight over a scroll.
    let driver = null;
    for (const ev of ["wheel", "pointerdown", "keydown", "focus", "touchstart"])
      for (const p of panes) p.addEventListener(ev, () => (driver = p), { passive: true });
    const link = (src, dst) =>
      src.addEventListener(
        "scroll",
        () => {
          if (driver && driver !== src) return;
          if (dst.scrollTop !== src.scrollTop) dst.scrollTop = src.scrollTop;
          if (dst.scrollLeft !== src.scrollLeft) dst.scrollLeft = src.scrollLeft;
        },
        { passive: true },
      );
    link(panes[0], panes[1]);
    link(panes[1], panes[0]);
    wrap.append(grid);
    return wrap;
  }
  const LONG_COLUMNS = ["Details", "Detail", "Feedback", "Reason", "Evidence", "Requirement"];
  function colClass(header) {
    return header === "Code" ? "ident" : LONG_COLUMNS.includes(header) ? "long" : "";
  }
  function table(headers, rows) {
    const t = el("table");
    t.append(el("thead", {}, el("tr", {}, ...headers.map((h) => el("th", { text: h })))));
    const body = el("tbody");
    for (const row of rows)
      body.append(
        el("tr", {}, ...row.map((c, i) => el("td", { cls: colClass(headers[i]), text: String(c) }))),
      );
    t.append(body);
    return el("div", { cls: "tablewrap" }, t);
  }
  function fmt(n) {
    return Number.isInteger(n) ? String(n) : String(Math.round(n * 100) / 100);
  }

  // ---- header ----
  $("#title").textContent = DATA.title;
  document.title = DATA.title;
  const metaParts = ["Built " + DATA.generated];
  for (const [k, v] of Object.entries(DATA.meta)) metaParts.push(k + ": " + v);
  $("#meta").textContent = metaParts.join(" · ");
  const counted = DATA.students.filter((s) => s.status === "Graded");
  const avg = counted.length ? counted.reduce((a, s) => a + s.earned, 0) / counted.length : 0;
  const possible = DATA.students.length ? DATA.students[0].possible : 0;
  const tiles = [
    ["Graded", counted.length],
    ["Needs review", DATA.students.length - counted.length],
    ["Failed", DATA.failed.length],
    ["Average", fmt(avg) + "/" + possible],
  ];
  for (const [label, value] of tiles)
    $("#tiles").append(el("div", { cls: "tile" }, el("b", { text: String(value) }), label));
  if (DATA.failed.length) {
    $("#failed").className = "alert";
    $("#failed").textContent = "Grading failed for: " + DATA.failed.join(", ");
  }

  // ---- student list ----
  let sortKey = "id",
    sortDir = 1,
    selected = null;
  function drawList() {
    const q = $("#filter").value.trim().toLowerCase();
    const rows = DATA.students.filter(
      (s) => !q || s.id.toLowerCase().includes(q) || s.status.toLowerCase().includes(q),
    );
    rows.sort((a, b) => (a[sortKey] > b[sortKey] ? 1 : a[sortKey] < b[sortKey] ? -1 : 0) * sortDir);
    const body = $("#students tbody");
    body.replaceChildren();
    for (const s of rows) {
      const tr = el(
        "tr",
        { tabindex: "0" },
        el("td", {}, s.id, s.status === "Graded" ? null : el("span", { cls: "badge", text: s.status })),
        el(
          "td",
          { cls: "num" },
          fmt(s.earned) + "/" + s.possible,
          el("div", { cls: "muted", text: s.pct + "%" }),
        ),
        el("td", { cls: "num", text: s.major + " / " + s.minor }),
        el("td", { text: s.output_label }),
      );
      if (selected === s) tr.className = "sel";
      const open = () => {
        selected = s;
        drawList();
        drawStudent(s);
      };
      tr.addEventListener("click", open);
      tr.addEventListener("keydown", (e) => {
        if (e.key === "Enter") open();
      });
      body.append(tr);
    }
  }
  for (const th of document.querySelectorAll("th[data-sort]")) {
    th.addEventListener("click", () => {
      const key = th.getAttribute("data-sort");
      sortDir = key === sortKey ? -sortDir : 1;
      sortKey = key;
      drawList();
    });
  }
  $("#filter").addEventListener("input", drawList);

  // ---- one student ----
  function tabs(container, panels) {
    const bar = el("div", { cls: "tabs", role: "tablist" });
    const area = el("div");
    const buttons = panels.map(([name, draw], i) => {
      const b = el("button", { role: "tab", type: "button", "aria-selected": "false", text: name });
      b.addEventListener("click", () => {
        buttons.forEach((x) => x.setAttribute("aria-selected", "false"));
        b.setAttribute("aria-selected", "true");
        area.replaceChildren();
        draw(area);
      });
      return b;
    });
    bar.append(...buttons);
    container.append(bar, area);
    if (buttons.length) buttons[0].click();
  }
  function drawRunner(container, code, notice) {
    if (!code.runner) {
      if (code.files.length)
        container.append(el("p", { cls: "muted", text: "These files cannot be run in OneCompiler." }));
      return;
    }
    const box = el("div");
    const run = el("button", { cls: "btn", type: "button", text: "Run in OneCompiler" });
    const actions = el("p", {}, run);
    if (code.gdb) {
      actions.append(" ");
      actions.append(
        el("a", {
          href: code.gdb,
          target: "_blank",
          rel: "noopener noreferrer",
          text: "Open OnlineGDB (paste the code there)",
        }),
      );
    }
    container.append(actions, el("p", { cls: "muted small", text: notice }), box);
    run.addEventListener("click", () => {
      if (box.firstChild) {
        box.replaceChildren();
        run.textContent = "Run in OneCompiler";
        return;
      }
      run.textContent = "Close runner";
      const frame = el("iframe", {
        cls: "runner",
        src: code.runner.src,
        title: "OneCompiler code runner",
        allow: "clipboard-write",
      });
      frame.addEventListener("load", () => {
        let sent = 0;
        const send = () => {
          frame.contentWindow.postMessage(code.runner.payload, DATA.runner_origin);
          if (++sent < 3) setTimeout(send, 500);
        };
        send();
      });
      box.append(frame);
    });
  }
  function drawCode(container, code) {
    if (!code.available) {
      container.append(el("p", { cls: "muted", text: DATA.no_code_text }));
      return;
    }
    if (!code.files.length) {
      container.append(el("p", { cls: "muted", text: "No previewable source files." }));
    }
    if (code.skipped.length)
      container.append(el("p", { cls: "muted small", text: "Not shown: " + code.skipped.join(", ") }));
    const picker = el("div", { cls: "files" });
    const view = el("div");
    const buttons = code.files.map((f) => {
      const b = el("button", { type: "button", "aria-pressed": "false", text: f.name });
      b.addEventListener("click", () => {
        buttons.forEach((x) => x.setAttribute("aria-pressed", "false"));
        b.setAttribute("aria-pressed", "true");
        view.replaceChildren();
        if (f.truncated)
          view.append(
            el("p", {
              cls: "muted small",
              text: "Showing the first part of a long file; it is left out of a run.",
            }),
          );
        view.append(codeBlock(f.content));
      });
      return b;
    });
    if (buttons.length > 1) container.append(picker);
    picker.append(...buttons);
    container.append(view);
    if (buttons.length) buttons[0].click();
    drawRunner(container, code, DATA.runner_notice);
  }
  // The diff view choice is shared by every case, like the app's Side by side | Unified radio.
  let diffMode = "side";
  let diffViews = [];
  function drawDiff(c) {
    const box = el("div");
    const draw = () => {
      box.replaceChildren();
      if (c.side && c.side.skipped)
        box.append(
          el("p", {
            cls: "muted",
            text: "Side by side left out: this report reached its size limit. The unified diff is shown.",
          }),
        );
      if (c.side && c.side.rows && diffMode === "side") {
        box.append(sideBySide(c.side));
      } else if (c.diff.kind === "full") {
        box.append(
          el("div", { cls: "label", text: "Differences (- expected, + student)" }),
          diffBlock(c.diff.text),
        );
      } else if (c.diff.kind === "excerpt") {
        box.append(
          el("div", { cls: "label", text: "Differences, short excerpt (the full outputs were not kept)" }),
          diffBlock(c.diff.text),
        );
      }
    };
    const wrap = el("div");
    if (c.side && c.side.rows) {
      const seg = el("div", { cls: "seg", role: "radiogroup", "aria-label": "Diff view for case " + c.id });
      const name = "dv-" + c.id + "-" + Math.random().toString(36).slice(2);
      const inputs = [];
      for (const [value, label] of [
        ["side", "Side by side"],
        ["unified", "Unified"],
      ]) {
        const input = el("input", { type: "radio", name: name, value: value });
        input.checked = diffMode === value;
        input.addEventListener("change", () => {
          diffMode = value;
          for (const v of diffViews) v();
        });
        inputs.push(input);
        seg.append(el("label", {}, input, label));
      }
      diffViews.push(() => {
        for (const i of inputs) i.checked = i.value === diffMode;
        draw();
      });
      wrap.append(seg);
    }
    draw();
    wrap.append(box);
    return wrap;
  }
  function drawOutput(container, out) {
    diffViews = [];
    container.append(el("p", { cls: "muted", text: out.status }));
    container.append(el("p", {}, el("b", { text: "Result: " }), out.label));
    if (out.cannot_verify)
      container.append(
        el("p", { cls: "muted", text: "The check could not change the grade: " + out.cannot_verify }),
      );
    if (!out.cases.length) return;
    container.append(
      table(
        ["Case", "Label", "Verdict", "Detail"],
        out.cases.map((c) => [c.id, c.label, c.verdict, c.detail]),
      ),
    );
    let first = true;
    for (const c of out.cases) {
      if (c.match) continue;
      const body = el("div", { cls: "body" });
      const card = el(
        "details",
        { cls: "case" },
        el("summary", { text: "Case " + c.id + (c.label ? ": " + c.label : "") + " - " + c.verdict }),
        body,
      );
      if (first) card.open = true;
      first = false;
      if (c.cut)
        body.append(
          el("p", {
            cls: "alert",
            text: "This program printed more than 64 KB; only the first 64 KB was kept.",
          }),
        );
      if (c.has_input) body.append(el("div", { cls: "label", text: "Input" }), pre(c.input));
      if (!(c.side && c.side.rows)) {
        body.append(el("div", { cls: "label", text: "Expected output (your solution)" }), pre(c.expected));
        body.append(el("div", { cls: "label", text: "Student output" }), pre(c.actual));
      }
      if (c.diff.kind === "same")
        body.append(
          el("p", { cls: "muted", text: "No differences in the stored text after normalization." }),
        );
      else body.append(drawDiff(c));
      container.append(card);
    }
  }
  function drawStudent(s) {
    const d = $("#detail");
    d.replaceChildren(el("h2", { text: s.id }));
    d.append(
      el(
        "div",
        { cls: "tiles" },
        el("div", { cls: "tile" }, el("b", { text: fmt(s.earned) + "/" + s.possible }), "Score"),
        el("div", { cls: "tile" }, el("b", { text: s.pct + "%" }), "Percent"),
        el("div", { cls: "tile" }, el("b", { cls: "txt", text: s.level || "—" }), "Level"),
        el("div", { cls: "tile" }, el("b", { text: s.major + " / " + s.minor }), "Errors (major / minor)"),
      ),
    );
    if (s.status !== "Graded")
      d.append(el("p", { cls: "alert", text: s.status + (s.review_reason ? ": " + s.review_reason : "") }));
    if (s.model) d.append(el("p", { cls: "muted small", text: "Graded with " + s.model }));
    const panels = [
      [
        "Feedback",
        (c) => {
          const fb = el("div", { cls: "card feedback" });
          fb.innerHTML = s.feedback_html; // escaped by _markdown_to_html
          c.append(
            el("p", { cls: "muted small", text: "What the student sees (no scores)." }),
            fb,
            el("h3", { text: "Overall summary" }),
            pre(s.overall),
          );
        },
      ],
      [
        "Criteria",
        (c) =>
          c.append(
            table(
              ["Criterion", "Points", "Level", "Feedback"],
              s.criteria.map((x) => [x.name, x.points, x.level, x.feedback]),
            ),
          ),
      ],
      [
        "Errors",
        (c) => {
          if (!s.errors.length) c.append(el("p", { cls: "muted", text: "No errors detected." }));
          else
            c.append(
              table(
                ["Severity", "Error", "Code", "Times", "Details", "Found by"],
                s.errors.map((e) => [e.severity, e.name, e.code, e.times, e.details, e.source]),
              ),
            );
          for (const e of s.errors)
            if (e.diagnostics)
              c.append(el("h3", { text: "Compiler output for " + e.code }), pre(e.diagnostics));
          if (s.notes.length) {
            c.append(el("h3", { text: "Scoring notes" }));
            c.append(
              table(
                ["Stage", "Action", "Error", "Reason", "State"],
                s.notes.map((n) => [n.stage, n.action, n.code, n.reason, n.state]),
              ),
            );
          }
        },
      ],
      [
        "Requirements",
        (c) => {
          if (!s.requirements.length)
            c.append(el("p", { cls: "muted", text: "No requirement checklist was used for this result." }));
          else
            c.append(
              table(
                ["Id", "Requirement", "Status", "Evidence"],
                s.requirements.map((r) => [r.id, r.text, r.status, r.evidence]),
              ),
            );
        },
      ],
    ];
    if (s.output) panels.push(["Output", (c) => drawOutput(c, s.output)]);
    panels.push(["Code", (c) => drawCode(c, s.code)]);
    tabs(d, panels);
  }

  // ---- solution ----
  if (DATA.solution) {
    const sec = el("section", { cls: "card", id: "solution" }, el("h2", { text: "Solution and test cases" }));
    const body = el("div");
    const toggle = el("button", { cls: "btn secondary", type: "button", text: "Show solution" });
    toggle.addEventListener("click", () => {
      if (body.firstChild) {
        body.replaceChildren();
        toggle.textContent = "Show solution";
        return;
      }
      toggle.textContent = "Hide solution";
      drawCode(body, DATA.solution);
      if (DATA.solution.cases.length) {
        body.append(el("h3", { text: "Test cases" }));
        for (const c of DATA.solution.cases)
          body.append(
            el(
              "div",
              { cls: "card" },
              el("b", { text: c.id + (c.label ? ": " + c.label : "") }),
              el("div", { cls: "label", text: "Input" }),
              pre(c.input),
              el("div", { cls: "label", text: "Expected output" }),
              pre(c.expected),
            ),
          );
      }
    });
    sec.append(toggle, body);
    $("#list").append(sec);
  }

  drawList();
  if (DATA.students.length) {
    selected = DATA.students[0];
    drawList();
    drawStudent(selected);
  }
})();
