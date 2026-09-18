// <pelias-search>: reusable single-field autocomplete for Pelias.
//
//   <pelias-search placeholder="Search Maine" min-chars="2" debounce="160"></pelias-search>
//   el.client  = new PeliasClient();            // required
//   el.context = () => ({ focus, boundary, layers, sources });  // optional, per request
//   el.addEventListener('pelias-select', e => e.detail.feature);
//   el.addEventListener('pelias-results', e => e.detail.features);
//
// Light DOM (no shadow root) so the host page styles it; class names are prefixed "ps-".
// Keyboard: ArrowUp/ArrowDown move, Enter selects (or runs a full search), Escape closes.
// All result text is rendered with textContent; nothing from the API is parsed as HTML.

let instances = 0;

export class PeliasSearch extends HTMLElement {
  #input;
  #list;
  #status;
  #features = [];
  #active = -1;
  #timer = 0;
  #controller = null;

  client = null;
  context = () => ({});

  connectedCallback() {
    if (this.#input) return;
    const id = `ps-${++instances}`;
    this.classList.add('ps');

    this.#input = document.createElement('input');
    Object.assign(this.#input, {
      type: 'search',
      id: `${id}-input`,
      className: 'ps-input',
      autocomplete: 'off',
      spellcheck: false,
      placeholder: this.getAttribute('placeholder') || 'Search',
    });
    this.#input.setAttribute('role', 'combobox');
    this.#input.setAttribute('aria-autocomplete', 'list');
    this.#input.setAttribute('aria-expanded', 'false');
    this.#input.setAttribute('aria-controls', `${id}-list`);
    this.#input.setAttribute('aria-label', this.getAttribute('label') || 'Search places and addresses');

    this.#list = document.createElement('ul');
    this.#list.id = `${id}-list`;
    this.#list.className = 'ps-list';
    this.#list.setAttribute('role', 'listbox');
    this.#list.hidden = true;

    this.#status = document.createElement('div');
    this.#status.className = 'ps-status';
    this.#status.setAttribute('role', 'status');
    this.#status.setAttribute('aria-live', 'polite');

    this.append(this.#input, this.#list, this.#status);

    this.#input.addEventListener('input', () => this.#schedule());
    this.#input.addEventListener('keydown', (e) => this.#onKey(e));
    this.#input.addEventListener('blur', () => setTimeout(() => this.#close(), 120));
    this.#input.addEventListener('focus', () => {
      if (this.#features.length) this.#open();
    });
  }

  get value() {
    return this.#input?.value ?? '';
  }

  set value(v) {
    if (this.#input) this.#input.value = v;
  }

  focus() {
    this.#input?.focus();
  }

  get #minChars() {
    return Number(this.getAttribute('min-chars') || 2);
  }

  get #debounce() {
    return Number(this.getAttribute('debounce') || 160);
  }

  #schedule() {
    clearTimeout(this.#timer);
    const text = this.#input.value.trim();
    if (text.length < this.#minChars) {
      this.#controller?.abort();
      this.#render([]);
      this.#setStatus('');
      return;
    }
    this.#timer = setTimeout(() => this.#run('autocomplete', text), this.#debounce);
  }

  async #run(kind, text) {
    if (!this.client) throw new Error('<pelias-search> needs .client');
    this.#controller?.abort();
    const controller = new AbortController();
    this.#controller = controller;
    this.classList.add('ps-busy');
    try {
      const opts = { size: 8, ...this.context() };
      const fc = kind === 'search'
        ? await this.client.search(text, opts, controller.signal)
        : await this.client.autocomplete(text, opts, controller.signal);
      if (controller.signal.aborted) return;
      this.#render(fc.features || []);
      this.#setStatus(fc.features?.length ? `${fc.features.length} results` : 'No matches');
      this.dispatchEvent(new CustomEvent('pelias-results', {
        detail: { kind, text, features: fc.features || [] },
      }));
      if (kind === 'search' && fc.features?.length) this.#select(0);
    } catch (err) {
      if (err.name === 'AbortError') return;
      this.#render([]);
      this.#setStatus(err.message || 'Search failed');
    } finally {
      if (this.#controller === controller) this.classList.remove('ps-busy');
    }
  }

  #render(features) {
    this.#features = features;
    this.#active = -1;
    this.#list.replaceChildren();
    features.forEach((f, i) => {
      const p = f.properties || {};
      const li = document.createElement('li');
      li.id = `${this.#list.id}-${i}`;
      li.className = 'ps-option';
      li.setAttribute('role', 'option');
      li.setAttribute('aria-selected', 'false');

      const name = document.createElement('span');
      name.className = 'ps-name';
      name.textContent = p.name || p.label || '';
      const detail = document.createElement('span');
      detail.className = 'ps-detail';
      detail.textContent = [p.locality || p.localadmin, p.county, p.region_a].filter(Boolean).join(', ');
      const tag = document.createElement('span');
      tag.className = `ps-tag ps-tag-${p.layer || 'other'}`;
      tag.textContent = p.layer || '';

      li.append(tag, name, detail);
      li.addEventListener('mousedown', (e) => {
        e.preventDefault(); // keep focus in the input
        this.#select(i);
      });
      this.#list.append(li);
    });
    features.length ? this.#open() : this.#close();
  }

  #open() {
    this.#list.hidden = false;
    this.#input.setAttribute('aria-expanded', 'true');
  }

  #close() {
    this.#list.hidden = true;
    this.#input.setAttribute('aria-expanded', 'false');
    this.#input.removeAttribute('aria-activedescendant');
  }

  #highlight(i) {
    const items = this.#list.children;
    if (!items.length) return;
    this.#active = (i + items.length) % items.length;
    [...items].forEach((li, j) => li.setAttribute('aria-selected', String(j === this.#active)));
    this.#input.setAttribute('aria-activedescendant', items[this.#active].id);
    items[this.#active].scrollIntoView({ block: 'nearest' });
    this.dispatchEvent(new CustomEvent('pelias-highlight', {
      detail: { feature: this.#features[this.#active] },
    }));
  }

  #select(i) {
    const feature = this.#features[i];
    if (!feature) return;
    this.#input.value = feature.properties?.label || this.#input.value;
    this.#close();
    this.dispatchEvent(new CustomEvent('pelias-select', { detail: { feature } }));
  }

  #onKey(e) {
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        if (this.#list.hidden && this.#features.length) this.#open();
        this.#highlight(this.#active + 1);
        break;
      case 'ArrowUp':
        e.preventDefault();
        this.#highlight(this.#active - 1);
        break;
      case 'Enter':
        e.preventDefault();
        clearTimeout(this.#timer);
        if (this.#active >= 0 && !this.#list.hidden) this.#select(this.#active);
        else if (this.#input.value.trim()) this.#run('search', this.#input.value.trim());
        break;
      case 'Escape':
        this.#close();
        break;
      default:
    }
  }

  #setStatus(text) {
    this.#status.textContent = text;
  }
}

if (!customElements.get('pelias-search')) {
  customElements.define('pelias-search', PeliasSearch);
}
