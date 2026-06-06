// pages/dashboard.js — dashboard shell entry (P3-1). Imports the widget modules
// (each self-registers on import) and boots the single shared scheduler.
//
// Migration note: this runs ALONGSIDE the legacy refresh.js/perf-chart.js while
// widgets are ported one at a time (phase-3 migration order). A widget lives here
// only once its partial has a stable root id and its legacy updateXxxDOM is gone.
import "../widgets/today-events.js";
import "../widgets/event-trigger.js";
import { start } from "../core/scheduler.js";

start();
