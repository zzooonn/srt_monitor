import { matchingStations } from './station-model.js';

export class StationPicker {
  constructor(name, { favorites, onSelect, onFavorite, onOpen }) {
    this.name = name;
    this.hidden = document.getElementById(name);
    this.input = document.getElementById(`${name}-search`);
    this.root = this.input.closest('.station-picker');
    this.popup = document.getElementById(`${name}-popup`);
    this.list = document.getElementById(`${name}-list`);
    this.empty = document.getElementById(`${name}-empty`);
    this.toggle = this.root.querySelector('.station-toggle');
    this.favorites = favorites;
    this.onSelect = onSelect;
    this.onFavorite = onFavorite;
    this.onOpen = onOpen;
    this.stations = [];
    this.matches = [];
    this.active = -1;
    this.disabled = true;
    this.focusTimer = null;
    this.input.setAttribute('aria-required', 'true');
    this.input.addEventListener('click', () => this.open());
    this.input.addEventListener('input', event => { event.stopPropagation(); this.open(false); this.active = -1; this.render(); });
    this.input.addEventListener('change', event => event.stopPropagation());
    this.input.addEventListener('keydown', event => this.keydown(event));
    this.toggle.addEventListener('click', () => {
      if (this.disabled) return;
      this.input.focus();
      if (this.popup.hidden) this.open(); else this.close();
    });
    this.root.addEventListener('focusin', () => { clearTimeout(this.focusTimer); this.focusTimer = null; });
    this.root.addEventListener('focusout', event => {
      clearTimeout(this.focusTimer);
      this.focusTimer = null;
      if (event.relatedTarget) {
        if (!this.root.contains(event.relatedTarget)) this.close();
        return;
      }
      // A removed favorite button or unfinished native focus move may report
      // no destination yet. Wait for that move to finish before dismissing.
      this.focusTimer = setTimeout(() => {
        this.focusTimer = null;
        if (!this.root.contains(document.activeElement)) this.close();
      }, 0);
    });
    this.root.addEventListener('keydown', event => {
      if (event.key === 'Escape' && event.target !== this.input && !this.popup.hidden) {
        event.preventDefault(); this.close(); this.input.focus();
      }
    });
    document.addEventListener('pointerdown', event => { if (!this.root.contains(event.target)) this.close(); });
  }

  setOptions(stations, emptyMessage) {
    this.stations = stations;
    this.emptyMessage = emptyMessage;
    if (!this.popup.hidden) { this.active = -1; this.render(); }
  }

  setValue(value) {
    this.hidden.value = value;
    this.close();
  }

  setDisabled(disabled) {
    this.disabled = disabled;
    this.input.disabled = disabled;
    this.toggle.disabled = disabled;
    this.list.querySelectorAll('button').forEach(button => { button.disabled = disabled; });
    if (disabled) this.close();
  }

  open(reset = true) {
    if (this.disabled) return;
    if (this.popup.hidden) {
      this.onOpen(this);
      this.popup.hidden = false;
      this.root.classList.add('is-open');
      this.input.setAttribute('aria-expanded', 'true');
      if (reset) this.input.value = '';
      this.active = -1;
      this.render();
    }
  }

  close() {
    clearTimeout(this.focusTimer);
    this.focusTimer = null;
    this.popup.hidden = true;
    this.root.classList.remove('is-open');
    this.input.setAttribute('aria-expanded', 'false');
    this.input.removeAttribute('aria-activedescendant');
    this.input.value = this.hidden.value;
    this.active = -1;
  }

  choose(name) {
    if (this.disabled || !this.stations.some(station => station.name === name)) return;
    this.setValue(name);
    this.input.focus();
    this.onSelect(name);
  }

  render() {
    const focusedFavorite = document.activeElement?.dataset.favorite;
    const previousActive = this.matches[this.active]?.name;
    this.matches = matchingStations(this.stations, this.input.value, this.favorites());
    this.active = previousActive ? this.matches.findIndex(station => station.name === previousActive) : -1;
    this.list.replaceChildren();
    this.empty.hidden = Boolean(this.matches.length);
    this.empty.textContent = this.stations.length ? '검색 결과가 없습니다. 다른 역 이름을 입력해 주세요.' : this.emptyMessage;
    let lastGroup = '';
    for (const [index, station] of this.matches.entries()) {
      const favorite = this.favorites().has(station.name);
      const group = favorite ? '즐겨찾기' : '역 목록';
      if (group !== lastGroup) {
        const heading = document.createElement('div');
        heading.className = 'station-group'; heading.setAttribute('role', 'presentation'); heading.textContent = group;
        this.list.append(heading); lastGroup = group;
      }
      const row = document.createElement('div'); row.className = 'station-option-row'; row.setAttribute('role', 'presentation');
      const option = document.createElement('div');
      option.id = `${this.name}-option-${index}`; option.className = 'station-option'; option.setAttribute('role', 'option');
      option.setAttribute('aria-selected', String(station.name === this.hidden.value));
      const label = document.createElement('span'); label.textContent = station.name;
      const meta = document.createElement('small'); meta.textContent = station.line || '';
      option.append(label, meta);
      option.addEventListener('pointerdown', event => event.preventDefault());
      option.addEventListener('click', () => this.choose(station.name));
      const star = document.createElement('button');
      star.type = 'button'; star.className = 'station-favorite'; star.textContent = favorite ? '★' : '☆';
      star.dataset.favorite = station.name;
      star.setAttribute('aria-label', `${station.name} 즐겨찾기 ${favorite ? '해제' : '등록'}`);
      star.setAttribute('aria-pressed', String(favorite)); star.disabled = this.disabled;
      // Keep the combobox focused during pointer selection so its blur cannot
      // dismiss the popup before the subsequent favorite click is delivered.
      star.addEventListener('pointerdown', event => event.preventDefault());
      star.addEventListener('click', () => { if (!this.disabled) this.onFavorite(station.name); });
      row.append(option, star); this.list.append(row);
    }
    this.highlight();
    if (focusedFavorite) [...this.list.querySelectorAll('[data-favorite]')].find(button => button.dataset.favorite === focusedFavorite)?.focus({ preventScroll: true });
  }

  highlight() {
    const options = this.list.querySelectorAll('[role=option]');
    options.forEach((option, index) => option.classList.toggle('is-active', index === this.active));
    const active = options[this.active];
    if (active) { this.input.setAttribute('aria-activedescendant', active.id); active.scrollIntoView({ block: 'nearest' }); }
    else this.input.removeAttribute('aria-activedescendant');
  }

  keydown(event) {
    if (this.disabled) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault(); this.open();
      if (!this.matches.length) return;
      this.active = this.active < 0 ? (event.key === 'ArrowDown' ? 0 : this.matches.length - 1)
        : (this.active + (event.key === 'ArrowDown' ? 1 : -1) + this.matches.length) % this.matches.length;
      this.highlight();
    } else if (event.key === 'Enter') {
      event.preventDefault();
      if (this.popup.hidden) this.open();
      else if (this.active >= 0) this.choose(this.matches[this.active].name);
    } else if (event.key === 'Escape') { if (!this.popup.hidden) { event.preventDefault(); this.close(); } }
    // Forward Tab reaches the favorite buttons; leaving the picker closes it.
    else if (event.key === 'Tab' && (event.shiftKey || !this.matches.length)) this.close();
  }
}
