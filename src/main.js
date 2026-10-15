import Handlebars from "handlebars";
import masterSource from "../templates/tradesheet.html.hbs?raw";
import page1Source from "../templates/page1.html.hbs?raw";
import page2Source from "../templates/page2.html.hbs?raw";
import reportData from "@report-data";
import configData from "@chapter-config";

// Register custom Handlebars helpers matching Python tradesheet.py
Handlebars.registerHelper("format_number", function (val) {
  if (val == null || val === "") return "0";
  const num = Number(val);
  return isNaN(num) ? String(val) : num.toLocaleString();
});

Handlebars.registerHelper("format_currency", function (val) {
  if (val == null || val === "") return "$0.00";
  const num = Number(val);
  return isNaN(num)
    ? String(val)
    : "$" + num.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
});

Handlebars.registerHelper("format_currency_short", function (val) {
  if (val == null || val === "") return "$0.0M";
  const num = Number(val);
  if (isNaN(num)) return String(val);
  if (Math.abs(num) >= 1_000_000) {
    return "$" + (num / 1_000_000).toFixed(1) + "M";
  } else if (Math.abs(num) >= 1_000) {
    return "$" + (num / 1_000).toFixed(1) + "K";
  }
  return "$" + num.toLocaleString();
});

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
Handlebars.registerHelper("format_roster_date", function (val) {
  if (!val) return "";
  try {
    const d = new Date(val);
    if (isNaN(d.getTime())) return String(val);
    const day = String(d.getDate()).padStart(2, "0");
    const mon = MONTHS[d.getMonth()];
    const yr = String(d.getFullYear()).slice(-2);
    return `${day}-${mon}-${yr}`;
  } catch {
    return String(val);
  }
});

Handlebars.registerHelper("break_lines", function (val) {
  if (!val) return "";
  const escaped = Handlebars.escapeExpression(String(val));
  return new Handlebars.SafeString(escaped.replace(/\n/g, "<br>"));
});

Handlebars.registerHelper("eq", function (left, right) {
  return left === right;
});

Handlebars.registerHelper("member_rows_by_last_name", function (roster) {
  if (!Array.isArray(roster)) return [];

  const sortKey = (member) => {
    const name = typeof member?.name === "string" ? member.name.trim() : "";
    const parts = name.split(/\s+/).filter(Boolean);
    const surname = parts.length ? parts.at(-1).toLocaleLowerCase("en-US") : "";
    return [name ? 0 : 1, surname, name.toLocaleLowerCase("en-US")];
  };

  const compareMembers = (left, right) => {
    const leftKey = sortKey(left);
    const rightKey = sortKey(right);
    return leftKey[0] - rightKey[0]
      || leftKey[1].localeCompare(rightKey[1])
      || leftKey[2].localeCompare(rightKey[2]);
  };

  const members = roster.filter((member) => member && typeof member === "object").sort(compareMembers);
  const midpoint = Math.ceil(members.length / 2);
  const leftColumn = members.slice(0, midpoint);
  const rightColumn = members.slice(midpoint);

  return Array.from({ length: Math.max(leftColumn.length, rightColumn.length) }, (_, index) => ({
    left: leftColumn[index] || null,
    right: rightColumn[index] || null,
  }));
});

Handlebars.registerHelper("leadership_rows", function (leaders) {
  if (!Array.isArray(leaders)) return [];

  const normalize = (value) => String(value || "").trim().replace(/\s+/g, " ").toLocaleLowerCase("en-US");
  const coreRoles = [
    ["president", "President"],
    ["vice president", "Vice President"],
    ["secretary/treasurer", "Secretary/Treasurer"],
    ["slideshow master", "Slideshow Master"],
    ["mentor coordinator", "Mentor Coordinator"],
    ["go4green coordinator", "Go4Green Coordinator"],
  ];
  const coreAliases = new Map([
    ...coreRoles,
    ["go for green co-ordinator", "Go4Green Coordinator"],
    ["go for green coordinator", "Go4Green Coordinator"],
  ]);
  const teamRoles = [
    ["growth_and_events", "Growth and Events"],
    ["membership_committee", "Membership Committee"],
    ["visitor_hosts", "Visitor Hosts"],
  ];
  const namesByRole = new Map([...coreRoles, ...teamRoles].map(([, label]) => [label, []]));
  const seenNamesByRole = new Map([...coreRoles, ...teamRoles].map(([, label]) => [label, new Set()]));
  const individualRoles = new Map();

  const appendName = (label, name) => {
    const nameKey = normalize(name);
    if (!nameKey || seenNamesByRole.get(label).has(nameKey)) return;
    namesByRole.get(label).push(String(name).trim());
    seenNamesByRole.get(label).add(nameKey);
  };

  for (const leader of leaders) {
    if (!leader || typeof leader !== "object") continue;
    const role = normalize(leader.role);
    const name = typeof leader.name === "string" ? leader.name : "";
    if (!role || !name.trim()) continue;

    let label = coreAliases.get(role);
    if (!label) {
      if (role.startsWith("growth and events")) {
        label = "Growth and Events";
      } else if (role.startsWith("membership committee")) {
        label = "Membership Committee";
      } else if (role.startsWith("visitor host")) {
        label = "Visitor Hosts";
      } else {
        label = individualRoles.get(role);
        if (!label) {
          label = String(leader.role).trim().replace(/\s+/g, " ");
          individualRoles.set(role, label);
          namesByRole.set(label, []);
          seenNamesByRole.set(label, new Set());
        }
      }
    }

    appendName(label, name);
  }

  const displayOrder = [
    ...coreRoles.map(([, label]) => label),
    ...individualRoles.values(),
    ...teamRoles.map(([, label]) => label),
  ];
  return displayOrder
    .filter((label) => namesByRole.get(label).length)
    .map((label) => ({ role: label, names: namesByRole.get(label).join(", ") }));
});

// Register template partials
Handlebars.registerPartial("page1", page1Source);
Handlebars.registerPartial("page2", page2Source);

// Assemble the same raw-document context as tradesheet.py.
function assembleContext(report, cfg) {
  const context = { tradesheet: cfg, bni_report: report };

  const today = new Date();
  const d = String(today.getDate()).padStart(2, "0");
  const m = String(today.getMonth() + 1).padStart(2, "0");
  const y = today.getFullYear();
  context.meeting_date = `${d}/${m}/${y}`;
  context.meeting_date_iso = `${y}-${m}-${d}`;
  context.meeting_day_name = today.toLocaleDateString("en-US", { weekday: "long" });

  return context;
}

// Compile and render
function render() {
  try {
    const context = assembleContext(reportData, configData);
    const template = Handlebars.compile(masterSource);
    const rendered = template(context);

    const container = document.getElementById("rendered-output");
    if (container) {
      container.innerHTML = rendered;
    }
  } catch (err) {
    const container = document.getElementById("rendered-output");
    if (container) {
      container.innerHTML = `<div style="color:red; font-family:monospace; padding:20px;">Render Error: ${err.message}<pre>${err.stack}</pre></div>`;
    }
    console.error(err);
  }
}

render();
