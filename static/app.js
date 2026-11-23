/**
 * Leslie Partnership OS - Core Intelligence Logic v2.0
 * Multi-client tourism edition (2026)
 */

document.addEventListener('DOMContentLoaded', () => {
    
    // ─── STATE ───
    let currentState = {
        currentUser: { id: 'owner', name: 'Leslie', emoji: '⚓' },
        currentClient: { slug: localStorage.getItem('leslie_active_client') || 'default', name: 'Portafoglio personale' },
        clients: [],
        opportunities: [],
        currentView: 'clients',
        learnedIntelligence: [],
        lastResults: [],
        weights: {}
    };

    // Tutte le chiamate API ereditano automaticamente lo spazio cliente attivo.
    const nativeFetch = window.fetch.bind(window);
    window.fetch = (input, init = {}) => {
        const url = typeof input === 'string' ? input : input.url;
        if (!url || (!url.startsWith('/api/') && !url.startsWith(window.location.origin + '/api/'))) {
            return nativeFetch(input, init);
        }
        const headers = new Headers(init.headers || (typeof input !== 'string' ? input.headers : undefined));
        headers.set('X-User-ID', currentState.currentUser.id);
        headers.set('X-Client-ID', currentState.currentClient.slug);
        return nativeFetch(input, { ...init, headers });
    };

    function escapeHTML(str) {
        if (!str) return '';
        return str.toString()
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function safeHostname(url) {
        try { return new URL(url).hostname; } catch { return url || 'N/D'; }
    }

    // Single-user mode
    const FIXED_USER = { id: 'owner', name: 'Leslie', emoji: '⚓' };

    // ─── UI ELEMENTS ───
    const elements = {
        navItems: document.querySelectorAll('.nav-item'),
        views: document.querySelectorAll('.view'),
        viewTitle: document.getElementById('view-title'),
        scoutBtn: document.getElementById('scout-btn'),
        learnedNiches: document.getElementById('learned-niches'),
        discoveryResults: document.getElementById('discovery-results'),
        savedLeadsList: document.getElementById('saved-leads-list'),
        settingsPanel: document.getElementById('settings-panel-content'),
        searchInput: document.getElementById('search-input'),
        searchBtn: document.getElementById('search-btn'),
        toast: document.getElementById('toast'),
        exportLink: document.getElementById('export-link')
    };

    // ─── HELPERS: API, busy state, toast con undo ───

    async function apiFetch(url, opts = {}, userMsg = null) {
        try {
            const headers = {
                'X-User-ID': currentState.currentUser.id,
                ...(opts.body ? { 'Content-Type': 'application/json' } : {}),
                ...(opts.headers || {})
            };
            const resp = await fetch(url, { ...opts, headers });
            if (resp.status === 429) {
                // Soglia API raggiunta — sempre visibile (anche se userMsg=false)
                let body = {};
                try { body = await resp.json(); } catch {}
                showToast(body.message || 'Soglia API raggiunta. Riprova domani.');
                const err = new Error(`HTTP 429 ${body.provider || ''}`);
                err.code = 'limit_exceeded';
                err.body = body;
                throw err;
            }
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const ct = resp.headers.get('content-type') || '';
            return ct.includes('application/json') ? await resp.json() : await resp.text();
        } catch (e) {
            console.error(`API ${url}:`, e);
            if (userMsg !== false && e.code !== 'limit_exceeded') {
                showToast(userMsg || 'Errore di connessione.');
            }
            throw e;
        }
    }

    function setBusy(btn, busy) {
        if (!btn) return;
        btn.disabled = busy;
        btn.dataset.busy = busy ? '1' : '';
    }

    function showUndoToast(message, onCommit, onUndo, durationMs = 5000) {
        const toast = elements.toast;
        toast.innerHTML = `<span>${escapeHTML(message)}</span>
            <button class="btn btn-ghost" id="undo-btn" style="margin-left:16px;">Annulla</button>
            <span id="undo-counter" style="margin-left:8px; font-size:12px; opacity:0.7;"></span>`;
        toast.style.display = 'flex';
        toast.style.alignItems = 'center';
        let remaining = Math.ceil(durationMs / 1000);
        const counter = toast.querySelector('#undo-counter');
        counter.textContent = `${remaining}s`;
        const tick = setInterval(() => {
            remaining--;
            if (remaining >= 0) counter.textContent = `${remaining}s`;
        }, 1000);
        const finish = (undone) => {
            clearInterval(tick);
            clearTimeout(timer);
            toast.style.display = 'none';
            toast.innerHTML = '';
            if (undone) onUndo && onUndo();
            else onCommit && onCommit();
        };
        const timer = setTimeout(() => finish(false), durationMs);
        toast.querySelector('#undo-btn').onclick = () => finish(true);
    }

    // ─── SYNC STATUS INDICATOR ───
    async function updateSyncStatus() {
        if (document.hidden) return;
        const dot = document.getElementById('sync-dot');
        const label = document.getElementById('sync-status');
        if (!dot || !label) return;
        try {
            const s = await apiFetch('/api/status', {}, false);
            dot.classList.remove('syncing', 'error', 'offline');
            if (!s.gcs_enabled) {
                dot.classList.add('offline');
                label.textContent = 'Leslie: modalità locale';
            } else if (s.gcs_dirty) {
                dot.classList.add('syncing');
                label.textContent = 'Sincronizzazione…';
            } else if (s.last_upload_ok === false) {
                dot.classList.add('error');
                label.textContent = 'Errore sync GCS';
            } else {
                label.textContent = 'Leslie Cloud Sync: Online';
            }
        } catch (e) {
            console.error('Sync Status Error:', e);
            dot.classList.remove('syncing');
            dot.classList.add('error');
            label.textContent = 'Server non raggiungibile';
        }
    }

    // ─── API USAGE PANEL ───
    function severity(pct) {
        if (pct >= 90) return { color: '#ef4444', label: 'CRITICO' };
        if (pct >= 70) return { color: '#facc15', label: 'ATTENZIONE' };
        return { color: '#4ade80', label: 'OK' };
    }

    function renderUsageBar(title, today, daily_limit, month, monthly_limit, breakdown) {
        const dayPct = Math.min(100, Math.round((today / daily_limit) * 100));
        const monthPct = Math.min(100, Math.round((month / monthly_limit) * 100));
        const sev = severity(Math.max(dayPct, monthPct));
        const breakdownHtml = breakdown && Object.keys(breakdown).length
            ? `<div style="font-size:10px; color:var(--text-muted); margin-top:6px;">${
                Object.entries(breakdown).map(([k, v]) => `${k}: ${v}`).join(' · ')
            }</div>`
            : '';
        return `
            <div style="background: rgba(255,255,255,0.02); border:1px solid var(--border); border-radius:14px; padding:18px;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                    <div style="font-weight:600; font-size:14px;">${title}</div>
                    <span style="font-size:10px; font-weight:700; letter-spacing:1px; color:${sev.color};">${sev.label}</span>
                </div>
                <div style="font-size:11px; color:var(--text-muted); margin-bottom:4px; display:flex; justify-content:space-between;">
                    <span>Oggi</span>
                    <span><strong style="color:var(--text-main);">${today}</strong> / ${daily_limit}</span>
                </div>
                <div style="width:100%; height:6px; background:rgba(255,255,255,0.05); border-radius:3px; overflow:hidden; margin-bottom:10px;">
                    <div style="width:${dayPct}%; height:100%; background:${severity(dayPct).color}; transition:width .4s;"></div>
                </div>
                <div style="font-size:11px; color:var(--text-muted); margin-bottom:4px; display:flex; justify-content:space-between;">
                    <span>Mese</span>
                    <span><strong style="color:var(--text-main);">${month}</strong> / ${monthly_limit}</span>
                </div>
                <div style="width:100%; height:6px; background:rgba(255,255,255,0.05); border-radius:3px; overflow:hidden;">
                    <div style="width:${monthPct}%; height:100%; background:${severity(monthPct).color}; transition:width .4s;"></div>
                </div>
                ${breakdownHtml}
            </div>
        `;
    }

    async function updateApiUsage() {
        if (document.hidden) return;
        try {
            const u = await apiFetch('/api/usage', {}, false);
            // Widget sempre visibile: prende la % più alta fra Places e DeepSeek
            const pct = Math.max(u.places.budget_used_pct || 0, u.deepseek.budget_used_pct || 0);
            const total = (u.places.estimated_cost_usd || 0) + (u.deepseek.estimated_cost_usd || 0);
            const budgetTotal = (u.places.monthly_budget_usd || 0) + (u.deepseek.monthly_budget_usd || 0);
            const widget = document.getElementById('cost-widget');
            if (widget) {
                widget.classList.remove('warn', 'crit');
                if (pct >= 80) widget.classList.add('crit');
                else if (pct >= 50) widget.classList.add('warn');
                document.getElementById('cost-widget-value').textContent = `$${total.toFixed(2)} / $${budgetTotal.toFixed(0)}`;
                document.getElementById('cost-widget-pct').textContent = `${Math.round(pct)}%`;
            }
            // Alert 80% — persistente ma dismissibile
            const alertEl = document.getElementById('budget-alert');
            if (alertEl && pct >= 80 && sessionStorage.getItem('budget_alert_dismissed_month') !== u.today.slice(0,7)) {
                alertEl.style.display = 'block';
                alertEl.classList.toggle('crit', pct >= 100);
                document.getElementById('budget-alert-detail').textContent =
                    `Places: $${u.places.estimated_cost_usd}/${u.places.monthly_budget_usd} (${u.places.budget_used_pct}%) · ` +
                    `DeepSeek: $${u.deepseek.estimated_cost_usd}/${u.deepseek.monthly_budget_usd} (${u.deepseek.budget_used_pct}%). ` +
                    `Al raggiungimento del cap, lo scout viene bloccato in real-time.`;
                alertEl.querySelector('button').onclick = () => {
                    alertEl.style.display = 'none';
                    sessionStorage.setItem('budget_alert_dismissed_month', u.today.slice(0,7));
                };
            }
            // Legacy: pannello costi nel tab settings, se presente
            const bars = document.getElementById('usage-bars');
            const costEl = document.getElementById('usage-cost');
            if (bars) {
                bars.innerHTML =
                    renderUsageBar('Google Places', u.places.today, u.places.daily_limit, u.places.month, u.places.monthly_limit, u.places.breakdown) +
                    renderUsageBar('DeepSeek AI', u.deepseek.today, u.deepseek.daily_limit, u.deepseek.month, u.deepseek.monthly_limit, null);
            }
            if (costEl) {
                costEl.textContent = `Costo stimato mese: $${total.toFixed(2)} (Places $${u.places.estimated_cost_usd} · AI $${u.deepseek.estimated_cost_usd})`;
            }
        } catch (e) {
            console.error('Usage error:', e);
        }
    }

    async function updateFollowupsBadge() {
        if (document.hidden) return;
        try {
            const r = await apiFetch('/api/leads/followups?days=7', {}, false);
            const badge = document.getElementById('followups-badge');
            const cnt = document.getElementById('followups-count');
            if (!badge || !cnt) return;
            if (r.count > 0) {
                badge.style.display = 'inline-flex';
                cnt.textContent = r.count;
            } else {
                badge.style.display = 'none';
            }
        } catch (e) { /* silenzioso */ }
    }

    // Modal cleanup on Esc
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            const detailModal = document.getElementById('lead-detail-modal');
            if (detailModal) detailModal.style.display = 'none';
            const clientDialog = document.getElementById('client-modal');
            if (clientDialog && !clientDialog.hidden) clientDialog.hidden = true;
        }
    });

    // ─── INITIALIZATION ───
    init();

    async function init() {
        await loadClients();
        updateExportLink();
        await loadIntelligence();
        await loadSettings();
        
        // Ensure indicator is positioned on current active item
        const activeItem = document.querySelector('.nav-item.active');
        if (activeItem) moveIndicator(activeItem);
        
        switchView(currentState.currentView);
        
        // Auto-refresh stats every 60s (solo se la tab è visibile)
        if (window.statsInterval) clearInterval(window.statsInterval);
        window.statsInterval = setInterval(() => {
            if (!document.hidden) loadIntelligence();
        }, 60000);

        // Sync status + API usage + follow-ups polling
        updateSyncStatus();
        updateApiUsage();
        updateFollowupsBadge();
        if (window.syncInterval) clearInterval(window.syncInterval);
        window.syncInterval = setInterval(() => {
            updateSyncStatus();
            updateApiUsage();
            updateFollowupsBadge();
        }, 30000);
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden) {
                updateSyncStatus();
                updateApiUsage();
                updateFollowupsBadge();
            }
        });
    }

    function updateExportLink() {
        if (elements.exportLink) {
            elements.exportLink.href = `/api/export?client_id=${encodeURIComponent(currentState.currentClient.slug)}`;
        }
    }

    async function loadClients() {
        try {
            [currentState.clients, currentState.opportunities] = await Promise.all([
                apiFetch('/api/clients/overview', {}, false),
                apiFetch('/api/opportunities/network', {}, false),
            ]);
        } catch {
            currentState.clients = [];
            currentState.opportunities = [];
        }
        const selected = currentState.clients.find(c => c.slug === currentState.currentClient.slug);
        if (currentState.currentClient.slug !== 'default' && !selected) {
            currentState.currentClient = { slug: 'default', name: 'Portafoglio personale' };
            localStorage.setItem('leslie_active_client', 'default');
        } else if (selected) {
            currentState.currentClient = selected;
        }
        renderClientSwitcher();
    }

    function renderClientSwitcher() {
        const name = document.getElementById('active-client-name');
        const meta = document.getElementById('active-client-meta');
        const list = document.getElementById('client-menu-list');
        if (!name || !meta || !list) return;
        name.textContent = currentState.currentClient.name;
        meta.textContent = currentState.currentClient.slug === 'default'
            ? 'Tutti i dati storici'
            : [currentState.currentClient.sector, currentState.currentClient.geography].filter(Boolean).join(' · ') || 'Progetto cliente';
        const workspaces = [
            { slug: 'default', name: 'Portafoglio personale' },
            ...currentState.clients,
        ];
        list.innerHTML = workspaces.map(client => `
            <button type="button" class="client-menu-item ${client.slug === currentState.currentClient.slug ? 'active' : ''}" data-client="${escapeHTML(client.slug)}">
                ${escapeHTML(client.name)}
            </button>`).join('');
        list.querySelectorAll('[data-client]').forEach(button => {
            button.onclick = () => selectClient(button.dataset.client);
        });
        renderActiveClientProfile();
        renderClientsView();
        updateClientGuard();
    }

    function hasActiveClient() {
        return currentState.currentClient.slug && currentState.currentClient.slug !== 'default';
    }

    function requireActiveClient() {
        if (hasActiveClient()) return true;
        showToast('Scegli prima il cliente per cui stai lavorando.');
        switchView('clients');
        return false;
    }

    function updateClientGuard() {
        const locked = !hasActiveClient();
        const banner = document.getElementById('client-required-banner');
        const discoveryGrid = document.querySelector('#discovery-view > .grid');
        if (banner) banner.hidden = !locked;
        if (discoveryGrid) discoveryGrid.classList.toggle('client-locked', locked);
        if (elements.searchInput) elements.searchInput.disabled = locked;
        if (elements.searchBtn) elements.searchBtn.disabled = locked;
        const strategyButton = document.getElementById('start-strategy-btn');
        if (strategyButton) strategyButton.disabled = locked;
        if (elements.scoutBtn) elements.scoutBtn.title = locked ? 'Scegli prima un cliente' : `Trova partner per ${currentState.currentClient.name}`;
        renderMissionCard();
    }

    function renderMissionCard() {
        const card = document.getElementById('mission-card');
        const client = currentState.currentClient;
        if (!card) return;
        card.hidden = !hasActiveClient();
        if (card.hidden) return;
        const playbook = client.playbook || {};
        document.getElementById('mission-title').textContent = client.primary_goal || `Trova nuovi partner per ${client.name}`;
        document.getElementById('mission-focus').textContent = playbook.scoring_focus || 'Leslie confronterà pubblico, territorio, capacità e potenziale commerciale.';
        const list = document.getElementById('mission-query-list');
        list.innerHTML = (playbook.recommended_queries || []).slice(0, 4).map(query =>
            `<button type="button" data-mission-query="${escapeHTML(query)}">${escapeHTML(query)}</button>`
        ).join('');
        list.querySelectorAll('[data-mission-query]').forEach(button => {
            button.onclick = () => executeSearch(button.dataset.missionQuery);
        });
    }

    function renderClientsView() {
        const grid = document.getElementById('clients-grid');
        if (!grid) return;
        const totals = currentState.clients.reduce((sum, client) => {
            const stats = client.stats || {};
            sum.leads += stats.leads || 0;
            sum.contacted += stats.contacted || 0;
            sum.partners += stats.partners || 0;
            return sum;
        }, { leads: 0, contacted: 0, partners: 0 });
        document.getElementById('portfolio-client-count').textContent = currentState.clients.length;
        document.getElementById('portfolio-lead-count').textContent = totals.leads;
        document.getElementById('portfolio-contacted-count').textContent = totals.contacted;
        document.getElementById('portfolio-partner-count').textContent = totals.partners;
        const priorityClient = currentState.clients.find(client => client.next_action?.kind === 'followup')
            || currentState.clients.find(client => client.next_action?.kind === 'outreach')
            || currentState.clients[0];
        document.getElementById('portfolio-focus').textContent = priorityClient
            ? `Priorità: ${priorityClient.name} — ${priorityClient.next_action?.label || 'controlla il profilo'}.`
            : 'Crea il primo cliente per preparare una strategia.';
        if (!currentState.clients.length) {
            grid.innerHTML = `<div class="card card-full"><h3>Nessun cliente configurato</h3><p style="color:var(--text-muted);margin:8px 0 18px;">Crea il primo profilo per iniziare una ricerca mirata.</p><button class="btn btn-primary" data-new-client><i class="fa-solid fa-plus"></i> Crea cliente</button></div>`;
        } else {
            grid.innerHTML = currentState.clients.map(client => {
                const stats = client.stats || {};
                const hospitality = client.business_type === 'hospitality';
                const color = hospitality ? '#67c5b8' : client.business_type === 'nautical' ? '#c5a059' : '#8fa6d8';
                const completeness = client.profile_completeness || 0;
                const factTwoLabel = hospitality ? 'Strutture' : 'Capienza';
                const factTwoValue = hospitality ? (client.inventory_count || 1) : (client.max_guests ? `Max ${client.max_guests}` : '—');
                return `
                <article class="client-card ${client.slug === currentState.currentClient.slug ? 'active' : ''}" style="--client-color:${color}">
                    <div class="client-card-top"><div class="client-card-avatar">${escapeHTML(client.name.slice(0,1).toUpperCase())}</div>${client.slug === currentState.currentClient.slug ? '<span class="badge badge-gold">Attivo</span>' : ''}</div>
                    <h3>${escapeHTML(client.name)}</h3>
                    <div class="client-card-offer">${escapeHTML(client.offer_name || client.sector || 'Offerta da completare')}</div>
                    <div class="client-card-facts">
                        <div class="client-card-fact"><small>${hospitality ? 'Categoria' : 'Mezzo'}</small><strong>${escapeHTML(client.boat_type || '—')}</strong></div>
                        <div class="client-card-fact"><small>${factTwoLabel}</small><strong>${factTwoValue}</strong></div>
                        <div class="client-card-fact"><small>Area</small><strong>${escapeHTML(client.geography || '—')}</strong></div>
                    </div>
                    <div class="client-card-metrics"><span><b>${stats.leads || 0}</b> opportunità</span><span><b>${stats.contacted || 0}</b> conversazioni</span><span><b>${stats.partners || 0}</b> partner</span></div>
                    <div class="client-card-status">
                        <span class="profile-ring" style="--progress:${completeness}" data-value="${completeness}%"></span>
                        <div><small>Prossima mossa</small><strong>${escapeHTML(client.next_action?.label || 'Completa il profilo')}</strong></div>
                    </div>
                    <div class="client-card-actions">
                        <button class="btn btn-primary" data-open-client="${escapeHTML(client.slug)}" data-action="${escapeHTML(client.next_action?.kind || 'profile')}"><i class="fa-solid fa-arrow-right"></i> Apri piano</button>
                        <button class="btn btn-ghost" data-edit-client="${escapeHTML(client.slug)}" aria-label="Modifica ${escapeHTML(client.name)}"><i class="fa-solid fa-pen"></i></button>
                    </div>
                </article>`;
            }).join('');
        }
        grid.querySelectorAll('[data-open-client]').forEach(button => {
            button.onclick = async () => {
                await selectClient(button.dataset.openClient);
                if (button.dataset.action === 'profile') openClientForm(currentState.currentClient);
                else if (button.dataset.action === 'outreach' || button.dataset.action === 'followup') switchView('leads');
                else switchView('discovery');
            };
        });
        grid.querySelectorAll('[data-edit-client]').forEach(button => {
            button.onclick = () => openClientForm(currentState.clients.find(client => client.slug === button.dataset.editClient));
        });
        grid.querySelector('[data-new-client]')?.addEventListener('click', () => openClientForm());
        renderOpportunityNetwork();
    }

    function renderOpportunityNetwork() {
        const panel = document.getElementById('network-panel');
        const grid = document.getElementById('network-grid');
        if (!panel || !grid) return;
        panel.hidden = !currentState.opportunities.length;
        grid.innerHTML = currentState.opportunities.slice(0, 6).map(item => `
            <article class="network-card">
                <span class="network-link-icon"><i class="fa-solid fa-link"></i></span>
                <div><small>${escapeHTML(item.suggested_offer || 'Collaborazione')}</small><strong>${escapeHTML(item.title)}</strong><p>${escapeHTML(item.detail)}</p><em>${escapeHTML(item.next_action || '')}</em></div>
            </article>`).join('');
    }

    function renderActiveClientProfile() {
        const bar = document.getElementById('client-profile-bar');
        if (!bar) return;
        const client = currentState.currentClient;
        bar.hidden = client.slug === 'default';
        if (bar.hidden) return;
        document.getElementById('client-profile-offer').textContent = client.offer_name || client.sector || client.name;
        document.getElementById('client-profile-description').textContent = client.experience_style || client.target_customer || 'Completa il profilo per guidare la ricerca.';
        document.getElementById('client-profile-boat').textContent = client.boat_type || 'Struttura da indicare';
        document.getElementById('client-profile-capacity').textContent = client.business_type === 'hospitality'
            ? `${client.inventory_count || 1} strutture`
            : (client.max_guests ? `Max ${client.max_guests} ospiti` : 'Capienza da indicare');
        document.getElementById('client-profile-area').textContent = client.geography || 'Area da indicare';
    }

    async function selectClient(slug) {
        if (window.currentEventSource) {
            try { window.currentEventSource.close(); } catch {}
            window.currentEventSource = null;
        }
        currentState.currentClient = slug === 'default'
            ? { slug: 'default', name: 'Portafoglio personale' }
            : currentState.clients.find(c => c.slug === slug) || { slug: 'default', name: 'Portafoglio personale' };
        localStorage.setItem('leslie_active_client', currentState.currentClient.slug);
        currentState.learnedIntelligence = [];
        currentState.lastResults = [];
        currentState.weights = {};
        document.getElementById('client-menu').hidden = true;
        renderClientSwitcher();
        updateExportLink();
        await Promise.all([loadIntelligence(), loadSettings()]);
        switchView(currentState.currentView);
        showToast(`Cliente attivo: ${currentState.currentClient.name}`);
    }

    const clientSwitcher = document.getElementById('client-switcher-button');
    const clientMenu = document.getElementById('client-menu');
    const clientModal = document.getElementById('client-modal');
    if (clientSwitcher && clientMenu) {
        clientSwitcher.onclick = () => { clientMenu.hidden = !clientMenu.hidden; };
    }
    function openClientForm(client = null) {
        const form = document.getElementById('client-form');
        form.reset();
        form.dataset.mode = client ? 'edit' : 'create';
        form.dataset.slug = client?.slug || '';
        document.getElementById('client-form-badge').textContent = client ? 'Profilo cliente' : 'Nuovo progetto';
        document.getElementById('client-form-title').textContent = client ? `Aggiorna ${client.name}` : 'Per quale cliente cerchiamo partner?';
        document.getElementById('client-form-submit-label').textContent = client ? 'Salva profilo' : 'Crea spazio cliente';
        if (client) {
            ['name', 'business_type', 'sector', 'geography', 'offer_name', 'boat_type', 'inventory_count',
             'max_guests', 'price_positioning', 'experience_style', 'target_customer', 'ideal_partner',
             'exclusions', 'primary_goal', 'differentiators', 'seasonality', 'notes'].forEach(field => {
                if (form.elements[field]) form.elements[field].value = client[field] ?? '';
            });
        }
        updateClientFormLabels(form.elements.business_type.value);
        clientModal.hidden = false;
        form.querySelector('input[name="name"]')?.focus();
    }

    function updateClientFormLabels(type) {
        const hospitality = type === 'hospitality';
        document.getElementById('client-asset-label').textContent = hospitality ? 'Tipologia di proprietà' : 'Mezzo o struttura';
        document.getElementById('client-inventory-label').textContent = hospitality ? 'Numero di case' : 'Numero di mezzi';
        document.getElementById('client-capacity-label').textContent = hospitality ? 'Posti letto totali (opzionale)' : 'Capienza massima';
    }

    document.querySelector('#client-form [name="business_type"]')?.addEventListener('change', event => updateClientFormLabels(event.target.value));

    document.getElementById('add-client-button')?.addEventListener('click', () => {
        clientMenu.hidden = true;
        openClientForm();
    });
    document.getElementById('clients-new-button')?.addEventListener('click', () => openClientForm());
    document.getElementById('edit-client-button')?.addEventListener('click', () => openClientForm(currentState.currentClient));
    document.getElementById('client-modal-close')?.addEventListener('click', () => { clientModal.hidden = true; });
    clientModal?.addEventListener('click', event => { if (event.target === clientModal) clientModal.hidden = true; });
    document.getElementById('client-form')?.addEventListener('submit', async event => {
        event.preventDefault();
        const form = event.currentTarget;
        const submit = form.querySelector('[type="submit"]');
        setBusy(submit, true);
        try {
            const payload = Object.fromEntries(new FormData(form).entries());
            const editing = form.dataset.mode === 'edit';
            const saved = await apiFetch(
                editing ? `/api/clients/${encodeURIComponent(form.dataset.slug)}` : '/api/clients',
                { method: editing ? 'PATCH' : 'POST', body: JSON.stringify(payload) },
            );
            if (editing) {
                currentState.clients = currentState.clients.map(client => client.slug === saved.slug ? saved : client);
            } else {
                currentState.clients.push(saved);
            }
            form.reset();
            clientModal.hidden = true;
            await selectClient(saved.slug);
        } catch (error) {
            showToast(error.message.includes('409') ? 'Esiste già un cliente con questo nome.' : 'Non riesco a creare il cliente.');
        } finally {
            setBusy(submit, false);
        }
    });

    // ─── NAVIGATION ───
    elements.navItems.forEach(item => {
        item.addEventListener('click', () => {
            const view = item.dataset.view;
            if (view) {
                switchView(view);
                elements.navItems.forEach(n => n.classList.remove('active'));
                item.classList.add('active');
                moveIndicator(item);
            }
        });
    });

    function moveIndicator(target) {
        const indicator = document.getElementById('nav-indicator');
        if (indicator && target) {
            indicator.style.top = `${target.offsetTop}px`;
            indicator.style.height = `${target.offsetHeight}px`;
            indicator.style.opacity = '1';
        }
    }

    function switchView(viewId) {
        // Cleanup: chiudi EventSource scout se l'utente lascia la view durante lo streaming
        if (currentState.currentView === 'settings' && viewId !== 'settings' && window.currentEventSource) {
            try { window.currentEventSource.close(); } catch (e) {}
            window.currentEventSource = null;
        }
        currentState.currentView = viewId;
        elements.views.forEach(v => v.classList.remove('active'));
        const targetView = document.getElementById(`${viewId}-view`);
        if (targetView) targetView.classList.add('active');

        // Update Title + Subtitle
        const titles = {
            clients: 'I tuoi clienti',
            sales: 'Banco Vendite & Giri in Barca',
            venues: 'Ristoranti, Case Vacanza & Portali QR',
            guide: 'Come usare Leslie',
            discovery: 'Cerca Nuovi Partner',
            leads: 'I Miei Contatti',
            dashboard: 'Pipeline Commerciale',
            intelligence: 'Panoramica Mercato',
            settings: 'Impostazioni'
        };
        const subtitles = {
            clients: 'Scegli per chi stai lavorando: Leslie adatterà ricerca, valutazione e messaggi.',
            sales: 'Preventivatore in 30s (Gabri 1–6 pax · Massi 7–40 pax) con abbinamento Ristorante sul mare e Case Vacanza.',
            venues: 'Invia prenotazioni tracciate a Ristoranti e Case Vacanza (Ale/Cosimo) e condividi i Portali Partner con QR Code.',
            guide: 'La guida pratica per trovare, contattare e trasformare aziende in partner commerciali.',
            discovery: 'Trova hotel, wedding planner, concierge e agenzie per nuove partnership.',
            leads: 'Tutti i tuoi contatti. Filtra, contatta, tieni traccia dello stato.',
            dashboard: 'Il funnel: da salvato a partner attivo. Le nicchie che convertono davvero.',
            intelligence: 'Nicchie scoperte, ricerche effettuate, andamento.',
            settings: 'Scegli quali settori esplorare e avvia la ricerca automatica.'
        };
        elements.viewTitle.textContent = titles[viewId] || 'Agency OS';
        const subtitleEl = document.getElementById('view-subtitle');
        if (subtitleEl) subtitleEl.innerHTML = subtitles[viewId] || '';

        // Load specific view data
        if (viewId === 'clients') renderClientsView();
        if (viewId === 'sales') loadSalesView();
        if (viewId === 'venues') loadVenuesView();
        if (viewId === 'discovery') loadDiscoveryHome();
        if (viewId === 'leads') loadLeads();
        if (viewId === 'dashboard') loadDashboardView();
        if (viewId === 'intelligence') loadIntelligenceView();
        if (viewId === 'settings') renderSettings();

        // Sync nav item active state
        elements.navItems.forEach(n => {
            n.classList.toggle('active', n.dataset.view === viewId);
        });
        const active = document.querySelector(`.nav-item[data-view='${viewId}']`);
        if (active) moveIndicator(active);
    }
    window.switchView = switchView;

    // ─── INTELLIGENCE & DISCOVERY ───
    async function loadIntelligence() {
        try {
            const [histResp, leadsResp, intelResp] = await Promise.all([
                fetch('/api/queries/history', { headers: { 'X-User-ID': currentState.currentUser.id } }),
                fetch('/api/leads', { headers: { 'X-User-ID': currentState.currentUser.id } }),
                fetch('/api/intelligence', { headers: { 'X-User-ID': currentState.currentUser.id } })
            ]);
            const history = await histResp.json();
            const leads = await leadsResp.json();
            const intelligence = await intelResp.json();

            // Stats reali
            const statsLeads = document.getElementById('stats-total-leads');
            if (statsLeads) {
                statsLeads.textContent = leads.length;
                statsLeads.parentElement.style.cursor = 'pointer';
                statsLeads.parentElement.onclick = () => switchView('leads');
            }

            // Coverage calculation
            const exhaustedCount = history.filter(h => h.is_exhausted).length;
            const totalQueries = history.length || 1;
            const coverage = Math.min(100, Math.round((exhaustedCount / totalQueries) * 100));

            const bar = document.getElementById('coverage-bar');
            const percent = document.getElementById('coverage-percent');
            if (bar && percent) {
                bar.style.width = `${coverage}%`;
                percent.textContent = `${coverage}%`;
            }

            // Render Niches (Top level tags from Intelligence)
            if (elements.learnedNiches) {
                elements.learnedNiches.innerHTML = intelligence.slice(0, 8).map(intel => `
                    <div class="niche-chip" 
                         title="${escapeHTML(intel.description) || 'Analisi strategica in corso...'}"
                         onclick="startScoutForNiche('${escapeHTML(intel.niche_key)}')">
                        <i class="fa-solid fa-wand-magic-sparkles" style="color: var(--accent);"></i>
                        <span>Nicchia: ${escapeHTML(intel.niche_key)}</span>
                    </div>
                `).join('');
            }

        } catch (e) { console.error('Intel Error:', e); }
    }

    // New: Trigger scout for specific niche
    window.startScoutForNiche = (niche) => {
        showToast(`Ricerca mirata per: ${niche}`);
        executeSearch(niche);
    };

    // Wire up refresh button
    const refreshBtn = document.getElementById('refresh-queries');
    if (refreshBtn) {
        refreshBtn.onclick = () => {
            refreshBtn.querySelector('i').classList.add('fa-spin');
            loadIntelligence().then(() => {
                setTimeout(() => refreshBtn.querySelector('i').classList.remove('fa-spin'), 1000);
            });
        };
    }

    window.loadDiscoveryHome = loadDiscoveryHome;
    async function loadDiscoveryHome() {
        const resultsEl = elements.discoveryResults;
        // Mostra ultimi risultati di ricerca se esistono, altrimenti onboarding
        if (currentState.lastResults && currentState.lastResults.length) {
            renderResults(currentState.lastResults);
        } else {
            renderDiscoveryOnboarding();
        }
    }

    function renderDiscoveryOnboarding() {
        elements.discoveryResults.innerHTML = `
            <div class="discovery-onboarding" style="grid-column: span 12;">
                <div style="text-align:center; margin-bottom: 40px; padding: 40px; background: rgba(var(--accent-rgb), 0.03); border-radius: 24px; border: 1px dashed var(--border);">
                    <div class="avatar" style="width: 80px; height: 80px; font-size: 40px; margin: 0 auto 20px; background: var(--accent-gradient); border: 4px solid rgba(255,255,255,0.1);">⚓</div>
                    <h2 style="font-family: 'Outfit'; font-size: 24px; margin-bottom: 10px;">Inizia la tua Discovery</h2>
                    <p style="color: var(--text-muted); max-width: 500px; margin: 0 auto;">Non hai ancora cercato partner. Avvia una ricerca automatica per trovare hotel, agenzie e concierge nella tua zona.</p>
                    <button class="btn btn-primary" style="margin-top: 25px; padding: 12px 24px;" onclick="document.getElementById('scout-btn').click()">
                        <i class="fa-solid fa-bolt"></i> Avvia Prima Ricerca
                    </button>
                </div>
            </div>`;
    }

    // Esponiamo le funzioni al window per permettere l'uso in onclick negli HTML dinamici
    window.executeSearch = async function(query) {
        if (!requireActiveClient()) return;
        if (!query) return;
        
        // Se non siamo in discovery, cambiamoci
        if (currentState.currentView !== 'discovery') {
            switchView('discovery');
            elements.navItems.forEach(n => {
                n.classList.remove('active');
                if (n.dataset.view === 'discovery') {
                    n.classList.add('active');
                    moveIndicator(n);
                }
            });
        }

        elements.searchInput.value = query;
        elements.discoveryResults.innerHTML = `
            <div class="loader-container">
                <div class="loader"></div>
                <p style="margin-top: 20px; font-weight: 500;">Leslie sta scansionando il mercato per: <span style="color: var(--accent);">${query}</span></p>
                <p style="font-size: 12px; color: var(--text-muted);">Questo potrebbe richiedere qualche secondo per l'analisi AI dei risultati.</p>
            </div>`;

        const searchBtn = elements.searchBtn;
        setBusy(searchBtn, true);
        try {
            const results = await apiFetch(`/api/search?q=${encodeURIComponent(query)}`, {}, false);
            renderResults(results);
        } catch (e) {
            elements.discoveryResults.innerHTML = `
                <div style="text-align:center; padding: 40px;">
                    <i class="fa-solid fa-triangle-exclamation" style="font-size: 32px; color: #f87171; margin-bottom: 16px;"></i>
                    <p style="color: var(--text-main); font-weight: 600;">Errore nella ricerca</p>
                    <p style="color: var(--text-muted); font-size: 13px;">Verifica la tua connessione o le impostazioni dell'API Key.</p>
                    <button class="btn btn-ghost" onclick="executeSearch('${query}')" style="margin-top: 15px;">Riprova</button>
                </div>`;
        } finally {
            setBusy(searchBtn, false);
        }
    };

    function renderResults(results) {
        currentState.lastResults = results;
        if (!results || !results.length) {
            elements.discoveryResults.innerHTML = '<p style="color:var(--text-muted); text-align:center; padding: 40px;">Nessun risultato trovato per questa ricerca.</p>';
            return;
        }

        elements.discoveryResults.style.display = 'grid';
        elements.discoveryResults.style.gridTemplateColumns = 'repeat(auto-fill, minmax(350px, 1fr))';
        elements.discoveryResults.style.gap = '24px';

        elements.discoveryResults.innerHTML = results.map((r, index) => {
            const previewUrl = `https://api.microlink.io/?url=${encodeURIComponent(r.website)}&screenshot=true&embed=screenshot.url`;
            const isKnown = r.is_known;

            return `
                <div class="card" style="padding: 0; overflow: hidden; display: flex; flex-direction: column;">
                    <div style="height: 160px; background: #000; position: relative;">
                        <img src="${escapeHTML(previewUrl)}" alt="Site Preview" style="width: 100%; height: 100%; object-fit: cover; opacity: 0.8;">
                        <div style="position: absolute; top: 12px; right: 12px; background: ${isKnown ? '#64748b' : 'var(--accent)'}; color: ${isKnown ? '#fff' : 'var(--primary)'}; padding: 4px 10px; border-radius: 8px; font-weight: 800; font-size: 11px;">
                            ${isKnown ? 'GIA\' SALVATO' : 'NUOVO'}
                        </div>
                    </div>
                    <div style="padding: 20px;">
                        <h4 style="font-family: 'Outfit'; font-size: 17px; margin-bottom: 5px;">${escapeHTML(r.name)}</h4>
                        <div style="font-size: 12px; color: var(--accent); margin-bottom: 12px;">
                            <i class="fa-solid fa-link"></i> ${escapeHTML(safeHostname(r.website))}
                        </div>
                        <div style="font-size: 12px; color: var(--text-muted); margin-bottom: 20px; display: flex; flex-direction: column; gap: 5px;">
                            <span><i class="fa-solid fa-location-dot"></i> ${r.address ? escapeHTML(r.address.split(',')[0]) : 'Indirizzo N/D'}</span>
                            ${r.emails && r.emails.length > 0 ? `<span style="color: #4ade80;"><i class="fa-solid fa-envelope"></i> ${escapeHTML(r.emails[0])}</span>` : ''}
                        </div>
                        <div style="font-size:10px;color:var(--text-muted);margin-bottom:10px;"><i class="fa-solid fa-circle-info"></i> Verrà valutato rispetto a ${escapeHTML(currentState.currentClient.name)} dopo il salvataggio.</div>
                        <button class="btn btn-primary" style="width: 100%; padding: 10px; font-size: 13px;" onclick="saveLeadByIndex(event, ${index})">
                            <i class="fa-solid fa-plus"></i> Salva come opportunità
                        </button>
                    </div>
                </div>
            `;
        }).join('');
    }

    window.saveLeadByIndex = async (e, index) => {
        e.stopPropagation();
        const lead = currentState.lastResults[index];
        if (!lead) return;
        
        showToast('Salvataggio lead...');
        try {
            const resp = await fetch('/api/leads/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-User-ID': currentState.currentUser.id },
                body: JSON.stringify(lead)
            });
            if (resp.ok) {
                showToast('Lead salvato con successo!');
                loadIntelligence();
            } else {
                showToast('Errore durante il salvataggio.');
            }
        } catch (e) { showToast('Errore di connessione.'); }
    };

    // ─── INTELLIGENCE VIEW ───
    window.loadIntelligenceView = loadIntelligenceView;
    async function loadIntelligenceView() {
        try {
            const [histResp, intelResp, leadsResp] = await Promise.all([
                fetch('/api/queries/history', { headers: { 'X-User-ID': currentState.currentUser.id } }),
                fetch('/api/intelligence', { headers: { 'X-User-ID': currentState.currentUser.id } }),
                fetch('/api/leads', { headers: { 'X-User-ID': currentState.currentUser.id } })
            ]);
            const history = await histResp.json();
            const intelligence = await intelResp.json();
            const leads = await leadsResp.json();

            // Update Stats Header (dati reali)
            const premiumCount = leads.filter(l => l.is_premium).length;
            document.getElementById('intel-stats-leads').textContent = leads.length;
            document.getElementById('intel-stats-niches').textContent = intelligence.length;
            document.getElementById('intel-stats-queries').textContent = history.length;
            document.getElementById('intel-stats-premium').textContent = premiumCount;

            // Nicchie apprese via AI
            const nicheEl = document.getElementById('intelligence-niches');
            if (intelligence.length) {
                nicheEl.innerHTML = intelligence.map(intel => {
                    const qList = JSON.parse(intel.suggested_queries || '[]');
                    const saturation = intel.market_saturation || Math.floor(Math.random() * 30) + 10;
                    return `
                    <div class="card" style="padding:24px; background: rgba(255,255,255,0.02); border: 1px solid var(--border); border-radius:16px; transition: transform 0.2s; position: relative; overflow: hidden;">
                        <div style="position: absolute; top: 0; left: 0; width: 4px; height: 100%; background: var(--accent);"></div>
                        
                        <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:16px;">
                            <div>
                                <div style="font-weight:700; color:var(--accent); font-size:18px; font-family:'Outfit'; margin-bottom: 4px;">🎯 ${escapeHTML(intel.niche_key)}</div>
                                <div style="font-size:13px; color: var(--text-main); font-style: italic; opacity: 0.8;">"${escapeHTML(intel.description) || 'Nessuna descrizione disponibile.'}"</div>
                            </div>
                            <div style="text-align: right;">
                                <div style="font-size:11px; color: var(--text-muted); text-transform: uppercase;">Confidenza AI</div>
                                <div style="font-weight: 700; color: var(--accent);">${'★'.repeat(Math.min(5, intel.confidence))}</div>
                            </div>
                        </div>

                        <!-- Saturation Bar -->
                        <div style="margin-bottom: 20px;">
                            <div style="display:flex; justify-content:space-between; font-size:11px; margin-bottom:6px; color: var(--text-muted);">
                                <span>Saturazione Mercato</span>
                                <span>${saturation}%</span>
                            </div>
                            <div style="width:100%; height:4px; background: rgba(255,255,255,0.05); border-radius:2px;">
                                <div style="width:${saturation}%; height:100%; background: ${saturation > 70 ? '#f87171' : 'var(--accent)'}; border-radius:2px;"></div>
                            </div>
                        </div>

                        <div style="display:flex; flex-wrap:wrap; gap:8px; margin-bottom: 20px;">
                            ${qList.map(q => `
                                <div class="niche-chip" style="font-size:11px; padding:6px 12px; background: rgba(var(--accent-rgb), 0.1); border: 1px solid rgba(var(--accent-rgb), 0.2);" data-query="${escapeHTML(q)}" onclick="executeSearch(this.dataset.query)">
                                    <i class="fa-solid fa-magnifying-glass"></i> ${escapeHTML(q)}
                                </div>
                            `).join('')}
                        </div>

                        <div style="border-top: 1px solid var(--border); padding-top: 16px;">
                            <button class="btn btn-primary" style="width: 100%; padding: 10px; justify-content: center;" data-niche="${escapeHTML(intel.niche_key)}" data-queries="${escapeHTML(intel.suggested_queries)}" onclick="deepDiveNiche(this.dataset.niche, this.dataset.queries)">
                                <i class="fa-solid fa-bolt"></i> Approfondisci Nicchia
                            </button>
                        </div>
                    </div>`;
                }).join('');
            } else {
                nicheEl.innerHTML = '<p style="color:var(--text-muted); text-align:center; padding:40px;">Nessuna nicchia ancora appresa. Esegui qualche Audit AI sui lead per popolare la Intelligence.</p>';
            }

            // Cronologia ricerche (cliccabile)
            const histEl = document.getElementById('search-history-list');
            if (histEl) {
                if (history.length) {
                    histEl.innerHTML = history.map(h => `
                        <div class="lead-row" style="padding:16px; margin:0; border-bottom:1px solid var(--border); cursor:pointer; border-radius:0; background: ${h.is_exhausted ? 'transparent' : 'rgba(var(--accent-rgb), 0.03)'}" data-query="${escapeHTML(h.query)}" onclick="executeSearch(this.dataset.query)">
                            <div style="display:flex; flex-direction:column; gap:4px; flex: 1;">
                                <div style="display: flex; align-items: center; gap: 8px;">
                                    <span style="color:var(--text-main); font-weight:600; font-size: 14px;">${escapeHTML(h.query)}</span>
                                    ${h.is_exhausted ? '<span class="badge" style="background: #333; font-size: 9px;">ESAURITA</span>' : ''}
                                </div>
                                <span style="font-size:11px; color: var(--text-muted);">${new Date(h.last_run).toLocaleString()}</span>
                            </div>
                            <div style="text-align: right; min-width: 100px;">
                                <div style="color:${h.is_exhausted ? 'var(--text-muted)' : 'var(--accent)'}; font-weight:700; font-size:13px;">
                                    ${h.is_exhausted ? 'Completata' : `+${h.new_count} nuovi`}
                                </div>
                                <div style="font-size: 10px; color: var(--text-muted);">${h.results_count} totali</div>
                            </div>
                            <div style="margin-left: 15px;">
                                <i class="fa-solid fa-chevron-right" style="font-size: 12px; color: var(--border);"></i>
                            </div>
                        </div>`).join('');
                } else {
                    histEl.innerHTML = '<div style="color:var(--text-muted); text-align:center; padding:40px;">Nessuna ricerca effettuata.</div>';
                }
            }

            loadMarketPulse();
        } catch (e) {
            console.error('Intelligence View Error:', e);
        }
    }

    async function loadMarketPulse() {
        const chartEl = document.getElementById('market-pulse-chart');
        const labelEl = document.getElementById('pulse-total-label');
        if (!chartEl) return;

        try {
            const resp = await fetch('/api/analytics/pulse', { headers: { 'X-User-ID': currentState.currentUser.id } });
            const data = await resp.json();
            
            const total = data.reduce((a, b) => a + b.count, 0);
            labelEl.textContent = `+${total} Lead Premium`;
            
            if (!data.length) {
                chartEl.innerHTML = '<div style="color:var(--text-muted); font-size:12px; padding:20px;">Nessun dato negli ultimi 14 giorni.</div>';
                return;
            }

            const maxCount = Math.max(...data.map(d => d.count), 1);
            chartEl.innerHTML = data.map(d => {
                const height = (d.count / maxCount) * 100;
                const date = new Date(d.day).toLocaleDateString('it-IT', { day: 'numeric', month: 'short' });
                return `
                    <div style="flex: 1; display: flex; flex-direction: column; align-items: center; gap: 8px; group: relative;">
                        <div style="width: 100%; height: ${height}%; background: var(--accent); border-radius: 4px; min-height: 2px; transition: height 0.5s ease-out; opacity: 0.8; cursor: pointer;" onmouseover="this.style.opacity='1'" onmouseout="this.style.opacity='0.8'" title="${d.count} lead il ${date}"></div>
                        <span style="font-size: 9px; color: var(--text-muted); transform: rotate(-45deg); margin-top: 5px; white-space: nowrap;">${date}</span>
                    </div>
                `;
            }).join('');

        } catch (e) { console.error('Pulse Error:', e); }
    }

    window.deepDiveNiche = async (niche, queriesJson) => {
        let queries;
        try {
            queries = JSON.parse(queriesJson);
        } catch (e) {
            console.error("Failed to parse queries:", queriesJson);
            showToast("Errore nel caricamento delle query della nicchia.");
            return;
        }
        
        showToast(`Avvio Deep Dive per la nicchia: ${niche}`);
        
        // Switch view
        switchView('discovery');
        
        // Use the streaming console even for deep dive
        const discoveryResults = document.getElementById('discovery-results');
        discoveryResults.innerHTML = `
            <div id="deep-dive-console" class="card" style="background: #000; border: 1px solid var(--accent); padding: 30px; animation: slideIn 0.4s ease-out;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; border-bottom: 1px solid #222; padding-bottom: 15px;">
                    <div style="display: flex; align-items: center; gap: 12px;">
                        <i class="fa-solid fa-microscope" style="color: var(--accent); font-size: 20px;"></i>
                        <span style="font-family: 'Outfit'; color: #fff; font-size: 16px; font-weight: 700; letter-spacing: 1px;">LESLIE DEEP DIVE: ${niche.toUpperCase()}</span>
                    </div>
                    <div class="animate-pulse" style="width: 10px; height: 10px; background: var(--accent); border-radius: 50%; box-shadow: 0 0 10px var(--accent);"></div>
                </div>
                <div id="dive-output" style="font-family: 'Courier New', monospace; font-size: 13px; color: #4ade80; min-height: 120px; max-height: 350px; overflow-y: auto; line-height: 1.8; background: rgba(0,0,0,0.3); padding: 15px; border-radius: 8px;">
                    <span style="color: #888;">[${new Date().toLocaleTimeString()}]</span> > Inizializzazione protocollo immersione...<br>
                    <span style="color: #888;">[${new Date().toLocaleTimeString()}]</span> > Calibrazione sensori per nicchia: <span style="color: var(--accent);">${niche}</span>...<br>
                </div>
                <div id="dive-results" style="margin-top: 25px; display: grid; grid-template-columns: 1fr; gap: 15px;"></div>
                
                <div style="margin-top: 20px; display: flex; justify-content: flex-end;">
                    <button class="btn btn-ghost" onclick="loadDiscoveryHome()" style="font-size: 12px;">
                        <i class="fa-solid fa-xmark"></i> Interrompi Analisi
                    </button>
                </div>
            </div>
        `;

        const outputEl = document.getElementById('dive-output');
        const scrollOutput = () => outputEl.scrollTop = outputEl.scrollHeight;

        try {
            outputEl.innerHTML += `<span style="color: #888;">[${new Date().toLocaleTimeString()}]</span> > Analisi in corso per ${queries.length} query strategiche generate dall'AI...<br>`;
            scrollOutput();
            
            const resp = await fetch('/api/search/batch', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-User-ID': currentState.currentUser.id },
                body: JSON.stringify({ queries })
            });
            const results = await resp.json();
            
            outputEl.innerHTML += `<span style="color: #888;">[${new Date().toLocaleTimeString()}]</span> > Batch completato con successo.<br>`;
            outputEl.innerHTML += `<span style="color: #888;">[${new Date().toLocaleTimeString()}]</span> > <span style="color: var(--accent); font-weight: 800;">Rilevati ${results.length} potenziali partner premium.</span><br>`;
            outputEl.innerHTML += `<span style="color: #888;">[${new Date().toLocaleTimeString()}]</span> > Sincronizzazione Discovery Wall in corso...<br>`;
            scrollOutput();
            
            setTimeout(() => {
                renderResults(results);
                showToast(`Deep Dive completato! Trovati ${results.length} risultati.`);
            }, 1500);
            
        } catch (e) {
            console.error("Deep Dive Error:", e);
            showToast('Errore durante il Deep Dive.');
            loadDiscoveryHome();
        }
    };

    // ─── LEADS MANAGEMENT ───
    async function loadLeads() {
        elements.savedLeadsList.innerHTML = '<div class="loader"></div>';
        try {
            const resp = await fetch('/api/leads', {
                headers: { 'X-User-ID': currentState.currentUser.id }
            });
            const leads = await resp.json();
            
            if (!leads.length) {
                elements.savedLeadsList.style.display = 'block';
                elements.savedLeadsList.innerHTML = `
                    <div style="text-align:center; padding: 60px 20px; background: rgba(var(--accent-rgb), 0.03); border: 1px dashed var(--border); border-radius: 24px;">
                        <i class="fa-solid fa-crown" style="font-size: 40px; color: var(--accent); margin-bottom: 20px; opacity: 0.4;"></i>
                        <h3 class="font-display" style="font-size: 22px; margin-bottom: 10px;">Nessuna opportunità ancora</h3>
                        <p class="text-muted-md" style="max-width: 460px; margin: 0 auto 24px;">Avvia una ricerca o salva un risultato per popolare la tua lista contatti.</p>
                        <button class="btn btn-primary" onclick="switchView('discovery')" style="margin-right: 12px;">
                            <i class="fa-solid fa-compass"></i> Cerca partner
                        </button>
                        <button class="btn btn-ghost" onclick="document.getElementById('scout-btn').click()">
                            <i class="fa-solid fa-bolt"></i> Avvia Ricerca
                        </button>
                    </div>`;
                return;
            }

            // Status summary
            const summaryEl = document.getElementById('leads-status-summary');
            if (summaryEl) {
                summaryEl.innerHTML = `
                    <span class="status-pill" style="border-color:var(--accent); color:var(--accent);">Totale: <strong>${leads.length}</strong></span>
                `;
            }

            elements.savedLeadsList.style.display = 'grid';
            elements.savedLeadsList.style.gridTemplateColumns = 'repeat(auto-fill, minmax(350px, 1fr))';
            elements.savedLeadsList.style.gap = '24px';

            const now = Date.now();
            const followupThresholdMs = 7 * 24 * 60 * 60 * 1000;
            elements.savedLeadsList.innerHTML = leads.map(l => {
                const seo = JSON.parse(l.seo_data || '{}');
                const previewUrl = (typeof seo.preview_url === 'string' && seo.preview_url) ? seo.preview_url
                    : (l.website ? `https://api.microlink.io/?url=${encodeURIComponent(l.website)}&screenshot=true&embed=screenshot.url` : '');
                const emails = (l.email || '').split(',').map(e => e.trim()).filter(Boolean);
                const firstEmail = emails[0] || '';
                const extraEmails = emails.length > 1 ? emails.length - 1 : 0;
                const isFollowup = l.status === 'contacted' && l.contacted_at && !l.replied_at &&
                    (now - new Date(l.contacted_at).getTime()) > followupThresholdMs;
                const statusLabel = STATUS_LABELS[l.status] || l.status;

                return `
                <div class="card lead-card" data-lead-id="${l.id}" data-status="${l.status}" data-name="${escapeHTML(l.company_name)}" data-premium="${l.is_premium ? '1' : '0'}" data-followup="${isFollowup ? '1' : '0'}" style="padding: 0; overflow: hidden; display: flex; flex-direction: column; height: 100%; transition: opacity 0.3s; cursor:pointer;" onclick="openLeadDetail(${l.id})">
                    <div style="height: 160px; overflow: hidden; position: relative; background: #000;">
                        ${previewUrl ? `<img src="${escapeHTML(previewUrl)}" alt="Preview" loading="lazy" style="width: 100%; height: 100%; object-fit: cover; opacity: 0.7;">` : ''}
                        <div style="position:absolute; top:12px; right:12px; display:flex; gap:6px; flex-direction:column; align-items:flex-end;">
                            ${l.is_premium ? `<span class="badge-gold" style="box-shadow: 0 0 15px rgba(197,160,89,0.4); font-size: 10px;">PREMIUM</span>` : ''}
                            <span class="status-pill-lead ${l.status}">${statusLabel}</span>
                            ${isFollowup ? `<span class="followup-badge"><i class="fa-solid fa-bell"></i> Follow-up</span>` : ''}
                            ${l.is_generic_email ? `<span class="generic-email-warn" title="Email generica (info@…) — probabilmente non è un decisore">info@</span>` : ''}
                        </div>
                        <div style="position:absolute; bottom:0; left:0; right:0; background:linear-gradient(transparent, rgba(0,0,0,0.9)); padding:15px;">
                            <h3 style="font-family:'Outfit'; font-size:18px; margin:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${escapeHTML(l.company_name)}</h3>
                            <a href="${escapeHTML(l.website)}" target="_blank" onclick="event.stopPropagation()" style="color:var(--accent); text-decoration:none; font-size:12px;">
                                <i class="fa-solid fa-link"></i> ${escapeHTML(safeHostname(l.website))}
                            </a>
                        </div>
                    </div>

                    <div style="padding:18px; flex:1; display:flex; flex-direction:column; gap:12px;">

                        ${l.fit_score !== null && l.fit_score !== undefined ? `
                        <div class="lead-fit-summary">
                            <span class="lead-fit-score">${l.fit_score}<small>/10</small></span>
                            <div><small>Compatibilità con ${escapeHTML(currentState.currentClient.name)}</small><strong>${escapeHTML(l.fit_reason || 'Valutazione completata')}</strong>${l.fit_confidence ? `<em>Affidabilità ${escapeHTML(l.fit_confidence)}</em>` : ''}</div>
                        </div>` : `
                        <div class="lead-fit-summary pending"><span><i class="fa-solid fa-wand-magic-sparkles"></i></span><div><small>Compatibilità</small><strong>Da valutare con Audit AI</strong></div></div>`}

                        ${l.selling_angle ? `
                        <div style="background:rgba(var(--accent-rgb),0.06); border:1px solid rgba(var(--accent-rgb),0.15); border-radius:10px; padding:10px 12px;">
                            <div style="font-size:10px; color:var(--accent); text-transform:uppercase; font-weight:800; margin-bottom:3px;"><i class="fa-solid fa-anchor"></i> Perché Contattarli</div>
                            <div style="font-size:12px; color:var(--text-main); font-style:italic; line-height:1.4;">"${escapeHTML(l.selling_angle)}"</div>
                        </div>` : ''}

                        ${firstEmail || l.phone ? `
                        <div style="display:flex; flex-wrap:wrap; gap:8px; font-size:12px;">
                            ${firstEmail ? `<span onclick="event.stopPropagation(); copyToClipboard('${escapeHTML(firstEmail)}')" class="contact-chip" title="Clicca per copiare" style="cursor:pointer; display:inline-flex; align-items:center; gap:5px; padding:5px 10px; background:rgba(74,222,128,0.08); border:1px solid rgba(74,222,128,0.2); border-radius:8px; color:#4ade80;">
                                <i class="fa-solid fa-envelope"></i> ${escapeHTML(firstEmail)}${extraEmails ? ` <span style="opacity:0.6">+${extraEmails}</span>` : ''}
                                <i class="fa-solid fa-copy" style="opacity:0.5; font-size:10px;"></i>
                            </span>` : ''}
                            ${l.phone ? `<a href="tel:${escapeHTML(l.phone)}" onclick="event.stopPropagation()" style="display:inline-flex; align-items:center; gap:5px; padding:5px 10px; background:rgba(96,165,250,0.08); border:1px solid rgba(96,165,250,0.2); border-radius:8px; color:#60a5fa; text-decoration:none;">
                                <i class="fa-solid fa-phone"></i> ${escapeHTML(l.phone)}
                            </a>` : ''}
                        </div>` : ''}

                        ${l.ai_audit ? `
                        <div class="lead-notes-preview" style="font-size:12px; color:var(--text-muted); line-height:1.5; background:rgba(0,0,0,0.15); border-radius:8px; padding:8px 10px;">
                            <span style="font-size:10px; color:var(--accent); font-weight:700;"><i class="fa-solid fa-robot"></i> Analisi AI</span><br>
                            ${escapeHTML(l.ai_audit.length > 150 ? l.ai_audit.slice(0, 150) + '...' : l.ai_audit)}
                        </div>` : ''}

                        <div style="margin-top:auto; display:flex; gap:8px; align-items:center;" onclick="event.stopPropagation()">
                            <button class="btn btn-primary" style="padding:10px; flex:1;" onclick="triggerAudit(${l.id}, this)" title="Audit AI">
                                <i class="fa-solid fa-wand-magic-sparkles"></i> Audit AI
                            </button>
                            <button class="btn btn-ghost" style="padding:10px;" onclick="deleteLead(${l.id})">
                                <i class="fa-solid fa-trash" style="color:#f87171;"></i>
                            </button>
                        </div>
                    </div>
                </div>`;
            }).join('');
        } catch (e) { console.error('Leads Error:', e); }
    }

    window.triggerAudit = async (leadId, btn) => {
        const oldContent = btn.innerHTML;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Audit in corso...';
        btn.disabled = true;
        
        try {
            const resp = await fetch(`/api/leads/audit/${leadId}`, {
                method: 'POST',
                headers: { 'X-User-ID': currentState.currentUser.id }
            });
            const data = await resp.json();
            if (data.audit) {
                showToast('Audit completato con successo!');
                loadLeads();
            }
        } catch (e) {
            showToast('Errore Audit AI.');
            btn.innerHTML = oldContent;
            btn.disabled = false;
        }
    };

    window.updateStatus = async (lid, status) => {
        try {
            await fetch(`/api/leads/${lid}/status`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-User-ID': currentState.currentUser.id },
                body: JSON.stringify({ status })
            });
            showToast('Stato aggiornato.');
        } catch (e) { showToast('Errore aggiornamento stato.'); }
    };

    window.changeLeadStatus = async (lid, status, btn) => {
        try {
            await apiFetch(`/api/leads/${lid}/status`, {
                method: 'POST',
                body: JSON.stringify({ status })
            });
            showToast(`Stato → ${STATUS_LABELS[status] || status}`);
            // Riapri il modal così i timestamp aggiornati si vedono
            openLeadDetail(lid);
            loadLeads();
            updateFollowupsBadge();
        } catch {
            showToast('Errore aggiornamento stato.');
        }
    };

    window.generateDraftEmail = async (lid, force) => {
        const block = document.getElementById(`draft-email-body-${lid}`);
        if (block) block.innerHTML = '<div style="padding:24px; text-align:center;"><div class="loader"></div><div style="margin-top:10px; font-size:12px; color:var(--text-muted);">Sto generando l\'email…</div></div>';
        try {
            const r = await apiFetch(`/api/leads/${lid}/draft_email`, {
                method: 'POST',
                body: JSON.stringify({ force: !!force })
            });
            openLeadDetail(lid);
        } catch (e) {
            showToast('Errore generazione bozza.');
            openLeadDetail(lid);
        }
    };

    window.deleteLead = (lid) => {
        const card = document.querySelector(`.lead-card[data-lead-id="${lid}"]`);
        if (!card) return;
        // Rimozione ottimistica
        const prevOpacity = card.style.opacity;
        const prevPointerEvents = card.style.pointerEvents;
        card.style.opacity = '0.3';
        card.style.pointerEvents = 'none';

        const commit = async () => {
            try {
                await apiFetch(`/api/leads/${lid}`, { method: 'DELETE' }, 'Errore eliminazione lead.');
                card.remove();
            } catch {
                // Ripristina se la DELETE fallisce
                card.style.opacity = prevOpacity;
                card.style.pointerEvents = prevPointerEvents;
            }
        };
        const undo = () => {
            card.style.opacity = prevOpacity;
            card.style.pointerEvents = prevPointerEvents;
            showToast('Eliminazione annullata.');
        };
        showUndoToast('Lead eliminato.', commit, undo, 5000);
    };

    // ─── CLIPBOARD & CONTACT HELPERS ───
    window.copyToClipboard = async (text) => {
        try {
            await navigator.clipboard.writeText(text);
            showToast('Copiato negli appunti!');
        } catch {
            const ta = document.createElement('textarea');
            ta.value = text;
            ta.style.position = 'fixed';
            ta.style.opacity = '0';
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            document.body.removeChild(ta);
            showToast('Copiato negli appunti!');
        }
    };

    // ─── INLINE NOTES EDITING ───
    window.editNotes = (lid, currentNotes) => {
        const card = document.querySelector(`.lead-card[data-lead-id="${lid}"]`);
        const notesArea = card?.querySelector('.lead-notes-preview');
        if (!notesArea) return;
        notesArea.innerHTML = `
            <textarea id="notes-edit-${lid}" style="width:100%; min-height:80px; background:rgba(0,0,0,0.3); border:1px solid var(--border); border-radius:8px; color:var(--text-main); padding:10px; font-family:inherit; font-size:12px; resize:vertical;">${escapeHTML(currentNotes || '')}</textarea>
            <div style="display:flex; gap:8px; margin-top:8px;">
                <button class="btn btn-primary" style="padding:6px 14px; font-size:11px;" onclick="event.stopPropagation(); saveNotes(${lid})"><i class="fa-solid fa-save"></i> Salva</button>
                <button class="btn btn-ghost" style="padding:6px 14px; font-size:11px;" onclick="event.stopPropagation(); loadLeads()">Annulla</button>
            </div>`;
    };

    window.saveNotes = async (lid) => {
        const textarea = document.getElementById(`notes-edit-${lid}`);
        if (!textarea) return;
        try {
            await apiFetch(`/api/leads/${lid}/notes`, {
                method: 'POST',
                body: JSON.stringify({ notes: textarea.value })
            });
            showToast('Note aggiornate!');
            loadLeads();
        } catch { showToast('Errore salvataggio note.'); }
    };

    // ─── LEADS FILTER ───
    window.filterLeads = () => {
        const q = (document.getElementById('leads-search')?.value || '').toLowerCase();
        const premiumOnly = document.getElementById('leads-premium-toggle')?.checked || false;
        const followupsOnly = document.getElementById('leads-followups-toggle')?.checked || false;
        const statusFilter = document.getElementById('leads-status-filter')?.value || '';
        document.querySelectorAll('.lead-card').forEach(c => {
            const name = (c.dataset.name || '').toLowerCase();
            const isPremium = c.dataset.premium === '1';
            const isFollowup = c.dataset.followup === '1';
            const status = c.dataset.status || 'saved';
            const matchesSearch = !q || name.includes(q);
            const matchesPremium = !premiumOnly || isPremium;
            const matchesFollowup = !followupsOnly || isFollowup;
            const matchesStatus = !statusFilter || status === statusFilter;
            c.style.display = (matchesSearch && matchesPremium && matchesFollowup && matchesStatus) ? '' : 'none';
        });
    };

    // ─── STRATEGY & WEIGHTS ───
    async function loadSettings() {
        try {
            const [weightsResp, scoutResp] = await Promise.all([
                fetch('/api/settings/weights'),
                fetch('/api/settings/scout', { headers: { 'X-User-ID': currentState.currentUser.id } })
            ]);
            currentState.weights = await weightsResp.json();
            currentState.scoutParams = await scoutResp.json();
        } catch (e) { console.error('Settings Error:', e); }
    }

    // ─── SETTINGS TAB SYSTEM ───
    let activeSettingsTab = 'sectors';
    let _queryStatsCache = null;

    function renderSettings() {
        const container = document.getElementById('settings-panel-content');
        if (!container) return;
        document.querySelectorAll('.settings-tab').forEach(t =>
            t.classList.toggle('active', t.dataset.tab === activeSettingsTab));
        const renderers = { sectors: renderSectorsTab, queries: renderQueriesTab, params: renderParamsTab, costs: renderCostsTab };
        if (renderers[activeSettingsTab]) renderers[activeSettingsTab](container);

        // Sync sliders with saved params
        const p = currentState.scoutParams || {};
        const maxSlider = document.getElementById('max-results-slider');
        const minSlider = document.getElementById('min-score-slider');
        if (maxSlider && p.max_results) {
            maxSlider.value = p.max_results;
            document.getElementById('max-results-label').textContent = p.max_results;
        }
        if (minSlider && p.min_score !== undefined) {
            minSlider.value = p.min_score;
            document.getElementById('min-score-label').textContent = p.min_score;
        }
    }

    // Tab click delegation
    document.addEventListener('click', (e) => {
        const tab = e.target.closest('.settings-tab');
        if (tab && tab.dataset.tab) {
            activeSettingsTab = tab.dataset.tab;
            renderSettings();
        }
    });

    // ─── TAB: SETTORI ───
    async function renderSectorsTab(container) {
        const total = Object.values(currentState.weights).reduce((a, b) => a + b, 0) || 1;

        // Fetch query stats per category (cached)
        if (!_queryStatsCache) {
            try {
                _queryStatsCache = await apiFetch('/api/queries/stats', {}, false);
            } catch { _queryStatsCache = {}; }
        }
        const stats = _queryStatsCache;

        container.innerHTML = `
        <div class="settings-section">
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 30px; align-items: start;">
                <div style="display: flex; flex-direction: column; gap: 20px;">
                    ${Object.entries(currentState.weights).map(([cat, val]) => {
                        const s = stats[cat] || {};
                        return `
                        <div class="stat-item" style="gap: 10px;">
                            <div style="display: flex; justify-content: space-between; align-items: center;">
                                <span class="stat-label" style="font-size: 13px;">${cat}</span>
                                <span style="color: var(--accent); font-weight: 700;">${val}%</span>
                            </div>
                            <input type="range" min="0" max="100" value="${val}" style="accent-color: var(--accent); height: 4px; width:100%;" oninput="updateWeight('${cat.replace(/'/g, "\\'")}', this.value)">
                            ${s.total ? `<div style="font-size:11px; color:var(--text-muted); margin-top:2px;">
                                ${s.available} ricerche disponibili · ${s.used + s.exhausted} completate
                            </div>` : ''}
                        </div>`;
                    }).join('')}
                </div>

                <div class="card" style="background: rgba(var(--accent-rgb), 0.02); border: 1px dashed var(--border); padding: 30px;">
                    <h3 style="font-family: 'Outfit'; font-size: 16px; margin-bottom: 20px;">Proiezione Strategica</h3>
                    <div style="display: flex; flex-direction: column; gap: 15px;">
                        ${Object.entries(currentState.weights).filter(([_, v]) => v > 0).sort((a,b) => b[1]-a[1]).slice(0, 3).map(([cat, val]) => `
                            <div>
                                <div style="display: flex; justify-content: space-between; font-size: 12px; margin-bottom: 5px;">
                                    <span style="color: var(--text-muted);">${cat.split('(')[0]}</span>
                                    <span style="color: var(--accent); font-weight: bold;">${Math.round((val/total)*100)}% del budget ricerca</span>
                                </div>
                                <div style="width: 100%; height: 4px; background: rgba(255,255,255,0.05); border-radius: 2px;">
                                    <div style="width: ${(val/total)*100}%; height: 100%; background: var(--accent); border-radius: 2px;"></div>
                                </div>
                            </div>
                        `).join('')}
                    </div>
                    <div style="margin-top: 25px; padding: 15px; background: rgba(0,0,0,0.2); border-radius: 12px; font-size: 12px; color: var(--text-muted);">
                        <i class="fa-solid fa-circle-info" style="color: var(--accent);"></i>
                        I settori con peso maggiore verranno cercati per primi. Imposta a 0 per disattivare un settore.
                    </div>
                </div>
            </div>
        </div>`;
    }

    window.updateWeight = async (cat, val) => {
        currentState.weights[cat] = parseInt(val);
        _queryStatsCache = null;
        renderSettings();
        await fetch('/api/settings/weights', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(currentState.weights)
        });
    };

    // ─── TAB: RICERCHE ───
    async function renderQueriesTab(container) {
        container.innerHTML = '<div style="padding:40px; text-align:center;"><div class="loader"></div></div>';
        let data;
        try {
            data = await apiFetch('/api/queries/pool', {}, false);
        } catch {
            container.innerHTML = '<div style="color:var(--text-muted);">Errore caricamento query pool.</div>';
            return;
        }

        const pool = data.pool;
        const stats = data.stats;
        const badgeLabels = { new: 'Nuova', used: 'Usata', exhausted: 'Esaurita' };

        container.innerHTML = `
        <div class="settings-section">
            <div style="display:flex; gap:16px; flex-wrap:wrap; margin-bottom:24px;">
                <span class="status-pill" style="border-color:var(--accent); color:var(--accent);">
                    Totale: <strong>${stats.total}</strong>
                </span>
                <span class="status-pill" style="border-color:#4ade80; color:#4ade80;">
                    Disponibili: <strong>${stats.available}</strong>
                </span>
                <span class="status-pill" style="border-color:var(--text-muted); color:var(--text-muted);">
                    Completate: <strong>${stats.used}</strong>
                </span>
                <span class="status-pill" style="border-color:#f87171; color:#f87171;">
                    Esaurite: <strong>${stats.exhausted}</strong>
                </span>
            </div>

            ${Object.entries(pool).map(([cat, queries]) => {
                const newCount = queries.filter(q => q.status === 'new').length;
                const catId = cat.replace(/[^a-zA-Z0-9]/g, '_');
                return `
                <div class="query-category">
                    <div class="query-category-header" onclick="document.getElementById('qcat_${catId}').style.display = document.getElementById('qcat_${catId}').style.display === 'none' ? 'flex' : 'none'">
                        <span>${cat}</span>
                        <span style="font-size:12px; font-weight:400; color:var(--text-muted);">
                            ${queries.length} query · ${newCount} nuove
                            <i class="fa-solid fa-chevron-down" style="margin-left:8px; font-size:10px;"></i>
                        </span>
                    </div>
                    <div class="query-category-body" id="qcat_${catId}" style="display:none;">
                        ${queries.map(q => `
                            <div class="query-item">
                                <span class="query-badge ${q.status}">${badgeLabels[q.status]}</span>
                                <span style="flex:1; color:var(--text-main);">${escapeHTML(q.query)}</span>
                                ${q.custom ? `<button class="btn btn-ghost" style="padding:2px 8px; font-size:10px; color:#f87171;" onclick="deleteCustomQuery('${escapeHTML(cat.replace(/'/g, "\\'"))}', '${escapeHTML(q.query.replace(/'/g, "\\'"))}')">
                                    <i class="fa-solid fa-xmark"></i>
                                </button>` : ''}
                            </div>
                        `).join('')}
                        <div class="add-query-row">
                            <input type="text" id="newq_${catId}" placeholder="Aggiungi una ricerca personalizzata..." onkeydown="if(event.key==='Enter')addCustomQuery('${escapeHTML(cat.replace(/'/g, "\\'"))}','newq_${catId}')">
                            <button class="btn btn-primary" style="padding:8px 16px; font-size:12px;" onclick="addCustomQuery('${escapeHTML(cat.replace(/'/g, "\\'"))}','newq_${catId}')">
                                <i class="fa-solid fa-plus"></i>
                            </button>
                        </div>
                    </div>
                </div>`;
            }).join('')}
        </div>`;
    }

    window.addCustomQuery = async (category, inputId) => {
        const input = document.getElementById(inputId);
        if (!input || !input.value.trim()) return;
        try {
            await apiFetch('/api/queries/custom', {
                method: 'POST',
                body: JSON.stringify({ category, query: input.value.trim() })
            });
            showToast('Ricerca aggiunta!');
            _queryStatsCache = null;
            renderQueriesTab(document.getElementById('settings-panel-content'));
        } catch { showToast('Errore aggiunta query.'); }
    };

    window.deleteCustomQuery = async (category, query) => {
        try {
            await apiFetch('/api/queries/custom', {
                method: 'DELETE',
                body: JSON.stringify({ category, query })
            });
            showToast('Ricerca rimossa.');
            _queryStatsCache = null;
            renderQueriesTab(document.getElementById('settings-panel-content'));
        } catch { showToast('Errore rimozione.'); }
    };

    // ─── TAB: COSTI ───
    async function renderCostsTab(container) {
        container.innerHTML = '<div style="padding:40px; text-align:center;"><div class="loader"></div></div>';
        let u;
        try {
            u = await apiFetch('/api/usage', {}, false);
        } catch {
            container.innerHTML = '<div style="color:var(--text-muted);">Errore caricamento dati consumo.</div>';
            return;
        }

        const totalCost = u.total_cost_usd || 0;

        container.innerHTML = `
        <div class="settings-section">
            <div class="cost-hero">
                <div class="amount">$${totalCost.toFixed(2)}</div>
                <div class="label">Costo stimato questo mese</div>
                <div style="display:flex; justify-content:center; gap:24px; margin-top:16px; font-size:13px;">
                    <span style="color:var(--text-muted);">Google Places: <strong style="color:var(--text-main);">$${u.places.estimated_cost_usd}</strong></span>
                    <span style="color:var(--text-muted);">DeepSeek AI: <strong style="color:var(--text-main);">$${u.deepseek.estimated_cost_usd}</strong></span>
                </div>
                ${u.total_searches ? `<div style="margin-top:12px; font-size:12px; color:var(--text-muted);">
                    ${u.total_searches} ricerche completate · costo medio per ricerca: <strong style="color:var(--accent);">$${u.avg_cost_per_search}</strong>
                </div>` : ''}
            </div>

            <div style="display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:24px;">
                ${renderUsageBar('Google Places', u.places.today, u.places.daily_limit, u.places.month, u.places.monthly_limit, u.places.breakdown)}
                ${renderUsageBar('DeepSeek AI', u.deepseek.today, u.deepseek.daily_limit, u.deepseek.month, u.deepseek.monthly_limit, null)}
            </div>

            <div style="padding:20px; background:rgba(var(--accent-rgb),0.05); border:1px solid var(--border); border-radius:16px;">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <div>
                        <h4 style="font-family:'Outfit'; color:var(--accent);">Backup Cloud</h4>
                        <p style="font-size:12px; color:var(--text-muted);">Salvataggio su Google Cloud Storage.</p>
                    </div>
                    <button class="btn btn-ghost" id="manual-sync-btn-costs" style="border-color:var(--accent); color:var(--accent);" onclick="document.getElementById('manual-sync-btn-costs').innerHTML='<i class=\\'fa-solid fa-spinner fa-spin\\'></i> Sync...'; fetch('/api/admin/sync',{method:'POST',headers:{'X-User-ID':'${currentState.currentUser.id}'}}).then(()=>{showToast('Sync completato!');document.getElementById('manual-sync-btn-costs').innerHTML='<i class=\\'fa-solid fa-cloud-arrow-up\\'></i> Sync';}).catch(()=>showToast('Errore sync.'))">
                        <i class="fa-solid fa-cloud-arrow-up"></i> Sync
                    </button>
                </div>
            </div>
        </div>`;
    }

    // ─── TAB: PARAMETRI ───
    async function renderParamsTab(container) {
        container.innerHTML = '<div style="padding:40px; text-align:center;"><div class="loader"></div></div>';
        let params;
        try {
            params = await apiFetch('/api/settings/scout', {}, false);
        } catch {
            container.innerHTML = '<div style="color:var(--text-muted);">Errore caricamento parametri.</div>';
            return;
        }

        function paramRow(icon, label, description, value, inputHtml, paramKey) {
            return `
            <div class="param-row" style="display:flex; align-items:center; gap:16px; padding:18px; background:rgba(0,0,0,0.15); border-radius:14px; border:1px solid var(--border);">
                <div style="width:36px; height:36px; background:rgba(var(--accent-rgb),0.1); border-radius:10px; display:flex; align-items:center; justify-content:center; flex-shrink:0;">
                    <i class="${icon}" style="color:var(--accent); font-size:14px;"></i>
                </div>
                <div style="flex:1; min-width:0;">
                    <div style="font-weight:600; font-size:13px; color:var(--text-main);">${label}</div>
                    <div style="font-size:11px; color:var(--text-muted); margin-top:2px;">${description}</div>
                </div>
                <div style="min-width:160px; text-align:right;">
                    ${inputHtml}
                </div>
            </div>`;
        }

        container.innerHTML = `
        <div class="settings-section" style="display:flex; flex-direction:column; gap:12px;">
            <div style="margin-bottom:8px;">
                <h3 style="font-family:'Outfit'; font-size:18px; margin-bottom:4px;">Parametri di Analisi</h3>
                <p style="font-size:12px; color:var(--text-muted);">Controlla ogni aspetto del processo di scouting. Le modifiche vengono salvate automaticamente.</p>
            </div>

            <div style="padding:12px 16px; background:rgba(var(--accent-rgb),0.06); border-radius:10px; border-left:3px solid var(--accent); margin-bottom:8px;">
                <div style="font-size:11px; color:var(--accent); font-weight:700; text-transform:uppercase; letter-spacing:1px; margin-bottom:4px;">Filtro Intelligence</div>
                <div style="font-size:12px; color:var(--text-muted);">Quando attivo, solo i lead con score >= soglia premium vengono evidenziati e ricevono audit AI approfondito. Con il filtro disattivato (soglia minima = 0), TUTTI i lead vengono salvati.</div>
            </div>

            ${paramRow('fa-solid fa-star', 'Soglia Premium', 'Score minimo per classificare un lead come Premium e lanciare audit AI approfondito',
                params.premium_threshold,
                `<div style="display:flex; align-items:center; gap:8px;">
                    <input type="range" min="1" max="10" value="${params.premium_threshold}" style="width:100px; accent-color:var(--accent);"
                        oninput="this.nextElementSibling.textContent=this.value+'/10'; saveParam('premium_threshold',parseInt(this.value))">
                    <span style="color:var(--accent); font-weight:700; font-size:14px; min-width:35px;">${params.premium_threshold}/10</span>
                </div>`, 'premium_threshold')}

            ${paramRow('fa-solid fa-list-ol', 'Max risultati Places', 'Quanti risultati Google Places analizzare per ogni query (impatta costo)',
                params.max_results,
                `<div style="display:flex; align-items:center; gap:8px;">
                    <input type="range" min="1" max="20" value="${params.max_results}" style="width:100px; accent-color:var(--accent);"
                        oninput="this.nextElementSibling.textContent=this.value; saveParam('max_results',parseInt(this.value))">
                    <span style="color:var(--accent); font-weight:700; font-size:14px; min-width:20px;">${params.max_results}</span>
                </div>`, 'max_results')}

            ${paramRow('fa-solid fa-filter', 'Soglia minima salvataggio', 'Score minimo per salvare un lead nel database (0 = salva tutti)',
                params.min_score,
                `<div style="display:flex; align-items:center; gap:8px;">
                    <input type="range" min="0" max="10" value="${params.min_score}" style="width:100px; accent-color:var(--accent);"
                        oninput="this.nextElementSibling.textContent=this.value+'/10'; saveParam('min_score',parseInt(this.value))">
                    <span style="color:var(--accent); font-weight:700; font-size:14px; min-width:35px;">${params.min_score}/10</span>
                </div>`, 'min_score')}

            ${paramRow('fa-solid fa-file-lines', 'Max testo scraper', 'Caratteri massimi estratti dal sito web',
                params.max_scrape_chars,
                `<div style="display:flex; align-items:center; gap:8px;">
                    <input type="number" value="${params.max_scrape_chars}" min="1000" max="20000" step="1000"
                        style="width:80px; background:rgba(0,0,0,0.3); border:1px solid var(--border); border-radius:8px; color:var(--text-main); padding:6px 8px; font-size:13px; text-align:right;"
                        onchange="saveParam('max_scrape_chars',parseInt(this.value))">
                    <span style="font-size:11px; color:var(--text-muted);">chars</span>
                </div>`, 'max_scrape_chars')}

            ${paramRow('fa-solid fa-robot', 'Max testo per AI', 'Caratteri massimi inviati a DeepSeek per analisi',
                params.max_ai_chars,
                `<div style="display:flex; align-items:center; gap:8px;">
                    <input type="number" value="${params.max_ai_chars}" min="500" max="10000" step="500"
                        style="width:80px; background:rgba(0,0,0,0.3); border:1px solid var(--border); border-radius:8px; color:var(--text-main); padding:6px 8px; font-size:13px; text-align:right;"
                        onchange="saveParam('max_ai_chars',parseInt(this.value))">
                    <span style="font-size:11px; color:var(--text-muted);">chars</span>
                </div>`, 'max_ai_chars')}

            ${paramRow('fa-solid fa-text-width', 'Min testo per analisi', 'Se il sito ha meno di questo testo, viene saltato (contenuto insufficiente)',
                params.min_text_for_analysis,
                `<div style="display:flex; align-items:center; gap:8px;">
                    <input type="number" value="${params.min_text_for_analysis}" min="50" max="2000" step="50"
                        style="width:80px; background:rgba(0,0,0,0.3); border:1px solid var(--border); border-radius:8px; color:var(--text-main); padding:6px 8px; font-size:13px; text-align:right;"
                        onchange="saveParam('min_text_for_analysis',parseInt(this.value))">
                    <span style="font-size:11px; color:var(--text-muted);">chars</span>
                </div>`, 'min_text_for_analysis')}

            <div style="margin-top:16px; padding:18px; background:rgba(0,0,0,0.15); border-radius:14px; border:1px solid var(--border);">
                <div style="display:flex; align-items:center; gap:10px; margin-bottom:12px;">
                    <i class="fa-solid fa-globe" style="color:var(--accent);"></i>
                    <div>
                        <div style="font-weight:600; font-size:13px;">Pagine extra scrappate</div>
                        <div style="font-size:11px; color:var(--text-muted);">Oltre alla homepage, lo scraper visita queste pagine per trovare email e contenuto</div>
                    </div>
                </div>
                <div style="display:flex; flex-wrap:wrap; gap:8px;">
                    ${(params.extra_pages || []).map(p => `
                        <span style="padding:5px 12px; background:rgba(var(--accent-rgb),0.1); border:1px solid rgba(var(--accent-rgb),0.2); border-radius:8px; font-size:12px; color:var(--text-main); font-family:monospace;">/${p}</span>
                    `).join('')}
                </div>
            </div>

            <div style="margin-top:8px; padding:18px; background:rgba(0,0,0,0.15); border-radius:14px; border:1px solid var(--border);">
                <div style="display:flex; align-items:center; gap:10px; margin-bottom:12px;">
                    <i class="fa-solid fa-ban" style="color:#f87171;"></i>
                    <div>
                        <div style="font-weight:600; font-size:13px;">Pattern email scartati</div>
                        <div style="font-size:11px; color:var(--text-muted);">Email contenenti questi pattern vengono ignorate automaticamente</div>
                    </div>
                </div>
                <div style="display:flex; flex-wrap:wrap; gap:6px;">
                    ${(params.junk_email_patterns || []).map(p => `
                        <span style="padding:3px 10px; background:rgba(248,113,113,0.08); border:1px solid rgba(248,113,113,0.2); border-radius:6px; font-size:11px; color:#f87171; font-family:monospace;">${escapeHTML(p)}</span>
                    `).join('')}
                </div>
            </div>
        </div>`;
    }

    window.saveParam = async (key, value) => {
        try {
            const body = {};
            body[key] = value;
            await apiFetch('/api/settings/scout', {
                method: 'POST',
                body: JSON.stringify(body)
            });
            if (currentState.scoutParams) currentState.scoutParams[key] = value;
        } catch { showToast('Errore salvataggio parametro.'); }
    };

    // ─── DASHBOARD / PIPELINE ───
    const STATUS_LABELS = {
        saved: 'Salvato',
        contacted: 'Contattato',
        replied: 'Ha risposto',
        meeting: 'Meeting',
        partner: 'Partner attivo',
        rejected: 'Scartato'
    };
    const STATUS_COLORS = {
        saved: '#8892b0',
        contacted: '#60a5fa',
        replied: '#a78bfa',
        meeting: '#facc15',
        partner: '#4ade80',
        rejected: '#f87171'
    };
    const STATUS_ORDER = ['saved', 'contacted', 'replied', 'meeting', 'partner', 'rejected'];

    window.loadDashboardView = loadDashboardView;
    async function loadDashboardView() {
        const el = document.getElementById('dashboard-content');
        if (!el) return;
        el.innerHTML = '<div class="loader-container"><div class="loader"></div></div>';
        try {
            const [conv, monthly] = await Promise.all([
                apiFetch('/api/dashboard/conversion', {}, false),
                apiFetch(`/api/reports/monthly`, {}, false)
            ]);
            renderDashboard(el, conv, monthly);
        } catch (e) {
            el.innerHTML = '<div class="card" style="padding:40px; text-align:center; color:var(--text-muted);">Impossibile caricare la pipeline.</div>';
        }
    }

    function renderDashboard(el, conv, monthly) {
        const f = conv.funnel;
        const t = conv.totals;
        const categoryEntries = Object.entries(conv.by_category || {})
            .map(([cat, s]) => ({ cat, ...s, total: STATUS_ORDER.reduce((a, k) => a + (s[k] || 0), 0) }))
            .sort((a, b) => b.total - a.total);

        const monthlyRows = (conv.monthly || []).slice(-6);
        const maxMonthly = Math.max(1, ...monthlyRows.map(m => Math.max(m.contacted, m.partner)));

        el.innerHTML = `
            <div class="dash-hero">
                <div class="dash-hero-tile"><div class="k">${t.leads}</div><div class="l">Lead totali</div></div>
                <div class="dash-hero-tile"><div class="k">${t.contacted_or_beyond}</div><div class="l">Contattati o oltre</div></div>
                <div class="dash-hero-tile"><div class="k" style="color:#4ade80;">${t.partners}</div><div class="l">Partner attivi</div></div>
                <div class="dash-hero-tile"><div class="k">${t.conversion_pct}%</div><div class="l">Contatto → Partner</div></div>
            </div>

            <div class="card card-full" style="margin-bottom:24px;">
                <h3 style="font-family:'Outfit'; margin-bottom:20px;">Funnel commerciale</h3>
                <div class="funnel-grid">
                    ${STATUS_ORDER.map(s => `
                        <div class="funnel-step stage-${s}">
                            <div class="funnel-label">${STATUS_LABELS[s]}</div>
                            <div class="funnel-count">${f[s] || 0}</div>
                        </div>
                    `).join('')}
                </div>
                <div style="font-size:12px; color:var(--text-muted); text-align:center;">
                    Aggiorna lo stato dal dettaglio di ogni lead. Il timestamp del passaggio a "Contattato", "Meeting" e "Partner" viene registrato automaticamente.
                </div>
            </div>

            <div class="grid-2col" style="align-items:start;">
                <div class="card">
                    <h3 style="font-family:'Outfit'; margin-bottom:20px;">Nicchie che convertono meglio</h3>
                    ${categoryEntries.length ? categoryEntries.map(row => {
                        const total = row.total || 1;
                        return `
                        <div class="dash-category-row">
                            <div style="font-size:12px; color:var(--text-main); overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${escapeHTML(row.cat)}">${escapeHTML(row.cat)}</div>
                            <div class="dash-category-bar" title="${STATUS_ORDER.map(s => `${STATUS_LABELS[s]}: ${row[s] || 0}`).join(' · ')}">
                                ${STATUS_ORDER.map(s => {
                                    const v = row[s] || 0;
                                    const w = (v / total) * 100;
                                    return v ? `<span style="width:${w}%; background:${STATUS_COLORS[s]};"></span>` : '';
                                }).join('')}
                            </div>
                            <div style="font-size:11px; color:var(--text-muted); text-align:right;">${row.total} lead</div>
                        </div>`;
                    }).join('') : '<div style="color:var(--text-muted); text-align:center; padding:24px;">Nessuna categoria ancora tracciata.</div>'}
                </div>

                <div class="card">
                    <h3 style="font-family:'Outfit'; margin-bottom:20px;">Ultimi 6 mesi</h3>
                    ${monthlyRows.length ? `
                        <div style="display:flex; align-items:flex-end; gap:14px; height:200px; padding:10px 0;">
                            ${monthlyRows.map(m => `
                                <div style="flex:1; text-align:center;">
                                    <div style="display:flex; gap:4px; align-items:flex-end; height:150px; justify-content:center;">
                                        <div title="Contattati: ${m.contacted}" style="width:14px; background:#60a5fa; border-radius:3px; height:${(m.contacted / maxMonthly) * 100}%;"></div>
                                        <div title="Partner nuovi: ${m.partner}" style="width:14px; background:#4ade80; border-radius:3px; height:${(m.partner / maxMonthly) * 100}%;"></div>
                                    </div>
                                    <div style="font-size:10px; color:var(--text-muted); margin-top:8px;">${m.month.slice(5)}/${m.month.slice(2,4)}</div>
                                </div>
                            `).join('')}
                        </div>
                        <div style="display:flex; gap:16px; justify-content:center; font-size:11px; color:var(--text-muted); margin-top:12px;">
                            <span><span style="display:inline-block; width:10px; height:10px; background:#60a5fa; border-radius:2px; vertical-align:middle;"></span> Contattati</span>
                            <span><span style="display:inline-block; width:10px; height:10px; background:#4ade80; border-radius:2px; vertical-align:middle;"></span> Partner nuovi</span>
                        </div>
                    ` : '<div style="color:var(--text-muted); text-align:center; padding:24px;">Nessun contatto tracciato negli ultimi 12 mesi.</div>'}
                </div>
            </div>

            <div class="card card-full" style="margin-top:24px;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px;">
                    <h3 style="font-family:'Outfit';">Report mese corrente (${monthly.month})</h3>
                    <span style="font-size:11px; color:var(--text-muted);">Endpoint per cron/email: <code>/api/reports/monthly?month=YYYY-MM</code></span>
                </div>
                <div style="display:grid; grid-template-columns:repeat(5, 1fr); gap:16px;">
                    <div class="stat-item"><div class="label-uppercase">Nuovi lead</div><div class="stat-value-lg">${monthly.pipeline.new_leads}</div></div>
                    <div class="stat-item"><div class="label-uppercase">Premium</div><div class="stat-value-lg">${monthly.pipeline.premium}</div></div>
                    <div class="stat-item"><div class="label-uppercase">Contattati</div><div class="stat-value-lg">${monthly.pipeline.contacted}</div></div>
                    <div class="stat-item"><div class="label-uppercase">Partner nuovi</div><div class="stat-value-lg" style="color:#4ade80;">${monthly.pipeline.new_partners}</div></div>
                    <div class="stat-item"><div class="label-uppercase">Costo mese</div><div class="stat-value-lg">$${monthly.costs.total_usd}</div></div>
                </div>
                ${monthly.pipeline.cost_per_new_partner_usd !== null ? `
                    <div style="margin-top:14px; text-align:center; font-size:13px; color:var(--text-muted);">
                        Costo di acquisizione per partner nuovo: <strong style="color:var(--accent);">$${monthly.pipeline.cost_per_new_partner_usd}</strong>
                    </div>
                ` : ''}
            </div>
        `;
    }

    // ─── LEAD DETAIL MODAL ───
    window.openLeadDetail = async (lid) => {
        const modal = document.getElementById('lead-detail-modal');
        const content = document.getElementById('lead-detail-content');
        if (!modal || !content) return;
        modal.style.display = 'block';
        content.innerHTML = '<div style="padding:60px; text-align:center;"><div class="loader"></div></div>';

        try {
            const leadsData = await apiFetch('/api/leads', {}, false);
            const lead = leadsData.find(l => l.id === lid);
            if (!lead) { modal.style.display = 'none'; return; }

            const seo = JSON.parse(lead.seo_data || '{}');
            const emails = (lead.email || '').split(',').map(e => e.trim()).filter(Boolean);
            const previewUrl = (typeof seo.preview_url === 'string' && seo.preview_url) ? seo.preview_url
                : (lead.website ? `https://api.microlink.io/?url=${encodeURIComponent(lead.website)}&screenshot=true&embed=screenshot.url` : '');

            content.innerHTML = `
                <div style="position:relative;">
                    <div style="height:200px; background:#000; position:relative; overflow:hidden; border-radius:28px 28px 0 0;">
                        ${previewUrl ? `<img src="${escapeHTML(previewUrl)}" style="width:100%; height:100%; object-fit:cover; opacity:0.4;">` : ''}
                        <button onclick="document.getElementById('lead-detail-modal').style.display='none'" style="position:absolute; top:15px; right:15px; background:rgba(0,0,0,0.6); border:none; color:#fff; width:40px; height:40px; border-radius:50%; cursor:pointer; font-size:20px; display:flex; align-items:center; justify-content:center;">&times;</button>
                        <div style="position:absolute; bottom:0; left:0; right:0; padding:25px 30px; background:linear-gradient(transparent, rgba(0,0,0,0.95));">
                            ${lead.is_premium ? '<span class="badge-gold" style="margin-bottom:8px; display:inline-block;">PREMIUM</span>' : ''}
                            <h2 style="font-family:'Outfit'; font-size:28px; margin:0;">${escapeHTML(lead.company_name)}</h2>
                            <a href="${escapeHTML(lead.website)}" target="_blank" style="color:var(--accent); text-decoration:none; font-size:14px;">${escapeHTML(lead.website)}</a>
                        </div>
                    </div>

                    <div style="padding:30px;">

                        ${emails.length || lead.phone || lead.address ? `
                        <div style="display:flex; flex-wrap:wrap; gap:12px; margin-bottom:24px; padding:20px; background:rgba(0,0,0,0.2); border-radius:16px;">
                            ${emails.map(e => `
                                <div style="display:flex; align-items:center; gap:8px; font-size:13px;">
                                    <i class="fa-solid fa-envelope" style="color:var(--accent);"></i>
                                    <span style="color:var(--text-main);">${escapeHTML(e)}</span>
                                    <button class="btn btn-ghost" style="padding:3px 8px; font-size:10px;" onclick="copyToClipboard('${escapeHTML(e)}')"><i class="fa-solid fa-copy"></i></button>
                                    <a href="mailto:${escapeHTML(e)}" class="btn btn-ghost" style="padding:3px 8px; font-size:10px; text-decoration:none;"><i class="fa-solid fa-paper-plane"></i></a>
                                </div>
                            `).join('')}
                            ${lead.phone ? `<div style="display:flex; align-items:center; gap:8px; font-size:13px;">
                                <i class="fa-solid fa-phone" style="color:#60a5fa;"></i>
                                <a href="tel:${escapeHTML(lead.phone)}" style="color:var(--text-main); text-decoration:none;">${escapeHTML(lead.phone)}</a>
                            </div>` : ''}
                            ${lead.address ? `<div style="display:flex; align-items:center; gap:8px; font-size:13px; width:100%;">
                                <i class="fa-solid fa-location-dot" style="color:#f87171;"></i>
                                <span style="color:var(--text-muted);">${escapeHTML(lead.address)}</span>
                            </div>` : ''}
                        </div>` : ''}

                        ${lead.selling_angle ? `
                        <div style="background:rgba(var(--accent-rgb),0.08); border:1px solid rgba(var(--accent-rgb),0.2); border-radius:16px; padding:20px; margin-bottom:24px;">
                            <h4 style="color:var(--accent); margin-bottom:8px; font-size:14px;"><i class="fa-solid fa-anchor"></i> Perché Contattarli</h4>
                            <p style="font-style:italic; font-size:15px; margin:0; line-height:1.5;">"${escapeHTML(lead.selling_angle)}"</p>
                        </div>` : ''}

                        ${lead.ai_audit ? `
                        <div style="margin-bottom:24px;">
                            <h4 style="margin-bottom:10px; font-size:14px;"><i class="fa-solid fa-robot" style="color:var(--accent);"></i> Analisi AI</h4>
                            <div style="background:rgba(0,0,0,0.2); border-radius:12px; padding:20px; white-space:pre-wrap; font-size:13px; line-height:1.8; color:var(--text-muted);">${escapeHTML(lead.ai_audit)}</div>
                        </div>` : ''}

                        ${seo.title ? `
                        <div style="margin-bottom:24px;">
                            <h4 style="margin-bottom:10px; font-size:14px;"><i class="fa-solid fa-chart-line" style="color:var(--accent);"></i> SEO del Sito</h4>
                            <div style="font-size:13px; color:var(--text-muted); line-height:1.6; background:rgba(0,0,0,0.15); border-radius:12px; padding:16px;">
                                <strong style="color:var(--text-main);">Titolo:</strong> ${escapeHTML(seo.title)}<br>
                                ${seo.description ? `<strong style="color:var(--text-main);">Descrizione:</strong> ${escapeHTML(seo.description)}<br>` : ''}
                                ${seo.h1 && seo.h1.length ? `<strong style="color:var(--text-main);">H1:</strong> ${seo.h1.map(h => escapeHTML(h)).join(', ')}` : ''}
                            </div>
                        </div>` : ''}

                        <div style="margin-bottom:24px;">
                            <h4 style="margin-bottom:10px; font-size:14px;"><i class="fa-solid fa-pen" style="color:var(--accent);"></i> Le Tue Note</h4>
                            <textarea id="modal-notes-${lid}" style="width:100%; min-height:100px; background:rgba(0,0,0,0.3); border:1px solid var(--border); border-radius:12px; color:var(--text-main); padding:15px; font-family:inherit; font-size:13px; resize:vertical; box-sizing:border-box;" placeholder="Aggiungi contesto, accordi, obiezioni e prossimo passo…">${escapeHTML(lead.user_notes || '')}</textarea>
                            <button class="btn btn-primary" style="margin-top:10px; padding:8px 20px; font-size:12px;" onclick="saveNotesFromModal(${lid})">
                                <i class="fa-solid fa-save"></i> Salva Note
                            </button>
                        </div>

                        <div style="padding-top:20px; border-top:1px solid var(--border); margin-bottom:20px;">
                            <div style="font-size:11px; color:var(--text-muted); text-transform:uppercase; letter-spacing:1px; margin-bottom:10px;">Stato Trattativa</div>
                            <div id="modal-status-bar-${lid}" style="display:flex; gap:6px; flex-wrap:wrap;">
                                ${STATUS_ORDER.map(s => `
                                    <button class="status-pill-lead ${s}" style="border:none; cursor:pointer; opacity:${lead.status === s ? '1' : '0.5'}; transform: ${lead.status === s ? 'scale(1.05)' : 'scale(1)'}" onclick="changeLeadStatus(${lid}, '${s}', this)">
                                        ${STATUS_LABELS[s]}
                                    </button>
                                `).join('')}
                            </div>
                            <div style="margin-top:10px; font-size:11px; color:var(--text-muted);">
                                ${lead.contacted_at ? `📤 Contattato: ${new Date(lead.contacted_at).toLocaleDateString('it-IT')} · ` : ''}
                                ${lead.replied_at ? `↩️ Risposta: ${new Date(lead.replied_at).toLocaleDateString('it-IT')} · ` : ''}
                                ${lead.meeting_at ? `📅 Meeting: ${new Date(lead.meeting_at).toLocaleDateString('it-IT')} · ` : ''}
                                ${lead.partner_since ? `🤝 Partner dal: ${new Date(lead.partner_since).toLocaleDateString('it-IT')}` : ''}
                            </div>
                        </div>

                        ${(() => {
                            let people = [];
                            try { people = JSON.parse(lead.contacts_json || '[]'); } catch {}
                            return people.length ? `
                                <div style="margin-bottom:24px;">
                                    <h4 style="margin-bottom:10px; font-size:14px;"><i class="fa-solid fa-users" style="color:var(--accent);"></i> Persone identificate (${people.length})</h4>
                                    <div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(240px, 1fr)); gap:10px;">
                                        ${people.map(p => `
                                            <div style="background:rgba(0,0,0,0.2); border:1px solid var(--border); border-radius:12px; padding:12px;">
                                                <div style="font-weight:600; font-size:13px; color:var(--text-main);">${escapeHTML(p.name)}</div>
                                                <div style="font-size:11px; color:var(--accent);">${escapeHTML(p.role || '')}</div>
                                                ${p.email ? `<div style="font-size:11px; color:${p.is_generic_email ? '#f87171' : '#4ade80'}; margin-top:4px;"><i class="fa-solid fa-envelope"></i> ${escapeHTML(p.email)}</div>` : ''}
                                            </div>
                                        `).join('')}
                                    </div>
                                </div>` : '';
                        })()}

                        ${lead.is_generic_email ? `
                            <div class="generic-email-warn" style="display:block; margin-bottom:20px; padding:12px 14px; text-align:left;">
                                <i class="fa-solid fa-circle-exclamation"></i>
                                <strong>Email generica</strong> — la casella (${escapeHTML((lead.email || '').split(',')[0].trim())}) non punta a un decisore. Prova a estrarre un contatto personale dal sito o LinkedIn prima di contattare.
                            </div>
                        ` : ''}

                        <div id="draft-email-block-${lid}" style="margin-bottom:24px;">
                            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
                                <h4 style="font-size:14px;"><i class="fa-solid fa-envelope-open-text" style="color:var(--accent);"></i> Bozza email B2B</h4>
                                <button class="btn btn-primary" style="padding:8px 16px; font-size:12px;" onclick="generateDraftEmail(${lid}, ${lead.draft_email ? 'true' : 'false'})">
                                    <i class="fa-solid fa-wand-magic-sparkles"></i> ${lead.draft_email ? 'Rigenera' : 'Genera con AI'}
                                </button>
                            </div>
                            <div id="draft-email-body-${lid}">
                                ${lead.draft_email ? `
                                    <textarea readonly style="width:100%; min-height:220px; background:rgba(0,0,0,0.3); border:1px solid var(--border); border-radius:12px; color:var(--text-main); padding:15px; font-family:inherit; font-size:13px; box-sizing:border-box; line-height:1.5;">${escapeHTML(lead.draft_email)}</textarea>
                                    <div style="display:flex; gap:8px; margin-top:8px;">
                                        <button class="btn btn-ghost" style="padding:6px 14px; font-size:12px;" onclick="copyToClipboard(${JSON.stringify(lead.draft_email).replace(/</g,'\\u003c')})">
                                            <i class="fa-solid fa-copy"></i> Copia
                                        </button>
                                        ${(lead.email || '').trim() ? `<a class="btn btn-ghost" style="padding:6px 14px; font-size:12px; text-decoration:none;" href="mailto:${escapeHTML((lead.email||'').split(',')[0].trim())}?subject=${encodeURIComponent((lead.draft_email.match(/^Oggetto: (.*)/)||[])[1]||'')}&body=${encodeURIComponent(lead.draft_email.replace(/^Oggetto:.*\n\n/, ''))}">
                                            <i class="fa-solid fa-paper-plane"></i> Apri nel client mail
                                        </a>` : ''}
                                    </div>
                                ` : '<div style="color:var(--text-muted); font-size:12px; padding:14px; background:rgba(0,0,0,0.15); border-radius:10px;">Nessuna bozza generata. Clicca "Genera con AI" per creare una prima email di outreach basata sul selling angle.</div>'}
                            </div>
                        </div>

                        <div style="display:flex; gap:12px; flex-wrap:wrap; padding-top:20px; border-top:1px solid var(--border);">
                            <button class="btn btn-primary" style="flex:1; justify-content:center; padding:12px;" onclick="triggerAuditFromModal(${lid}, this)">
                                <i class="fa-solid fa-wand-magic-sparkles"></i> Aggiorna Audit AI
                            </button>
                            <button class="btn btn-ghost" style="flex:1; justify-content:center; padding:12px; border-color:var(--accent); color:var(--accent);" onclick="createPortalFromLead(${lid}, '${escapeHTML(lead.company_name.replace(/'/g, "\\'"))}', '${escapeHTML((lead.phone || '').replace(/'/g, "\\'"))}', '${escapeHTML(((lead.email || '').split(',')[0] || '').trim().replace(/'/g, "\\'"))}')">
                                <i class="fa-solid fa-qrcode"></i> Crea Portale Partner (/p/…)
                            </button>
                            <a href="${escapeHTML(lead.website)}" target="_blank" class="btn btn-ghost" style="flex:1; justify-content:center; text-decoration:none; padding:12px;">
                                <i class="fa-solid fa-arrow-up-right-from-square"></i> Visita Sito
                            </a>
                        </div>
                    </div>
                </div>`;
        } catch (e) {
            console.error('Lead Detail Error:', e);
            content.innerHTML = '<div style="padding:60px; text-align:center; color:var(--text-muted);">Errore nel caricamento.</div>';
        }
    };

    window.saveNotesFromModal = async (lid) => {
        const textarea = document.getElementById(`modal-notes-${lid}`);
        if (!textarea) return;
        try {
            await apiFetch(`/api/leads/${lid}/notes`, {
                method: 'POST',
                body: JSON.stringify({ notes: textarea.value })
            });
            showToast('Note salvate!');
        } catch { showToast('Errore salvataggio.'); }
    };

    window.triggerAuditFromModal = async (lid, btn) => {
        const oldContent = btn.innerHTML;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Audit...';
        btn.disabled = true;
        try {
            await fetch(`/api/leads/audit/${lid}`, {
                method: 'POST',
                headers: { 'X-User-ID': currentState.currentUser.id }
            });
            showToast('Audit completato! Aggiornamento...');
            openLeadDetail(lid); // Refresh modal
            loadLeads(); // Refresh list behind
        } catch {
            showToast('Errore Audit.');
            btn.innerHTML = oldContent;
            btn.disabled = false;
        }
    };

    // Close modal on click outside or Escape
    document.getElementById('lead-detail-modal')?.addEventListener('click', (e) => {
        if (e.target.id === 'lead-detail-modal') e.target.style.display = 'none';
    });

    // ─── SCOUT ───
    const startStrategyBtn = document.getElementById('start-strategy-btn');
    if (startStrategyBtn) {
        startStrategyBtn.addEventListener('click', () => startStrategicScouting());
    }

    // Crea una card step espandibile nella console
    function createStepCard(id, icon, title, subtitle, borderColor) {
        return `
        <div id="step-${id}" class="step-card" style="background:rgba(255,255,255,0.02); border:1px solid #222; border-left:3px solid ${borderColor}; border-radius:10px; overflow:hidden; transition:all 0.3s;">
            <div class="step-header" style="padding:12px 16px; cursor:pointer; display:flex; align-items:center; gap:10px;" onclick="this.parentElement.querySelector('.step-body').style.display = this.parentElement.querySelector('.step-body').style.display === 'none' ? 'block' : 'none'; this.querySelector('.chevron').classList.toggle('fa-chevron-down'); this.querySelector('.chevron').classList.toggle('fa-chevron-right');">
                <i class="${icon}" style="color:${borderColor}; font-size:13px; width:18px; text-align:center;"></i>
                <span style="font-weight:600; font-size:13px; color:var(--text-main); flex:1;">${title}</span>
                <span style="font-size:11px; color:var(--text-muted);">${subtitle}</span>
                <i class="fa-solid fa-chevron-right chevron" style="font-size:10px; color:var(--text-muted); transition:transform 0.2s;"></i>
            </div>
            <div class="step-body" style="display:none; padding:0 16px 14px; font-size:12px; color:var(--text-muted); line-height:1.6;">
            </div>
        </div>`;
    }

    function appendStepContent(stepId, html) {
        const body = document.querySelector(`#step-${stepId} .step-body`);
        if (body) body.innerHTML += html;
    }

    function expandStep(stepId) {
        const body = document.querySelector(`#step-${stepId} .step-body`);
        if (body) body.style.display = 'block';
        const chevron = document.querySelector(`#step-${stepId} .chevron`);
        if (chevron) { chevron.classList.remove('fa-chevron-right'); chevron.classList.add('fa-chevron-down'); }
    }

    async function startStrategicScouting() {
        if (!requireActiveClient()) return;
        const consoleEl = document.getElementById('strategy-console');
        const outputEl = document.getElementById('console-output');
        const stepsEl = document.getElementById('console-steps');
        const resultsEl = document.getElementById('strategy-live-results');
        const queryBox = document.getElementById('query-selection-box');
        const progressText = document.getElementById('scout-progress-text');
        const btn = document.getElementById('start-strategy-btn');

        if (!consoleEl || !stepsEl || !btn) return;

        // Read slider values
        const maxResults = parseInt(document.getElementById('max-results-slider')?.value || '10');
        const minScore = parseInt(document.getElementById('min-score-slider')?.value || '0');

        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Ricerca in Corso...';
        consoleEl.style.display = 'block';
        stepsEl.innerHTML = '';
        outputEl.innerHTML = '<span style="color:#666;">> Connessione al server...</span>';
        resultsEl.innerHTML = '';
        if (queryBox) queryBox.style.display = 'none';

        consoleEl.scrollIntoView({ behavior: 'smooth', block: 'start' });

        if (window.currentEventSource) {
            try { window.currentEventSource.close(); } catch (e) {}
        }

        const url = `/api/scout/stream?user_id=${currentState.currentUser.id}&client_id=${encodeURIComponent(currentState.currentClient.slug)}&max_results=${maxResults}&min_score=${minScore}`;
        const eventSource = new EventSource(url);
        window.currentEventSource = eventSource;

        let currentStepId = 0;

        eventSource.onmessage = (event) => {
            const update = JSON.parse(event.data);
            const t = update.type;

            if (t === 'query_selected') {
                const d = update.data;
                if (queryBox) {
                    queryBox.style.display = 'block';
                    const methodLabel = d.selection_method === 'random'
                        ? `<span style="background:rgba(250,204,21,0.15); color:#facc15; padding:2px 8px; border-radius:6px; font-size:10px; font-weight:700;">RANDOM</span>`
                        : `<span style="background:rgba(74,222,128,0.15); color:#4ade80; padding:2px 8px; border-radius:6px; font-size:10px; font-weight:700;">MANUALE</span>`;
                    queryBox.innerHTML = `
                        <div style="display:flex; align-items:center; gap:10px; margin-bottom:10px;">
                            <i class="fa-solid fa-dice" style="color:var(--accent); font-size:16px;"></i>
                            <span style="font-weight:700; font-size:14px; color:var(--text-main);">Query Selezionata</span>
                            ${methodLabel}
                        </div>
                        <div style="font-size:16px; color:var(--accent); font-weight:700; font-family:'Outfit'; margin-bottom:8px;">"${escapeHTML(d.query)}"</div>
                        <div style="font-size:12px; color:var(--text-muted); margin-bottom:10px;">
                            Categoria: <strong style="color:var(--text-main);">${escapeHTML(d.category)}</strong>
                            ${d.pool_size ? ` &middot; Estratta da un pool di ${d.pool_size} query &middot; ${d.total_categories} categorie &middot; ${d.total_queries_available} query totali` : ''}
                        </div>
                        ${d.all_queries_in_category && d.all_queries_in_category.length ? `
                        <details style="margin-top:6px;">
                            <summary style="font-size:11px; color:var(--accent); cursor:pointer; font-weight:600;">Mostra tutte le query di questa categoria (${d.all_queries_in_category.length})</summary>
                            <div style="margin-top:8px; display:flex; flex-wrap:wrap; gap:6px;">
                                ${d.all_queries_in_category.map(q => `<span style="padding:3px 10px; background:${q === d.query ? 'var(--accent)' : 'rgba(255,255,255,0.05)'}; color:${q === d.query ? 'var(--primary)' : 'var(--text-muted)'}; border-radius:6px; font-size:11px; font-weight:${q === d.query ? '700' : '400'};">${escapeHTML(q)}</span>`).join('')}
                            </div>
                        </details>` : ''}
                    `;
                }
            } else if (t === 'places_results') {
                const d = update.data;
                outputEl.innerHTML += `<div style="color:#4ade80;">> Google Places: ${d.total_found} risultati trovati, analizzo i primi ${d.analyzing} (costo stimato: ${d.estimated_api_calls} chiamate API)</div>`;
                if (progressText) progressText.textContent = `0/${d.analyzing}`;
            } else if (t === 'analysis_start') {
                const d = update.data;
                currentStepId = d.index;
                if (progressText) progressText.textContent = `${d.index}/${d.total}`;
                stepsEl.innerHTML += createStepCard(
                    d.index,
                    'fa-solid fa-spinner fa-spin',
                    escapeHTML(d.name),
                    `${d.index}/${d.total}`,
                    '#555'
                );
                appendStepContent(d.index, `
                    <div style="display:flex; flex-wrap:wrap; gap:12px; margin-top:4px;">
                        <span><i class="fa-solid fa-location-dot" style="color:#f87171;"></i> ${escapeHTML(d.address)}</span>
                        ${d.rating ? `<span><i class="fa-solid fa-star" style="color:#facc15;"></i> ${d.rating} (${d.user_ratings_total} recensioni)</span>` : ''}
                    </div>
                `);
                stepsEl.scrollTop = stepsEl.scrollHeight;
            } else if (t === 'analysis_skip') {
                const d = update.data;
                const el = document.querySelector(`#step-${d.index}`);
                if (el) {
                    el.style.borderLeftColor = '#666';
                    el.querySelector('.step-header i').className = 'fa-solid fa-forward';
                    el.querySelector('.step-header i').style.color = '#666';
                }
                appendStepContent(d.index, `<div style="color:#888; margin-top:6px;"><i class="fa-solid fa-forward"></i> Saltato: ${escapeHTML(d.reason)}</div>`);
            } else if (t === 'scraping_start') {
                const d = update.data;
                appendStepContent(d.index, `<div style="margin-top:8px; color:var(--accent);"><i class="fa-solid fa-spider"></i> Scraping: <a href="${escapeHTML(d.website)}" target="_blank" style="color:var(--accent);">${escapeHTML(d.website)}</a>...</div>`);
            } else if (t === 'scrape_result') {
                const d = update.data;
                appendStepContent(d.index, `
                    <div style="margin-top:8px; background:rgba(0,0,0,0.3); border-radius:8px; padding:12px;">
                        <div style="font-weight:600; color:var(--text-main); margin-bottom:8px;"><i class="fa-solid fa-file-code"></i> Dati Estratti dal Sito</div>
                        <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-bottom:8px;">
                            <div><span style="color:var(--accent); font-size:10px; text-transform:uppercase;">Contenuto</span><br>${d.content_length} caratteri</div>
                            <div><span style="color:var(--accent); font-size:10px; text-transform:uppercase;">Email trovate</span><br>${d.emails_found.length ? d.emails_found.map(e => `<span style="color:#4ade80;">${escapeHTML(e)}</span>`).join(', ') : '<span style="color:#888;">nessuna</span>'}</div>
                        </div>
                        ${d.seo.title ? `
                        <div style="margin-top:6px; padding:8px; background:rgba(0,0,0,0.2); border-radius:6px;">
                            <span style="color:var(--accent); font-size:10px; text-transform:uppercase;">SEO Metadata</span>
                            <div style="margin-top:4px; font-size:11px;">
                                <strong>Title:</strong> ${escapeHTML(d.seo.title)}<br>
                                ${d.seo.description ? `<strong>Description:</strong> ${escapeHTML(d.seo.description.substring(0, 150))}${d.seo.description.length > 150 ? '...' : ''}<br>` : ''}
                                ${d.seo.h1 && d.seo.h1.length ? `<strong>H1:</strong> ${d.seo.h1.map(h => escapeHTML(h)).join(', ')}` : ''}
                            </div>
                        </div>` : ''}
                        <details style="margin-top:8px;">
                            <summary style="font-size:11px; color:var(--accent); cursor:pointer;">Anteprima contenuto estratto</summary>
                            <div style="margin-top:6px; font-size:11px; color:#888; max-height:150px; overflow-y:auto; white-space:pre-wrap; background:rgba(0,0,0,0.2); padding:8px; border-radius:6px;">${escapeHTML(d.content_preview)}</div>
                        </details>
                        <div style="margin-top:6px; font-size:10px; color:#555;">Pagine scrappate: ${d.pages_scraped.map(p => '/' + p).join(', ')}</div>
                    </div>
                `);
            } else if (t === 'ai_analysis_start') {
                const d = update.data;
                appendStepContent(d.index, `<div style="margin-top:8px; color:#a78bfa;"><i class="fa-solid fa-robot fa-spin"></i> Analisi AI in corso (${d.content_sent_chars} chars inviati a DeepSeek)...</div>`);
            } else if (t === 'ai_analysis_result') {
                const d = update.data;
                const scoreColor = d.score >= 7 ? '#4ade80' : d.score >= 4 ? '#facc15' : '#f87171';
                appendStepContent(d.index, `
                    <div style="margin-top:8px; background:rgba(0,0,0,0.3); border-radius:8px; padding:12px;">
                        <div style="display:flex; align-items:center; gap:12px; margin-bottom:8px;">
                            <div style="width:40px; height:40px; border-radius:10px; background:${scoreColor}22; border:2px solid ${scoreColor}; display:flex; align-items:center; justify-content:center; font-weight:800; font-size:16px; color:${scoreColor};">${d.score}</div>
                            <div>
                                <div style="font-weight:700; color:var(--text-main); font-size:13px;">${escapeHTML(d.client_type)}</div>
                                <div style="font-size:11px; color:var(--text-muted);">${d.is_premium ? 'PREMIUM' : 'Standard'}</div>
                            </div>
                        </div>
                        <div style="font-size:12px; color:var(--text-main); margin-bottom:6px;"><strong>Motivazione:</strong> ${escapeHTML(d.reason)}</div>
                        <div style="font-size:12px; color:var(--text-muted);"><strong>Angolo di vendita:</strong> <em>${escapeHTML(d.selling_angle)}</em></div>
                        <details style="margin-top:8px;">
                            <summary style="font-size:11px; color:var(--accent); cursor:pointer;">Risposta AI completa (raw)</summary>
                            <pre style="margin-top:6px; font-size:10px; color:#888; background:rgba(0,0,0,0.2); padding:8px; border-radius:6px; overflow-x:auto;">${escapeHTML(JSON.stringify(d.raw_analysis, null, 2))}</pre>
                        </details>
                    </div>
                `);
            } else if (t === 'analysis_complete') {
                const d = update.data;
                const el = document.querySelector(`#step-${d.index}`);
                if (el) {
                    const icon = el.querySelector('.step-header i');
                    if (d.decision === 'premium') {
                        el.style.borderLeftColor = '#4ade80';
                        icon.className = 'fa-solid fa-crown';
                        icon.style.color = '#c5a059';
                    } else if (d.decision === 'saved') {
                        el.style.borderLeftColor = '#60a5fa';
                        icon.className = 'fa-solid fa-check';
                        icon.style.color = '#60a5fa';
                    } else if (d.decision === 'filtered') {
                        el.style.borderLeftColor = '#666';
                        icon.className = 'fa-solid fa-filter-circle-xmark';
                        icon.style.color = '#666';
                    }
                }
            } else if (t === 'niche_learned') {
                const d = update.data;
                appendStepContent(currentStepId, `
                    <div style="margin-top:8px; background:rgba(197,160,89,0.08); border:1px solid rgba(197,160,89,0.2); border-radius:8px; padding:10px;">
                        <div style="font-size:11px; color:var(--accent); font-weight:700;"><i class="fa-solid fa-brain"></i> Nuova Nicchia Appresa</div>
                        <div style="font-size:13px; color:var(--text-main); margin-top:4px;"><strong>${escapeHTML(d.niche)}</strong>: ${escapeHTML(d.description)}</div>
                        <div style="margin-top:6px; display:flex; flex-wrap:wrap; gap:4px;">
                            ${d.suggested_queries.map(q => `<span style="padding:2px 8px; background:rgba(var(--accent-rgb),0.1); border-radius:4px; font-size:10px; color:var(--accent);">${escapeHTML(q)}</span>`).join('')}
                        </div>
                    </div>
                `);
            } else if (t === 'lead_found') {
                const lead = update.data;
                const scoreColor = lead.score >= 7 ? '#4ade80' : lead.score >= 4 ? '#facc15' : '#f87171';
                const badgeHtml = lead.is_premium
                    ? '<span class="badge-gold" style="font-size:9px;">PREMIUM</span>'
                    : `<span style="background:${scoreColor}22; color:${scoreColor}; padding:2px 8px; border-radius:6px; font-size:9px; font-weight:700;">SCORE ${lead.score}</span>`;

                const leadHtml = `
                    <div class="lead-row" style="background:rgba(${lead.is_premium ? 'var(--accent-rgb),0.1' : '255,255,255,0.02'}); border-left:4px solid ${lead.is_premium ? 'var(--accent)' : scoreColor}; animation:slideIn 0.3s ease-out; padding:15px; display:grid; grid-template-columns:50px 1fr 1fr 100px; gap:15px; align-items:center; border-radius:8px;">
                        <div style="width:40px; height:40px; border-radius:10px; background:${scoreColor}22; border:2px solid ${scoreColor}; display:flex; align-items:center; justify-content:center; font-weight:800; font-size:16px; color:${scoreColor};">${lead.score}</div>
                        <div>
                            <div style="color:#fff; font-size:14px; font-weight:600;">${escapeHTML(lead.name)}</div>
                            <div style="font-size:11px; color:var(--text-muted);">${escapeHTML(lead.client_type || '')}</div>
                        </div>
                        <div style="font-size:12px;">
                            ${lead.email ? `<div style="color:#4ade80;"><i class="fa-solid fa-envelope"></i> ${escapeHTML(lead.email)}</div>` : ''}
                            ${lead.phone ? `<div style="color:#60a5fa;"><i class="fa-solid fa-phone"></i> ${escapeHTML(lead.phone)}</div>` : ''}
                        </div>
                        <div style="text-align:right;">${badgeHtml}</div>
                    </div>
                `;
                resultsEl.innerHTML = leadHtml + resultsEl.innerHTML;
            } else if (t === 'status') {
                outputEl.innerHTML += `<div style="color:#fff;">> ${escapeHTML(update.message)}</div>`;
                outputEl.scrollTop = outputEl.scrollHeight;
            } else if (t === 'log') {
                outputEl.innerHTML += `<div style="color:#555;">> ${escapeHTML(update.message || '')}</div>`;
                outputEl.scrollTop = outputEl.scrollHeight;
            } else if (t === 'final') {
                const d = update.data;
                if (progressText) progressText.textContent = 'Completato';
                const dots = document.getElementById('strategy-progress-dots');
                if (dots) { dots.classList.remove('animate-pulse'); dots.style.background = '#4ade80'; }
                outputEl.innerHTML += `<div style="color:var(--accent); font-weight:700; margin-top:8px;">> COMPLETATO: ${d.total_analyzed} analizzati, ${d.total_saved} salvati, ${d.premium_found} premium per "${escapeHTML(d.query)}"</div>`;
                outputEl.scrollTop = outputEl.scrollHeight;
                eventSource.close();
                btn.disabled = false;
                btn.innerHTML = '<i class="fa-solid fa-check"></i> Ricerca Completata';
                setTimeout(() => {
                    btn.innerHTML = '<i class="fa-solid fa-play"></i> Avvia Ricerca Automatica';
                }, 5000);
                loadLeads();
                loadIntelligence();
            } else if (t === 'error') {
                outputEl.innerHTML += `<div style="color:#f87171;">> ERRORE: ${escapeHTML(update.message)}</div>`;
                eventSource.close();
                btn.disabled = false;
                btn.innerHTML = '<i class="fa-solid fa-play"></i> Riprova';
            }
        };

        eventSource.onerror = (err) => {
            console.error('SSE Error:', err);
            outputEl.innerHTML += `<div style="color:#f87171;">> Errore di connessione.</div>`;
            eventSource.close();
            btn.disabled = false;
            btn.innerHTML = '<i class="fa-solid fa-play"></i> Riprova';
        };
    }

    // ─── SEARCH HANDLERS ───
    if (elements.searchBtn) {
        elements.searchBtn.onclick = () => executeSearch(elements.searchInput.value);
    }
    if (elements.searchInput) {
        elements.searchInput.onkeypress = (e) => { if (e.key === 'Enter') executeSearch(elements.searchInput.value); };
    }

    if (elements.scoutBtn) {
        elements.scoutBtn.onclick = () => {
            if (!requireActiveClient()) return;
            switchView('discovery');
            showToast(`Ricerca pronta per ${currentState.currentClient.name}. Scegli una categoria o avvia la ricerca automatica.`);
            elements.searchInput?.focus();
        };
    }

    const syncBtn = document.getElementById('manual-sync-btn');
    if (syncBtn) {
        syncBtn.onclick = async () => {
            setBusy(syncBtn, true);
            try {
                const resp = await fetch('/api/admin/sync', { 
                    method: 'POST',
                    headers: { 'X-User-ID': currentState.currentUser.id }
                });
                const res = await resp.json();
                if (res.ok) showToast(res.message);
                else showToast(res.error);
            } catch (e) { showToast('Sync fallito.'); }
            finally { setBusy(syncBtn, false); }
        };
    }

    // ─── LESLIE 2.0: BANCO VENDITE & PREVENTIVATORE GIRI IN BARCA ───
    let commerceCache = { kpis: {}, offers: [], venues: [], portals: [], bookings: [] };

    async function loadSalesView() {
        const el = document.getElementById('sales-view-content');
        if (!el) return;
        try {
            const data = await apiFetch('/api/commerce/overview', {}, false);
            commerceCache = data;
            renderSalesDesk(el, data);
        } catch (e) {
            el.innerHTML = `<div class="card" style="padding:40px; text-align:center; color:var(--text-muted);">Errore caricamento Banco Vendite.</div>`;
        }
    }
    window.loadSalesView = loadSalesView;

    function renderSalesDesk(el, data) {
        const k = data.kpis || {};
        const offers = data.offers || [];
        const restaurants = (data.venues || []).filter(v => v.venue_type === 'restaurant');
        const stays = (data.venues || []).filter(v => v.venue_type === 'holiday_home');
        const portals = data.portals || [];
        const bookings = data.bookings || [];

        const statusBadge = (st) => {
            const map = {
                inquiry: { label: 'Richiesta da Partner', color: '#facc15' },
                quote_sent: { label: 'Preventivo Inviato', color: '#60a5fa' },
                confirmed: { label: 'Confermato 🛥️', color: '#4ade80' },
                completed: { label: 'Completato ✅', color: '#22c55e' },
                cancelled: { label: 'Annullato', color: '#f87171' }
            };
            const item = map[st] || { label: st, color: '#94a3b8' };
            return `<span style="padding:4px 10px; border-radius:999px; font-size:11px; font-weight:700; background:${item.color}22; color:${item.color}; border:1px solid ${item.color}44;">${item.label}</span>`;
        };

        el.innerHTML = `
            <!-- KPI Row -->
            <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:16px; margin-bottom:26px;">
                <div class="card" style="padding:18px; border-left:3px solid #4ade80;">
                    <div style="font-size:11px; color:var(--text-muted); text-transform:uppercase;">Giri Barca Confermati</div>
                    <div style="font-size:28px; font-weight:700; font-family:'Outfit'; color:#4ade80; margin-top:4px;">€${k.boat_revenue_confirmed_eur || 0}</div>
                    <div style="font-size:11px; color:var(--text-muted);">${k.confirmed_boats || 0} uscite confermate · €${k.boat_revenue_pipeline_eur || 0} in pipeline</div>
                </div>
                <div class="card" style="padding:18px; border-left:3px solid var(--accent);">
                    <div style="font-size:11px; color:var(--text-muted); text-transform:uppercase;">Coperti Mandati a Ristoranti</div>
                    <div style="font-size:28px; font-weight:700; font-family:'Outfit'; color:var(--accent); margin-top:4px;">${k.restaurant_covers_sent || 0} <small style="font-size:13px;">ospiti</small></div>
                    <div style="font-size:11px; color:var(--text-muted);">Boat-to-Table (San Fruttuoso / Portofino)</div>
                </div>
                <div class="card" style="padding:18px; border-left:3px solid #67c5b8;">
                    <div style="font-size:11px; color:var(--text-muted); text-transform:uppercase;">Notti Case Vacanza (Ale/Cosimo)</div>
                    <div style="font-size:28px; font-weight:700; font-family:'Outfit'; color:#67c5b8; margin-top:4px;">${k.stay_nights_sent || 0} <small style="font-size:13px;">notti</small></div>
                    <div style="font-size:11px; color:var(--text-muted);">Pacchetti Stay &amp; Sail e gruppi</div>
                </div>
                <div class="card" style="padding:18px; border-left:3px solid #a78bfa;">
                    <div style="font-size:11px; color:var(--text-muted); text-transform:uppercase;">Fee &amp; Commissioni Referral</div>
                    <div style="font-size:28px; font-weight:700; font-family:'Outfit'; color:#a78bfa; margin-top:4px;">€${(k.referral_fees_eur || 0).toFixed(0)}</div>
                    <div style="font-size:11px; color:var(--text-muted);">Valore generato verso Ristoranti &amp; Case</div>
                </div>
            </div>

            <!-- Instant Quote & Smart Dispatcher -->
            <div class="card card-full" style="margin-bottom:28px; border:1px solid rgba(197,160,89,0.3);">
                <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:12px; margin-bottom:18px;">
                    <div>
                        <span class="badge badge-gold"><i class="fa-solid fa-bolt"></i> Preventivatore &amp; Smart Boat Dispatcher</span>
                        <h2 style="font-family:'Outfit'; font-size:22px; margin-top:8px;">Crea Offerta Giro in Barca + Ristorante + Casa in 30 secondi</h2>
                        <p style="font-size:13px; color:var(--text-muted);">Incolla un messaggio WhatsApp/Email oppure compila i campi: fino a 6 ospiti assegna <strong>Gabri</strong>, da 7 a 40 ospiti assegna <strong>Massi</strong>.</p>
                    </div>
                </div>

                <!-- Natural Language Paste Box -->
                <div style="display:flex; gap:10px; margin-bottom:18px; flex-wrap:wrap;">
                    <input type="text" id="q-raw-paste" class="search-input" style="flex:1; min-width:260px; background:rgba(0,0,0,0.3);" placeholder="Incolla messaggio cliente (es. 'Hi! We are 4 people on 2026-07-20, we want a sunset boat tour + dinner in Portofino')...">
                    <button type="button" class="btn btn-ghost" style="border-color:var(--accent); color:var(--accent);" onclick="parseAndDispatchQuote()">
                        <i class="fa-solid fa-wand-magic-sparkles"></i> Analizza &amp; Smista in Automatico
                    </button>
                </div>

                <div id="q-dispatch-banner" style="display:none; margin-bottom:16px; padding:12px 16px; border-radius:12px; background:rgba(197,160,89,0.1); border-left:3px solid var(--accent); font-size:13px;"></div>

                <form id="quick-quote-form" onsubmit="submitQuickQuote(event)">
                    <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:14px; margin-bottom:16px;">
                        <label style="font-size:12px; color:var(--text-muted);">Nome Cliente / Gruppo *
                            <input type="text" id="q-customer-name" required placeholder="Es. Fam. Johnson / Concierge Splendido" style="width:100%; margin-top:4px; padding:10px; border-radius:10px; background:rgba(0,0,0,0.3); border:1px solid var(--border); color:var(--text-main);">
                        </label>
                        <label style="font-size:12px; color:var(--text-muted);">WhatsApp o Email Cliente
                            <input type="text" id="q-customer-contact" placeholder="+39 / +1 ... o email" style="width:100%; margin-top:4px; padding:10px; border-radius:10px; background:rgba(0,0,0,0.3); border:1px solid var(--border); color:var(--text-main);">
                        </label>
                        <label style="font-size:12px; color:var(--text-muted);">Numero Ospiti *
                            <input type="number" id="q-guests" min="1" max="60" value="4" required oninput="onQuoteGuestsChange(this.value)" style="width:100%; margin-top:4px; padding:10px; border-radius:10px; background:rgba(0,0,0,0.3); border:1px solid var(--border); color:var(--text-main);">
                        </label>
                        <label style="font-size:12px; color:var(--text-muted);">Data Uscita
                            <input type="date" id="q-date" onchange="checkQuoteAvailability()" style="width:100%; margin-top:4px; padding:10px; border-radius:10px; background:rgba(0,0,0,0.3); border:1px solid var(--border); color:var(--text-main);">
                        </label>
                        <label style="font-size:12px; color:var(--text-muted);">Fascia Oraria
                            <select id="q-slot" onchange="checkQuoteAvailability()" style="width:100%; margin-top:4px; padding:10px; border-radius:10px; background:rgba(0,0,0,0.3); border:1px solid var(--border); color:var(--text-main);">
                                <option value="full_day">Giornata Intera con Pranzo (10:00–17:00)</option>
                                <option value="sunset">Tramonto &amp; Aperitivo (18:00–20:30)</option>
                                <option value="morning">Mattina (09:30–13:00)</option>
                                <option value="afternoon">Pomeriggio (14:00–17:30)</option>
                            </select>
                        </label>
                        <label style="font-size:12px; color:var(--text-muted);">Lingua Preventivo
                            <select id="q-lang" style="width:100%; margin-top:4px; padding:10px; border-radius:10px; background:rgba(0,0,0,0.3); border:1px solid var(--border); color:var(--text-main);">
                                <option value="it">🇮🇹 Italiano</option>
                                <option value="en">🇬🇧/🇺🇸 English</option>
                                <option value="fr">🇫🇷 Français</option>
                            </select>
                        </label>
                    </div>

                    <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); gap:14px; margin-bottom:18px;">
                        <label style="font-size:12px; color:var(--text-muted);">🛥️ Esperienza in Barca *
                            <select id="q-offer-id" onchange="onQuoteOfferSelect(this.value)" style="width:100%; margin-top:4px; padding:10px; border-radius:10px; background:rgba(0,0,0,0.3); border:1px solid var(--border); color:var(--text-main);">
                                ${offers.map(o => `<option value="${o.id}" data-price="${o.base_price_eur}" data-op="${o.operator_slug}">${escapeHTML(o.operator_name)} · ${escapeHTML(o.title)} (€${o.base_price_eur})</option>`).join('')}
                            </select>
                        </label>
                        <label style="font-size:12px; color:var(--text-muted);">Euro Barca (€)
                            <input type="number" id="q-boat-price" value="${offers[0] ? offers[0].base_price_eur : 450}" min="50" step="10" style="width:100%; margin-top:4px; padding:10px; border-radius:10px; background:rgba(0,0,0,0.3); border:1px solid var(--border); color:var(--text-main);">
                        </label>
                        <label style="font-size:12px; color:var(--text-muted);">🍽️ Abbinamento Ristorante sul Mare (Upsell)
                            <select id="q-restaurant-id" style="width:100%; margin-top:4px; padding:10px; border-radius:10px; background:rgba(0,0,0,0.3); border:1px solid var(--border); color:var(--text-main);">
                                <option value="">Nessun ristorante abbinato</option>
                                ${restaurants.map((r, idx) => `<option value="${r.id}" ${idx === 0 ? 'selected' : ''}>${escapeHTML(r.name)} (${escapeHTML(r.location)})</option>`).join('')}
                            </select>
                        </label>
                        <label style="font-size:12px; color:var(--text-muted);">🏡 Abbinamento Casa Vacanza (Ale / Cosimo)
                            <select id="q-stay-id" onchange="document.getElementById('q-stay-nights').value = this.value ? 2 : 0" style="width:100%; margin-top:4px; padding:10px; border-radius:10px; background:rgba(0,0,0,0.3); border:1px solid var(--border); color:var(--text-main);">
                                <option value="">Solo Giro in Barca</option>
                                ${stays.map(s => `<option value="${s.id}">${escapeHTML(s.name)} (€${s.avg_spend_eur}/notte)</option>`).join('')}
                            </select>
                        </label>
                        <label style="font-size:12px; color:var(--text-muted);">Notti Soggiorno
                            <input type="number" id="q-stay-nights" value="0" min="0" max="30" style="width:100%; margin-top:4px; padding:10px; border-radius:10px; background:rgba(0,0,0,0.3); border:1px solid var(--border); color:var(--text-main);">
                        </label>
                        <label style="font-size:12px; color:var(--text-muted);">🤝 Partner / Canale Provenienza
                            <select id="q-portal-slug" style="width:100%; margin-top:4px; padding:10px; border-radius:10px; background:rgba(0,0,0,0.3); border:1px solid var(--border); color:var(--text-main);">
                                <option value="">Vendita Diretta (0% comm. passiva)</option>
                                ${portals.map(p => `<option value="${escapeHTML(p.slug)}">${escapeHTML(p.partner_name)} (${p.commission_pct}%)</option>`).join('')}
                            </select>
                        </label>
                    </div>

                    <div style="display:flex; justify-content:flex-end; gap:12px;">
                        <button type="submit" class="btn btn-primary" id="q-submit-btn" style="padding:14px 28px; font-size:15px;">
                            <i class="fa-solid fa-paper-plane"></i> Genera Link Offerta (/offer/…) &amp; Messaggio WhatsApp
                        </button>
                    </div>
                </form>

                <div id="q-created-result" style="display:none; margin-top:20px; padding:20px; border-radius:16px; background:rgba(74,222,128,0.08); border:1px solid rgba(74,222,128,0.35);"></div>
            </div>

            <!-- Active Quotes & Bookings List -->
            <div class="card card-full">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:18px;">
                    <div>
                        <h3 style="font-family:'Outfit'; font-size:20px;">Giri in Barca, Preventivi &amp; Tavoli Ristorante</h3>
                        <p style="font-size:12px; color:var(--text-muted);">Conferma i giri in barca e invia in 1 click la prenotazione tavolo al ristorante partner.</p>
                    </div>
                </div>
                ${bookings.length ? `
                <div style="display:flex; flex-direction:column; gap:12px;">
                    ${bookings.map(b => `
                        <div class="lead-row" style="padding:18px; border-radius:14px; background:rgba(255,255,255,0.02); border:1px solid var(--border); display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:14px;">
                            <div style="flex:1; min-width:240px;">
                                <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:6px;">
                                    <strong style="font-size:16px; font-family:'Outfit';">${escapeHTML(b.customer_name)}</strong>
                                    ${statusBadge(b.status)}
                                    <span class="badge" style="background:rgba(197,160,89,0.15); color:var(--accent); font-size:10px;">
                                        ${b.assigned_operator === 'gabri' ? '🛥️ Gabri (≤6 pax)' : '⛴️ Massi (Gruppi)'} · ${b.guests_count} pax
                                    </span>
                                    ${b.event_date ? `<span style="font-size:12px; color:var(--text-muted);"><i class="fa-regular fa-calendar"></i> ${escapeHTML(b.event_date)}</span>` : ''}
                                </div>
                                <div style="font-size:13px; color:var(--text-main); margin-bottom:4px;">
                                    ${escapeHTML(b.offer_title)} — <strong style="color:var(--accent);">€${b.total_price_eur}</strong>
                                    ${b.source_portal_slug ? ` <span style="font-size:11px; color:var(--text-muted);">· via Partner: ${escapeHTML(b.source_portal_slug)} (comm. €${b.partner_commission_eur})</span>` : ''}
                                </div>
                                <div style="display:flex; gap:12px; flex-wrap:wrap; font-size:12px; color:var(--text-muted);">
                                    ${b.restaurant_name ? `<span style="color:#4ade80;"><i class="fa-solid fa-utensils"></i> ${escapeHTML(b.restaurant_name)} (${b.restaurant_covers} coperti · Fee €${b.restaurant_fee_eur} · Stato tavolo: <b>${escapeHTML(b.restaurant_status)}</b>)</span>` : ''}
                                    ${b.stay_name ? `<span style="color:#67c5b8;"><i class="fa-solid fa-house"></i> ${escapeHTML(b.stay_name)} (${b.stay_nights} notti · Fee €${b.stay_fee_eur})</span>` : ''}
                                </div>
                            </div>
                            <div style="display:flex; gap:8px; flex-wrap:wrap; align-items:center;">
                                <a href="/offer/${escapeHTML(b.public_token)}" target="_blank" class="btn btn-ghost" style="padding:8px 12px; font-size:12px; text-decoration:none;">
                                    <i class="fa-solid fa-arrow-up-right-from-square"></i> Pagina Offerta
                                </a>
                                <button type="button" class="btn btn-ghost" style="padding:8px 12px; font-size:12px;" onclick="copyToClipboard(${JSON.stringify(b.custom_pitch || '').replace(/</g,'\\u003c')})">
                                    <i class="fa-brands fa-whatsapp" style="color:#4ade80;"></i> Copia Pitch
                                </button>
                                ${b.restaurant_venue_id ? `
                                <button type="button" class="btn btn-ghost" style="padding:8px 12px; font-size:12px; border-color:#4ade80; color:#4ade80;" onclick="sendRestaurantWhatsApp(${b.id})">
                                    <i class="fa-solid fa-utensils"></i> Prenota Ristorante
                                </button>` : ''}
                                ${b.status !== 'confirmed' ? `
                                <button type="button" class="btn btn-primary" style="padding:8px 14px; font-size:12px;" onclick="changeQuoteStatus(${b.id}, 'confirmed')">
                                    <i class="fa-solid fa-check"></i> Conferma Barca
                                </button>` : `
                                <button type="button" class="btn btn-ghost" style="padding:8px 12px; font-size:12px;" onclick="changeQuoteStatus(${b.id}, 'completed')">
                                    Segna Completato
                                </button>`}
                            </div>
                        </div>
                    `).join('')}
                </div>` : `<div style="text-align:center; padding:40px; color:var(--text-muted);">Nessun preventivo ancora creato. Usa il modulo qui sopra per generare la tua prima proposta Barca + Ristorante!</div>`}
            </div>
        `;
    }

    window.onQuoteOfferSelect = (offerId) => {
        const offer = (commerceCache.offers || []).find(o => String(o.id) === String(offerId));
        if (offer) {
            const priceInput = document.getElementById('q-boat-price');
            if (priceInput) priceInput.value = offer.base_price_eur;
        }
    };

    window.onQuoteGuestsChange = (val) => {
        const guests = parseInt(val || '2', 10);
        const targetOp = guests <= 6 ? 'gabri' : 'massi';
        const matching = (commerceCache.offers || []).find(o => o.operator_slug === targetOp && guests >= o.min_guests && guests <= o.max_guests)
            || (commerceCache.offers || []).find(o => o.operator_slug === targetOp);
        const sel = document.getElementById('q-offer-id');
        if (matching && sel) {
            sel.value = matching.id;
            onQuoteOfferSelect(matching.id);
        }
        checkQuoteAvailability();
    };

    window.checkQuoteAvailability = async () => {
        const guests = parseInt(document.getElementById('q-guests')?.value || '2', 10);
        const event_date = document.getElementById('q-date')?.value || '';
        const time_slot = document.getElementById('q-slot')?.value || 'full_day';
        const banner = document.getElementById('q-dispatch-banner');
        if (!banner) return;
        try {
            const r = await apiFetch('/api/commerce/dispatch', {
                method: 'POST',
                body: JSON.stringify({ guests_count: guests, event_date, time_slot })
            }, false);
            const d = r.dispatch || {};
            banner.style.display = 'block';
            if (d.conflict) {
                banner.style.borderLeftColor = '#facc15';
                banner.innerHTML = `<i class="fa-solid fa-triangle-exclamation" style="color:#facc15;"></i> <strong>${escapeHTML(d.alternative_note)}</strong>`;
            } else {
                banner.style.borderLeftColor = '#4ade80';
                banner.innerHTML = `<i class="fa-solid fa-circle-check" style="color:#4ade80;"></i> <strong>Smistamento ottimale (${guests} pax):</strong> ${escapeHTML(d.operator_label)} — Disponibile${d.recommended_restaurant ? ` · Ristorante consigliato: <b>${escapeHTML(d.recommended_restaurant.name)}</b>` : ''}.`;
            }
        } catch {}
    };

    window.parseAndDispatchQuote = async () => {
        const raw = document.getElementById('q-raw-paste')?.value || '';
        if (!raw.trim()) {
            showToast('Incolla prima il messaggio WhatsApp o l\'email del cliente.');
            return;
        }
        try {
            const r = await apiFetch('/api/commerce/dispatch', {
                method: 'POST',
                body: JSON.stringify({ raw_text: raw })
            });
            const p = r.parsed || {};
            const d = r.dispatch || {};
            if (p.guests_count) document.getElementById('q-guests').value = p.guests_count;
            if (p.customer_language) document.getElementById('q-lang').value = p.customer_language;
            if (p.time_slot) document.getElementById('q-slot').value = p.time_slot;
            if (p.event_date) document.getElementById('q-date').value = p.event_date;
            if (d.recommended_offer) {
                document.getElementById('q-offer-id').value = d.recommended_offer.id;
                document.getElementById('q-boat-price').value = d.recommended_offer.base_price_eur;
            }
            if (d.recommended_restaurant) {
                document.getElementById('q-restaurant-id').value = d.recommended_restaurant.id;
            }
            if (d.recommended_stay && p.wants_stay) {
                document.getElementById('q-stay-id').value = d.recommended_stay.id;
                document.getElementById('q-stay-nights').value = p.stay_nights || 2;
            }
            checkQuoteAvailability();
            showToast(`Richiesta analizzata: ${p.guests_count || 2} ospiti → ${d.operator_label}`);
        } catch {
            showToast('Errore nell\'analisi della richiesta.');
        }
    };

    window.submitQuickQuote = async (e) => {
        e.preventDefault();
        const btn = document.getElementById('q-submit-btn');
        setBusy(btn, true);
        try {
            const payload = {
                customer_name: document.getElementById('q-customer-name').value,
                customer_contact: document.getElementById('q-customer-contact').value,
                guests_count: parseInt(document.getElementById('q-guests').value || '2', 10),
                event_date: document.getElementById('q-date').value,
                time_slot: document.getElementById('q-slot').value,
                customer_language: document.getElementById('q-lang').value,
                offer_id: parseInt(document.getElementById('q-offer-id').value, 10) || null,
                boat_price_eur: parseInt(document.getElementById('q-boat-price').value || '450', 10),
                restaurant_venue_id: parseInt(document.getElementById('q-restaurant-id').value, 10) || null,
                stay_venue_id: parseInt(document.getElementById('q-stay-id').value, 10) || null,
                stay_nights: parseInt(document.getElementById('q-stay-nights').value || '0', 10),
                source_portal_slug: document.getElementById('q-portal-slug').value || ''
            };
            const created = await apiFetch('/api/commerce/quotes', {
                method: 'POST',
                body: JSON.stringify(payload)
            });
            await loadSalesView();
            const resBox = document.getElementById('q-created-result');
            if (resBox) {
                resBox.style.display = 'block';
                const cleanPhone = (created.customer_contact || '').replace(/[^\d]/g, '');
                const waUrl = cleanPhone
                    ? `https://wa.me/${cleanPhone}?text=${encodeURIComponent(created.custom_pitch)}`
                    : `https://wa.me/?text=${encodeURIComponent(created.custom_pitch)}`;
                resBox.innerHTML = `
                    <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; margin-bottom:10px;">
                        <strong style="color:#4ade80; font-size:16px;"><i class="fa-solid fa-circle-check"></i> Offerta Pronta: ${escapeHTML(created.public_token)} (€${created.total_price_eur})</strong>
                        <div style="display:flex; gap:8px;">
                            <a href="/offer/${escapeHTML(created.public_token)}" target="_blank" class="btn btn-ghost" style="padding:6px 12px; font-size:12px; text-decoration:none;">Apri Link Offerta</a>
                            <button type="button" class="btn btn-ghost" style="padding:6px 12px; font-size:12px;" onclick="copyToClipboard(${JSON.stringify(created.custom_pitch).replace(/</g,'\\u003c')})"><i class="fa-solid fa-copy"></i> Copia Testo</button>
                            <a href="${waUrl}" target="_blank" class="btn btn-primary" style="padding:6px 14px; font-size:12px; text-decoration:none;"><i class="fa-brands fa-whatsapp"></i> Invia su WhatsApp</a>
                        </div>
                    </div>
                    <pre style="white-space:pre-wrap; font-size:12px; color:var(--text-main); background:rgba(0,0,0,0.3); padding:12px; border-radius:10px; font-family:inherit;">${escapeHTML(created.custom_pitch)}</pre>
                `;
            }
            showToast('Offerta generata con successo!');
        } catch {
            showToast('Errore creazione preventivo.');
        } finally {
            setBusy(btn, false);
        }
    };

    window.changeQuoteStatus = async (bid, status) => {
        try {
            await apiFetch(`/api/commerce/quotes/${bid}/status`, {
                method: 'POST',
                body: JSON.stringify({ status })
            });
            showToast('Stato prenotazione aggiornato!');
            loadSalesView();
        } catch {
            showToast('Errore aggiornamento stato.');
        }
    };

    window.sendRestaurantWhatsApp = async (bid) => {
        try {
            const data = await apiFetch(`/api/commerce/quotes/${bid}/restaurant-whatsapp`);
            await apiFetch(`/api/commerce/quotes/${bid}/status`, {
                method: 'POST',
                body: JSON.stringify({ restaurant_status: 'requested' })
            });
            window.open(data.whatsapp_url, '_blank');
            loadSalesView();
        } catch {
            showToast('Errore apertura WhatsApp ristorante.');
        }
    };

    // ─── LESLIE 2.0: RISTORANTI PARTNER, CASE VACANZA & PORTALI QR (/p/<slug>) ───
    async function loadVenuesView() {
        const el = document.getElementById('venues-view-content');
        if (!el) return;
        try {
            const data = await apiFetch('/api/commerce/overview', {}, false);
            commerceCache = data;
            renderVenuesAndPortals(el, data);
        } catch {
            el.innerHTML = `<div class="card" style="padding:40px; text-align:center; color:var(--text-muted);">Errore caricamento Ristoranti e Portali.</div>`;
        }
    }
    window.loadVenuesView = loadVenuesView;

    function renderVenuesAndPortals(el, data) {
        const venues = data.venues || [];
        const portals = data.portals || [];
        const origin = window.location.origin;

        el.innerHTML = `
            <div style="display:grid; grid-template-columns:1fr; gap:28px;">
                <!-- Ristoranti & Case Vacanza -->
                <div class="card card-full">
                    <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:12px; margin-bottom:20px;">
                        <div>
                            <span class="badge badge-gold"><i class="fa-solid fa-utensils"></i> Cross-Sell &amp; Referral Network</span>
                            <h2 style="font-family:'Outfit'; font-size:22px; margin-top:6px;">Ristoranti sul Mare &amp; Case Vacanza Partner</h2>
                            <p style="font-size:13px; color:var(--text-muted);">Ogni volta che abbini un ristorante o una casa vacanza a un giro in barca, Leslie calcola coperti, notti e fee maturate.</p>
                        </div>
                        <button type="button" class="btn btn-primary" onclick="document.getElementById('new-venue-box').style.display = document.getElementById('new-venue-box').style.display === 'none' ? 'block' : 'none'">
                            <i class="fa-solid fa-plus"></i> Aggiungi Ristorante o Struttura
                        </button>
                    </div>

                    <form id="new-venue-box" onsubmit="submitNewVenue(event)" style="display:none; margin-bottom:22px; padding:18px; border-radius:14px; background:rgba(0,0,0,0.3); border:1px solid var(--border);">
                        <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:12px; margin-bottom:12px;">
                            <input type="text" id="v-name" required placeholder="Nome Ristorante / Casa Vacanza *" style="padding:10px; border-radius:8px; background:rgba(0,0,0,0.4); border:1px solid var(--border); color:var(--text-main);">
                            <select id="v-type" style="padding:10px; border-radius:8px; background:rgba(0,0,0,0.4); border:1px solid var(--border); color:var(--text-main);">
                                <option value="restaurant">🍽️ Ristorante sul Mare</option>
                                <option value="holiday_home">🏡 Casa Vacanza / Villa</option>
                                <option value="beach_club">🏖️ Beach Club</option>
                            </select>
                            <input type="text" id="v-location" placeholder="Località (es. San Fruttuoso)" value="San Fruttuoso" style="padding:10px; border-radius:8px; background:rgba(0,0,0,0.4); border:1px solid var(--border); color:var(--text-main);">
                            <input type="text" id="v-phone" placeholder="WhatsApp (+39...)" style="padding:10px; border-radius:8px; background:rgba(0,0,0,0.4); border:1px solid var(--border); color:var(--text-main);">
                            <select id="v-comm-model" style="padding:10px; border-radius:8px; background:rgba(0,0,0,0.4); border:1px solid var(--border); color:var(--text-main);">
                                <option value="per_cover">€ fissi a Coperto inviato</option>
                                <option value="pct_bill">% sul Conto / Soggiorno</option>
                            </select>
                            <input type="number" id="v-comm-val" value="10" min="0" step="1" placeholder="Valore (€ o %)" style="padding:10px; border-radius:8px; background:rgba(0,0,0,0.4); border:1px solid var(--border); color:var(--text-main);">
                        </div>
                        <button type="submit" class="btn btn-primary" style="padding:10px 20px; font-size:13px;"><i class="fa-solid fa-check"></i> Salva nel Catalogo</button>
                    </form>

                    <div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(310px, 1fr)); gap:16px;">
                        ${venues.map(v => {
                            const st = v.stats || {};
                            const isRest = v.venue_type === 'restaurant';
                            return `
                            <div class="card" style="padding:18px; background:rgba(255,255,255,0.02); border:1px solid var(--border); display:flex; flex-direction:column; justify-content:space-between; gap:12px;">
                                <div>
                                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                                        <span style="font-size:11px; color:${isRest ? '#4ade80' : '#67c5b8'}; font-weight:700; text-transform:uppercase;">
                                            ${isRest ? '🍽️ Ristorante Partner' : '🏡 Casa Vacanza'} · ${escapeHTML(v.location)}
                                        </span>
                                        ${v.boat_access ? '<span class="badge" style="font-size:10px; background:rgba(197,160,89,0.15); color:var(--accent);">⚓ Sbarco Barca</span>' : ''}
                                    </div>
                                    <h3 style="font-family:'Outfit'; font-size:18px; margin-bottom:6px;">${escapeHTML(v.name)}</h3>
                                    <p style="font-size:12px; color:var(--text-muted); margin-bottom:10px;">${escapeHTML(v.specialty_note || '')}</p>
                                </div>
                                <div style="padding:12px; border-radius:10px; background:rgba(0,0,0,0.25); display:flex; justify-content:space-between; align-items:center; font-size:12px;">
                                    <div>
                                        <span style="color:var(--text-muted);">${isRest ? 'Coperti mandati:' : 'Notti mandate:'}</span>
                                        <strong style="color:var(--text-main); font-size:15px; margin-left:4px;">${st.covers_or_nights || 0}</strong>
                                    </div>
                                    <div>
                                        <span style="color:var(--text-muted);">Fee maturata:</span>
                                        <strong style="color:#4ade80; font-size:15px; margin-left:4px;">€${(st.fee_eur || 0).toFixed(0)}</strong>
                                    </div>
                                </div>
                            </div>`;
                        }).join('')}
                    </div>
                </div>

                <!-- Portali Partner & Guest Pass QR (/p/<slug>) -->
                <div class="card card-full">
                    <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:12px; margin-bottom:20px;">
                        <div>
                            <span class="badge badge-gold"><i class="fa-solid fa-qrcode"></i> Portali Partner B2B2C &amp; Guest Pass</span>
                            <h2 style="font-family:'Outfit'; font-size:22px; margin-top:6px;">Link Prenotazione &amp; QR Code per Case Vacanza, Hotel e Concierge</h2>
                            <p style="font-size:13px; color:var(--text-muted);">Ogni partner ha la sua pagina dedicata <code>/p/&lt;slug&gt;</code>: gli ospiti scansionano il QR in appartamento o in hotel, richiedono il giro in barca e Leslie traccia la commissione.</p>
                        </div>
                        <button type="button" class="btn btn-primary" onclick="document.getElementById('new-portal-box').style.display = document.getElementById('new-portal-box').style.display === 'none' ? 'block' : 'none'">
                            <i class="fa-solid fa-plus"></i> Nuovo Portale Partner
                        </button>
                    </div>

                    <form id="new-portal-box" onsubmit="submitNewPortal(event)" style="display:none; margin-bottom:22px; padding:18px; border-radius:14px; background:rgba(0,0,0,0.3); border:1px solid var(--border);">
                        <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:12px; margin-bottom:12px;">
                            <input type="text" id="p-new-name" required placeholder="Nome Partner (es. Hotel Miramare / Villa Blu) *" style="padding:10px; border-radius:8px; background:rgba(0,0,0,0.4); border:1px solid var(--border); color:var(--text-main);">
                            <select id="p-new-type" style="padding:10px; border-radius:8px; background:rgba(0,0,0,0.4); border:1px solid var(--border); color:var(--text-main);">
                                <option value="accommodation">🏡 Casa Vacanza / Host Airbnb</option>
                                <option value="hotel_concierge">🏨 Hotel &amp; Concierge</option>
                                <option value="wedding_planner">💍 Wedding &amp; Event Planner</option>
                            </select>
                            <input type="number" id="p-new-comm" value="12" min="0" max="40" placeholder="Commissione Partner %" style="padding:10px; border-radius:8px; background:rgba(0,0,0,0.4); border:1px solid var(--border); color:var(--text-main);">
                        </div>
                        <button type="submit" class="btn btn-primary" style="padding:10px 20px; font-size:13px;"><i class="fa-solid fa-check"></i> Crea Portale &amp; QR</button>
                    </form>

                    <div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(320px, 1fr)); gap:16px;">
                        ${portals.map(p => {
                            const fullUrl = `${origin}/p/${p.slug}`;
                            const qrImg = `https://api.qrserver.com/v1/create-qr-code/?size=160x160&data=${encodeURIComponent(fullUrl)}`;
                            return `
                            <div class="card" style="padding:18px; background:rgba(255,255,255,0.02); border:1px solid var(--border); display:flex; gap:14px; align-items:center;">
                                <img src="${qrImg}" alt="QR ${escapeHTML(p.slug)}" style="width:76px; height:76px; border-radius:10px; background:#fff; padding:4px; flex-shrink:0;">
                                <div style="flex:1; min-width:0;">
                                    <div style="font-size:11px; color:var(--accent); font-weight:700;">Comm. Partner: ${p.commission_pct}% · ${p.views_count || 0} visite · ${p.requests_count || 0} richieste</div>
                                    <h4 style="font-family:'Outfit'; font-size:16px; margin:3px 0;">${escapeHTML(p.partner_name)}</h4>
                                    <div style="font-size:11px; color:var(--text-muted); margin-bottom:10px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">/p/${escapeHTML(p.slug)}</div>
                                    <div style="display:flex; gap:8px; flex-wrap:wrap;">
                                        <a href="/p/${escapeHTML(p.slug)}" target="_blank" class="btn btn-primary" style="padding:6px 12px; font-size:11px; text-decoration:none;">Apri Portale</a>
                                        <button type="button" class="btn btn-ghost" style="padding:6px 10px; font-size:11px;" onclick="copyToClipboard('${escapeHTML(fullUrl)}')"><i class="fa-solid fa-copy"></i> Copia Link</button>
                                    </div>
                                </div>
                            </div>`;
                        }).join('')}
                    </div>
                </div>
            </div>
        `;
    }

    window.submitNewVenue = async (e) => {
        e.preventDefault();
        try {
            await apiFetch('/api/commerce/venues', {
                method: 'POST',
                body: JSON.stringify({
                    name: document.getElementById('v-name').value,
                    venue_type: document.getElementById('v-type').value,
                    location: document.getElementById('v-location').value,
                    whatsapp_phone: document.getElementById('v-phone').value,
                    commission_model: document.getElementById('v-comm-model').value,
                    commission_value: parseFloat(document.getElementById('v-comm-val').value || '10')
                })
            });
            showToast('Struttura aggiunta al catalogo!');
            loadVenuesView();
        } catch {
            showToast('Errore salvataggio struttura.');
        }
    };

    window.submitNewPortal = async (e) => {
        e.preventDefault();
        try {
            await apiFetch('/api/commerce/portals', {
                method: 'POST',
                body: JSON.stringify({
                    partner_name: document.getElementById('p-new-name').value,
                    partner_type: document.getElementById('p-new-type').value,
                    commission_pct: parseInt(document.getElementById('p-new-comm').value || '12', 10)
                })
            });
            showToast('Portale Partner creato!');
            loadVenuesView();
        } catch {
            showToast('Errore creazione portale.');
        }
    };

    window.createPortalFromLead = async (leadId, companyName, phone, email) => {
        try {
            const created = await apiFetch('/api/commerce/portals', {
                method: 'POST',
                body: JSON.stringify({
                    partner_name: companyName,
                    partner_type: 'hotel_concierge',
                    lead_id: leadId,
                    commission_pct: 12,
                    contact_whatsapp: phone || '',
                    contact_email: email || ''
                })
            });
            copyToClipboard(created.public_url);
            showToast(`Portale attivato e link copiato: ${created.public_url}`);
        } catch {
            showToast('Errore creazione portale dal contatto.');
        }
    };

    // ─── UTILS ───
    function showToast(msg) {
        elements.toast.textContent = msg;
        elements.toast.style.display = 'block';
        setTimeout(() => { elements.toast.style.display = 'none'; }, 4000);
    }

});
