document.addEventListener('DOMContentLoaded', () => {
    // State
    let isRunning = false;
    let isDryRun = true;

    // Elements
    const engineStatusPill = document.getElementById('engineStatusPill');
    const engineStatusText = document.getElementById('engineStatusText');
    const modeStatusPill = document.getElementById('modeStatusPill');
    const modeStatusText = document.getElementById('modeStatusText');
    const activityTicker = document.getElementById('activityTicker');

    const btnStart = document.getElementById('btnStartEngine');
    const btnStop = document.getElementById('btnStopEngine');
    const btnLoginBrowser = document.getElementById('btnLoginBrowser');

    // Stats
    const statTotalScanned = document.getElementById('statTotalScanned');
    const statCommentsToday = document.getElementById('statCommentsToday');
    const statLiveComments = document.getElementById('statLiveComments');
    const statDryComments = document.getElementById('statDryComments');
    const statQuotaRemaining = document.getElementById('statQuotaRemaining');

    // Tabs
    const tabBtns = document.querySelectorAll('.tab-btn');
    const tabPanes = document.querySelectorAll('.tab-pane');

    // Feed & Table
    const feedTableBody = document.getElementById('feedTableBody');
    const btnRefreshFeed = document.getElementById('btnRefreshFeed');

    // Simulator
    const simAuthor = document.getElementById('simAuthor');
    const simKeyword = document.getElementById('simKeyword');
    const simTweetText = document.getElementById('simTweetText');
    const btnRunSimulation = document.getElementById('btnRunSimulation');
    const simOutputText = document.getElementById('simOutputText');
    const simCharCount = document.getElementById('simCharCount');
    const simEngineType = document.getElementById('simEngineType');

    // Settings
    const btnSaveSettings = document.getElementById('btnSaveSettings');
    const cfgCompanyName = document.getElementById('cfgCompanyName');
    const cfgCompanyOneLiner = document.getElementById('cfgCompanyOneLiner');
    const cfgCompanyCTA = document.getElementById('cfgCompanyCTA');
    const cfgCompanyUrl = document.getElementById('cfgCompanyUrl');
    const cfgOpenAIKey = document.getElementById('cfgOpenAIKey');
    const cfgTargetKeywords = document.getElementById('cfgTargetKeywords');
    const cfgNegativeKeywords = document.getElementById('cfgNegativeKeywords');
    const cfgDryRun = document.getElementById('cfgDryRun');
    const cfgMinDelay = document.getElementById('cfgMinDelay');
    const cfgMaxDelay = document.getElementById('cfgMaxDelay');
    const cfgDailyLimit = document.getElementById('cfgDailyLimit');
    const cfgAttachImage = document.getElementById('cfgAttachImage');
    const cfgMediaImagePath = document.getElementById('cfgMediaImagePath');
    const btnUploadMediaTrigger = document.getElementById('btnUploadMediaTrigger');
    const mediaFileInput = document.getElementById('mediaFileInput');
    const mediaCount = document.getElementById('mediaCount');

    // Console
    const consoleOutput = document.getElementById('consoleOutput');
    const btnClearLogs = document.getElementById('btnClearLogs');
    const toast = document.getElementById('toast');

    // Tab Navigation
    tabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            tabBtns.forEach(b => b.classList.remove('active'));
            tabPanes.forEach(p => p.classList.remove('active'));

            btn.classList.add('active');
            const targetPane = document.getElementById(`tab-${btn.dataset.tab}`);
            if (targetPane) targetPane.classList.add('active');
        });
    });

    // Toast Notification
    function showToast(msg, duration = 3000) {
        toast.textContent = msg;
        toast.classList.add('show');
        setTimeout(() => toast.classList.remove('show'), duration);
    }

    // Status Fetching
    async function fetchStatus() {
        try {
            const res = await fetch('/api/status');
            if (!res.ok) return;
            const data = await res.json();

            // Update Engine Status
            isRunning = data.engine.state === 'running';
            isDryRun = data.config.dry_run;

            if (isRunning) {
                engineStatusPill.className = 'status-pill running';
                engineStatusText.textContent = 'ONLINE';
                btnStart.disabled = true;
                btnStop.disabled = false;
            } else {
                engineStatusPill.className = 'status-pill stopped';
                engineStatusText.textContent = 'STOPPED';
                btnStart.disabled = false;
                btnStop.disabled = true;
            }

            // Mode Pill
            if (isDryRun) {
                modeStatusPill.className = 'mode-pill dry';
                modeStatusText.textContent = 'DRY RUN';
            } else {
                modeStatusPill.className = 'mode-pill live';
                modeStatusText.textContent = 'LIVE ACTIVE';
            }

            // Activity Ticker
            activityTicker.textContent = data.engine.activity || 'Idle';

            // Metrics
            statTotalScanned.textContent = data.stats.total_scanned.toLocaleString();
            statCommentsToday.textContent = data.stats.comments_today.toLocaleString();
            statLiveComments.textContent = data.stats.live_comments.toLocaleString();
            statDryComments.textContent = data.stats.dry_comments.toLocaleString();
            statQuotaRemaining.textContent = `Quota: ${data.stats.comments_today} / ${data.config.daily_comment_limit} daily`;

        } catch (err) {
            engineStatusText.textContent = 'OFFLINE';
            engineStatusPill.className = 'status-pill stopped';
        }
    }

    // Populate Settings
    async function loadSettings() {
        try {
            const res = await fetch('/api/status');
            if (!res.ok) return;
            const data = await res.json();
            const cfg = data.config;

            cfgCompanyName.value = cfg.company_name || '';
            cfgCompanyOneLiner.value = cfg.company_one_liner || '';
            cfgCompanyCTA.value = cfg.company_cta || '';
            cfgCompanyUrl.value = cfg.company_url || '';
            cfgTargetKeywords.value = cfg.target_keywords || '';
            cfgNegativeKeywords.value = cfg.negative_keywords || '';
            cfgDryRun.checked = cfg.dry_run;
            cfgMinDelay.value = cfg.min_delay_seconds || 90;
            cfgMaxDelay.value = cfg.max_delay_seconds || 240;
            cfgDailyLimit.value = cfg.daily_comment_limit || 25;
            if (cfgAttachImage) cfgAttachImage.checked = !!cfg.attach_image;
            if (cfgMediaImagePath) cfgMediaImagePath.value = cfg.media_image_path || './media';
            fetchMediaList();

            if (cfg.has_openai_key) {
                cfgOpenAIKey.placeholder = '•••••••••••••••• (API Key Active)';
            }
        } catch (e) {
            console.error('Failed to load settings', e);
        }
    }

    // Save Settings
    btnSaveSettings.addEventListener('click', async (e) => {
        e.preventDefault();
        const payload = {
            company_name: cfgCompanyName.value.trim(),
            company_one_liner: cfgCompanyOneLiner.value.trim(),
            company_cta: cfgCompanyCTA.value.trim(),
            company_url: cfgCompanyUrl.value.trim(),
            target_keywords: cfgTargetKeywords.value.trim(),
            negative_keywords: cfgNegativeKeywords.value.trim(),
            dry_run: cfgDryRun.checked,
            min_delay_seconds: parseInt(cfgMinDelay.value, 10),
            max_delay_seconds: parseInt(cfgMaxDelay.value, 10),
            daily_comment_limit: parseInt(cfgDailyLimit.value, 10),
            attach_image: cfgAttachImage.checked,
            media_image_path: cfgMediaImagePath.value.trim() || './media'
        };

        if (cfgOpenAIKey.value.trim()) {
            payload.openai_api_key = cfgOpenAIKey.value.trim();
        }

        try {
            const res = await fetch('/api/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (res.ok) {
                showToast('Settings successfully updated!');
                fetchStatus();
                fetchMediaList();
            } else {
                showToast('Error saving settings.');
            }
        } catch (err) {
            showToast('Network error while saving settings.');
        }
    });

    async function fetchMediaList() {
        try {
            const res = await fetch('/api/media');
            if (res.ok) {
                const data = await res.json();
                const count = (data.media || []).length;
                if (mediaCount) {
                    if (count === 0) {
                        mediaCount.textContent = 'No images found in folder';
                    } else if (data.today_image && data.today_image.filename) {
                        mediaCount.textContent = `${data.today_image.filename} (Active for today | ${count} image${count === 1 ? '' : 's'} in pool)`;
                    } else {
                        const names = data.media.map(m => m.filename).join(', ');
                        mediaCount.textContent = `${count} image${count === 1 ? '' : 's'} (${names})`;
                    }
                }
            }
        } catch (e) {
            console.error('Failed to fetch media list', e);
        }
    }

    if (btnUploadMediaTrigger && mediaFileInput) {
        btnUploadMediaTrigger.addEventListener('click', () => mediaFileInput.click());
        mediaFileInput.addEventListener('change', async () => {
            if (!mediaFileInput.files || mediaFileInput.files.length === 0) return;
            const file = mediaFileInput.files[0];
            const formData = new FormData();
            formData.append('file', file);
            try {
                showToast(`Uploading ${file.name}...`);
                const res = await fetch('/api/media/upload', {
                    method: 'POST',
                    body: formData
                });
                if (res.ok) {
                    showToast(`Image uploaded: ${file.name}`);
                    fetchMediaList();
                } else {
                    showToast('Failed to upload image.');
                }
            } catch (e) {
                showToast('Upload error.');
            }
        });
    }

    // Start / Stop Automation Controls
    btnStart.addEventListener('click', async () => {
        try {
            const res = await fetch('/api/control/start', { method: 'POST' });
            if (res.ok) {
                showToast('Automation engine started!');
                fetchStatus();
                fetchLogs();
            }
        } catch (err) {
            showToast('Failed to start automation.');
        }
    });

    btnStop.addEventListener('click', async () => {
        try {
            const res = await fetch('/api/control/stop', { method: 'POST' });
            if (res.ok) {
                showToast('Automation engine stopped.');
                fetchStatus();
                fetchLogs();
            }
        } catch (err) {
            showToast('Failed to stop automation.');
        }
    });

    // Visible Browser for Login
    btnLoginBrowser.addEventListener('click', async () => {
        showToast('Opening visible browser for X login...');
        try {
            const res = await fetch('/api/browser/open-login', { method: 'POST' });
            const data = await res.json();
            if (data.status === 'opened') {
                showToast('Browser opened! Please log in to X in the browser window.');
            } else {
                showToast(`Error: ${data.message}`);
            }
        } catch (err) {
            showToast('Failed to launch browser.');
        }
    });

    // Fetch Feed & Comments
    async function fetchFeed() {
        try {
            const res = await fetch('/api/comments');
            if (!res.ok) return;
            const data = await res.json();
            const comments = data.comments;

            if (!comments || comments.length === 0) {
                feedTableBody.innerHTML = `<tr><td colspan="6" class="empty-state">No comments recorded yet. Start automation or run a test simulation!</td></tr>`;
                return;
            }

            feedTableBody.innerHTML = comments.map(c => {
                let badgeClass = 'badge-dry';
                let badgeText = 'DRY RUN';
                let titleAttr = '';
                if (c.mode === 'live' && c.status === 'success') {
                    badgeClass = 'badge-success';
                    badgeText = 'LIVE SUCCESS';
                } else if (c.status === 'restricted') {
                    badgeClass = 'badge-dry';
                    badgeText = 'REPLIES RESTRICTED';
                    titleAttr = c.error_message || 'Post author turned off or restricted replies';
                } else if (c.status === 'failed') {
                    badgeClass = 'badge-failed';
                    badgeText = c.error_message && c.error_message.toLowerCase().includes('suspend') ? 'SUSPENDED' : 'FAILED';
                    titleAttr = c.error_message || '';
                }

                const timeStr = new Date(c.posted_at + 'Z').toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
                const tweetUrl = c.tweet_url || `https://x.com/i/web/status/${c.tweet_id}`;
                const originalExcerpt = c.original_tweet ? (c.original_tweet.length > 80 ? c.original_tweet.slice(0, 80) + '...' : c.original_tweet) : 'Original tweet';
                const errSub = (c.status === 'failed' && c.error_message) ? `<div style="font-size: 0.72rem; color: #f87171; margin-top: 4px; line-height: 1.2;">${c.error_message.length > 45 ? c.error_message.slice(0, 45) + '...' : c.error_message}</div>` : '';

                return `
                    <tr>
                        <td style="min-width: 110px;">
                            <span class="badge ${badgeClass}" title="${titleAttr}" style="cursor: ${titleAttr ? 'help' : 'default'};">${badgeText}</span>
                            ${errSub}
                        </td>
                        <td>
                            <strong style="color: var(--accent-cyan);">@${c.author || 'user'}</strong>
                            <div style="font-size: 0.8rem; color: var(--text-muted); margin-top: 2px;">${originalExcerpt}</div>
                        </td>
                        <td style="max-width: 420px; line-height: 1.4;">${c.reply_text}</td>
                        <td><span style="font-family: var(--font-mono); font-size: 0.78rem; text-transform: uppercase;">${c.mode}</span></td>
                        <td style="color: var(--text-muted); font-size: 0.82rem;">${timeStr}</td>
                        <td>
                            <a href="${tweetUrl}" target="_blank" rel="noopener" class="btn btn-sm btn-outline" style="text-decoration: none;">
                                View Tweet
                            </a>
                        </td>
                    </tr>
                `;
            }).join('');
        } catch (err) {
            console.error('Failed to fetch feed', err);
        }
    }

    btnRefreshFeed.addEventListener('click', () => {
        fetchFeed();
        showToast('Feed refreshed');
    });

    // AI Simulation Generator
    btnRunSimulation.addEventListener('click', async () => {
        const text = simTweetText.value.trim();
        if (!text) {
            showToast('Please enter tweet text to test.');
            return;
        }

        btnRunSimulation.disabled = true;
        simOutputText.textContent = 'Formulating contextual comment...';

        try {
            const res = await fetch('/api/test/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    tweet_text: text,
                    author: simAuthor.value.trim() || 'crypto_trader',
                    keyword: simKeyword.value
                })
            });

            if (res.ok) {
                const data = await res.json();
                simOutputText.textContent = data.reply;
                simCharCount.textContent = `${data.length} / 280 characters`;
                simEngineType.textContent = data.mode === 'ai' ? 'ENGINE: OPENAI LLM' : 'ENGINE: SMART TEMPLATE';
            } else {
                simOutputText.textContent = 'Error generating simulation reply.';
            }
        } catch (e) {
            simOutputText.textContent = 'Network error during simulation.';
        } finally {
            btnRunSimulation.disabled = false;
        }
    });

    // Console Logs
    async function fetchLogs() {
        try {
            const res = await fetch('/api/logs');
            if (!res.ok) return;
            const data = await res.json();
            const logs = data.logs;

            if (logs && logs.length > 0) {
                consoleOutput.innerHTML = logs.map(l => {
                    const levelClass = l.level.toLowerCase();
                    const time = new Date(l.created_at + 'Z').toLocaleTimeString();
                    return `<div class="console-line ${levelClass}">[${time}] [${l.level}] ${l.message}</div>`;
                }).join('');
            }
        } catch (e) {
            console.error('Failed to fetch logs', e);
        }
    }

    btnClearLogs.addEventListener('click', () => {
        consoleOutput.innerHTML = `<div class="console-line info">[SYSTEM] Console view cleared.</div>`;
    });

    // Initialize
    loadSettings();
    fetchStatus();
    fetchFeed();
    fetchLogs();

    // Intervals
    setInterval(fetchStatus, 3000);
    setInterval(fetchFeed, 5000);
    setInterval(fetchLogs, 4000);
});
