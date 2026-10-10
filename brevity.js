(() => {
    "use strict";

    const TOKEN_KEY = "brevity.githubToken";
    const POLL_MS = 20000;
    const POLL_LIMIT_MS = 10 * 60 * 1000;

    const button = document.querySelector("[data-refresh-all]");
    const dialog = document.getElementById("refresh-dialog");
    if (!button || !dialog || typeof dialog.showModal !== "function") return;

    const form = dialog.querySelector("[data-refresh-form]");
    const tokenField = dialog.querySelector("[data-token-field]");
    const tokenInput = dialog.querySelector("#refresh-token");
    const errorEl = dialog.querySelector("[data-refresh-error]");
    const forgetButton = dialog.querySelector("[data-token-forget]");
    const confirmButton = dialog.querySelector("[data-refresh-confirm]");
    const label = button.querySelector("[data-refresh-label]");

    function storedToken() {
        try {
            return localStorage.getItem(TOKEN_KEY) || "";
        } catch {
            return "";
        }
    }

    function saveToken(token) {
        try {
            localStorage.setItem(TOKEN_KEY, token);
        } catch {
            /* private browsing */
        }
    }

    function clearToken() {
        try {
            localStorage.removeItem(TOKEN_KEY);
        } catch {
            /* ignore */
        }
    }

    function showError(message) {
        errorEl.textContent = message || "";
        errorEl.hidden = !message;
    }

    function showTokenField(show) {
        tokenField.hidden = !show;
        tokenInput.required = show;
        tokenInput.value = "";
        forgetButton.hidden = show;
    }

    function openDialog() {
        showTokenField(!storedToken());
        showError("");
        dialog.showModal();
        (tokenField.hidden ? confirmButton : tokenInput).focus();
    }

    async function startWorkflow(token) {
        const { repo, workflow } = button.dataset;
        let response;
        try {
            response = await fetch(`https://api.github.com/repos/${repo}/actions/workflows/${workflow}/dispatches`, {
                method: "POST",
                headers: {
                    Accept: "application/vnd.github+json",
                    Authorization: `Bearer ${token}`,
                    "X-GitHub-Api-Version": "2022-11-28",
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({ ref: "main", inputs: { force: "true" } }),
            });
        } catch {
            throw new Error("Could not reach GitHub. Check your connection and try again.");
        }
        if (response.status === 204) return;
        if ([401, 403, 404].includes(response.status)) {
            const error = new Error("GitHub rejected the token. It needs Actions: Read And Write on this repository.");
            error.badToken = true;
            throw error;
        }
        throw new Error(`GitHub returned ${response.status}. Please try again shortly.`);
    }

    const generatedAt = (doc) => {
        const meta = doc.querySelector('meta[name="brevity-generated"]');
        return meta ? meta.content : "";
    };

    async function latestGeneratedAt() {
        const url = new URL(window.location.href);
        url.hash = "";
        url.searchParams.set("refresh", String(Date.now()));
        const response = await fetch(url.toString(), { cache: "no-store" });
        if (!response.ok) return "";
        return generatedAt(new DOMParser().parseFromString(await response.text(), "text/html"));
    }

    function setBusy(text) {
        button.disabled = true;
        button.classList.add("is-refreshing");
        label.textContent = text;
    }

    function setIdle(text) {
        button.disabled = false;
        button.classList.remove("is-refreshing");
        label.textContent = text;
    }

    function reloadWhenPublished() {
        const before = generatedAt(document);
        const started = Date.now();
        setBusy("Refreshing\u2026");
        const timer = window.setInterval(async () => {
            if (Date.now() - started > POLL_LIMIT_MS) {
                window.clearInterval(timer);
                setIdle("Not Finished, Reload Later");
                return;
            }
            try {
                const latest = await latestGeneratedAt();
                if (latest && latest !== before) {
                    window.clearInterval(timer);
                    window.location.reload();
                }
            } catch {
                /* keep polling */
            }
        }, POLL_MS);
    }

    button.addEventListener("click", openDialog);

    forgetButton.addEventListener("click", () => {
        clearToken();
        showTokenField(true);
        tokenInput.focus();
    });

    form.addEventListener("submit", async (event) => {
        if (!event.submitter || event.submitter.value !== "confirm") return;
        event.preventDefault();
        const token = storedToken() || tokenInput.value.trim();
        if (!token) {
            showError("Paste a GitHub token to continue.");
            tokenInput.focus();
            return;
        }
        confirmButton.disabled = true;
        showError("");
        try {
            await startWorkflow(token);
            saveToken(token);
            dialog.close();
            reloadWhenPublished();
        } catch (error) {
            if (error.badToken) {
                clearToken();
                showTokenField(true);
            }
            showError(error.message);
        } finally {
            confirmButton.disabled = false;
        }
    });
})();
