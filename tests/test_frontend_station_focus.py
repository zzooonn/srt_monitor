"""Exercise focus transitions that can occur before native focus settles."""
import base64
from pathlib import Path
import shutil
import subprocess

import pytest


NODE = shutil.which("node")
FRONTEND = Path(__file__).resolve().parents[1] / "frontend"


@pytest.mark.skipif(NODE is None, reason="Node.js is needed for frontend focus checks")
def test_picker_keeps_internal_focus_and_delays_unknown_focus_destination():
    model_url = "data:text/javascript;base64," + base64.b64encode((FRONTEND / "station-model.js").read_bytes()).decode("ascii")
    source = (FRONTEND / "station-picker.js").read_text(encoding="utf-8").replace("'./station-model.js'", repr(model_url))
    picker_url = "data:text/javascript;base64," + base64.b64encode(source.encode("utf-8")).decode("ascii")
    script = """
import assert from 'node:assert/strict';
const { StationPicker } = await import(PICKER_URL);
const element = () => ({
  value: '', hidden: true, listeners: {},
  classList: {add() {}, remove() {}},
  addEventListener(name, listener) { this.listeners[name] = listener; },
  setAttribute() {}, removeAttribute() {}, querySelectorAll() {return [];},
});
const root = element(), input = element(), star = element(), toggle = element(), outside = element();
const popup = element(), list = element(), hidden = element(), empty = element();
const members = new Set([input, star, toggle, popup, list, hidden, empty]);
root.contains = target => members.has(target);
root.querySelector = () => toggle;
input.closest = () => root;
const fields = {'arrival_station': hidden, 'arrival_station-search': input,
  'arrival_station-popup': popup, 'arrival_station-list': list, 'arrival_station-empty': empty};
globalThis.document = {activeElement: input, getElementById: name => fields[name], addEventListener() {}};
const picker = new StationPicker('arrival_station', {favorites: () => new Set(), onSelect() {}, onFavorite() {}, onOpen() {}});
picker.render = () => {};
picker.setDisabled(false);
const tick = () => new Promise(resolve => setTimeout(resolve, 5));

picker.open();
document.activeElement = outside; // Native Tab may briefly expose body.
root.listeners.focusout({relatedTarget: star});
await tick();
assert.equal(popup.hidden, false, 'Known internal Tab destination must retain the popup');

root.listeners.focusout({relatedTarget: outside});
assert.equal(popup.hidden, true, 'Known external destination must close the popup');

picker.open();
root.listeners.focusout({relatedTarget: null});
document.activeElement = star;
await tick();
assert.equal(popup.hidden, false, 'Unknown destination must wait for native focus to settle');

document.activeElement = outside;
root.listeners.focusout({relatedTarget: null}); // Favorite re-render temporarily removes focus.
document.activeElement = star;
root.listeners.focusin({});
await tick();
assert.equal(popup.hidden, false, 'Restored favorite focus must cancel pending dismissal');

document.activeElement = outside;
root.listeners.focusout({relatedTarget: null});
picker.close();
picker.open();
document.activeElement = input;
await tick();
assert.equal(popup.hidden, false, 'A prior dismissal timer must not affect a reopened picker');

document.activeElement = outside;
root.listeners.focusout({relatedTarget: null});
await tick();
assert.equal(popup.hidden, true, 'Focus that stays outside must close the popup after settling');
""".replace("PICKER_URL", repr(picker_url))
    result = subprocess.run([NODE, "--input-type=module"], input=script, text=True, encoding="utf-8", capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
