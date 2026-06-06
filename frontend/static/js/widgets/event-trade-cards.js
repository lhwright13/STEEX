// widgets/event-trade-cards.js — P3-6. Renders the event-trade cards into any
// container that opts in. Registered for two mount points (Today's Events and
// the event panel) so the same one-source cards appear in both, per P3-6.
import { register } from "../core/scheduler.js";
import { renderCards } from "../components/event-trade-card.js";

const ENDPOINT = "/api/v1/events/trade-cards?limit=20";

function update(data, root) {
  renderCards(data, root);
}

// Same data, two homes. Tiny payload; a second 15s poll is negligible.
register({ id: "event-trade-cards-today", endpoint: ENDPOINT, update, cadence: 15000 });
register({ id: "event-trade-cards-panel", endpoint: ENDPOINT, update, cadence: 15000 });
