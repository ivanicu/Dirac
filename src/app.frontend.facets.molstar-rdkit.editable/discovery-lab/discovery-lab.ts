const query = new URLSearchParams(location.search);
const apiBase = query.get('api') || `http://${location.hostname}:8901`;
const state = document.getElementById('backend-state');
const url = document.getElementById('backend-url');
if (url) url.textContent = apiBase;

void fetch(`${apiBase}/health`, { signal: AbortSignal.timeout(5000) }).then(response => {
    if (!response.ok) throw new Error(String(response.status));
    if (state) { state.textContent = '● BACKEND LINKED'; state.classList.add('live'); }
}).catch(() => {
    if (state) state.textContent = 'BACKEND UNAVAILABLE';
});
