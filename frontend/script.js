const starterCode = {
    python: 'print("Welcome to Cloud IDE")\n',
    c: "#include <stdio.h>\n\nint main() {\n    printf(\"Hello from C\\n\");\n    return 0;\n}\n",
    cpp: "#include <iostream>\nusing namespace std;\n\nint main() {\n    cout << \"Hello from C++\" << endl;\n    return 0;\n}\n",
    java: "public class Main {\n    public static void main(String[] args) {\n        System.out.println(\"Hello from Java\");\n    }\n}\n",
    javascript: 'console.log("Hello from JavaScript");\n',
    html: "<!DOCTYPE html>\n<html><body><h1>Hello from Cloud IDE</h1></body></html>\n",
    css: "body {\n    background: #0f172a;\n    color: white;\n}\n",
    json: '{\n  "name": "cloud-ide"\n}\n',
    markdown: "# Cloud IDE\n",
    text: "",
};

const syntaxExamples = {
    python: `# Python syntax starter
def main():
    name = "Student"
    print(f"Hello, {name}")

if __name__ == "__main__":
    main()
`,
    c: `#include <stdio.h>

int main(void) {
    printf("Hello, world\\n");
    return 0;
}
`,
    cpp: `#include <iostream>
using namespace std;

int main() {
    cout << "Hello, world" << endl;
    return 0;
}
`,
    java: `public class Main {
    public static void main(String[] args) {
        System.out.println("Hello, world");
    }
}
`,
    javascript: `// JavaScript syntax starter
function main() {
  console.log("Hello, world");
}

main();
`,
    html: `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>HTML Starter</title>
</head>
<body>
  <h1>Hello from HTML</h1>
</body>
</html>
`,
    css: `body {
  font-family: sans-serif;
  background: #0f172a;
  color: white;
}
`,
    json: `{
  "name": "cloud-ide",
  "version": "1.0.0"
}
`,
    markdown: `# Markdown Starter

- Add headings with \`#\`
- Make lists with \`-\`
- Use **bold** and *italic*
`,
    text: "Plain text has no special syntax.",
};

const languageModes = {
    python: "python",
    c: "text/x-csrc",
    cpp: "text/x-c++src",
    java: "text/x-java",
    javascript: "javascript",
    html: "htmlmixed",
    css: "css",
    json: { name: "javascript", json: true },
    markdown: "null",
    text: "null",
};

const state = {
    user: null,
    tree: { folders: [], files: [], shares: [] },
    selectedFile: null,
    selectedFolderId: null,
    expandedFolders: new Set(),
    autosaveTimer: null,
    cloudSyncTimer: null,
    dragItem: null,
    theme: localStorage.getItem("cloudIdeTheme") || "dark",
};

const $ = (id) => document.getElementById(id);
const authView = $("authView");
const ideView = $("ideView");
const treeRoot = $("treeRoot");
const versionsList = $("versionsList");
const shareList = $("shareList");
const searchResults = $("searchResults");
const historyList = $("historyList");
const serverList = $("serverList");
const cloudStats = $("cloudStats");
const recentFilesList = $("recentFilesList");
const breadcrumbBar = $("breadcrumbBar");
const treeContextMenu = $("treeContextMenu");
const outputConsole = $("outputConsole");
const syntaxHelperInput = $("syntaxHelperInput");
const loadSyntaxButton = $("loadSyntaxButton");
const resetSyntaxButton = $("resetSyntaxButton");
const syntaxLanguageBadge = $("syntaxLanguageBadge");
const themeToggleButton = $("themeToggleButton");
const workspaceLinkButton = $("workspaceLinkButton");
const deployAppButton = $("deployAppButton");
const cloudLinkStatus = $("cloudLinkStatus");
const cloudLinkList = $("cloudLinkList");
const syncStatus = $("syncStatus");
const previewFrame = $("previewFrame");
const autosaveStatus = $("autosaveStatus");
const currentFileLabel = $("currentFileLabel");
const fileNameInput = $("fileNameInput");
const parentFolderSelect = $("parentFolderSelect");
const languageSelect = $("languageSelect");
const stdinInput = $("stdinInput");
const executionMeta = $("executionMeta");
const storageBadge = $("storageBadge");
const dockerBadge = $("dockerBadge");
const workspaceTitle = $("workspaceTitle");

const editor = CodeMirror.fromTextArea($("codeEditor"), {
    mode: languageModes.python,
    theme: "material-darker",
    lineNumbers: true,
    indentUnit: 4,
    tabSize: 4,
    lineWrapping: true,
});

async function apiFetch(url, options = {}) {
    const response = await fetch(url, {
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", ...(options.headers || {}) },
        ...options,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
        throw new Error(data.error || "Request failed.");
    }
    return data;
}

function setView(view) {
    authView.classList.toggle("hidden", view !== "auth");
    ideView.classList.toggle("hidden", view !== "ide");
}

function setMessage(id, text, isError = false) {
    const el = $(id);
    el.textContent = text;
    el.className = `form-message ${isError ? "error" : "success"}`;
}

function emptyState(target, text) {
    target.innerHTML = `<div class="empty">${text}</div>`;
}

function fileLabel(file) {
    return `${file.filename}${file.permission === "read" ? " (read-only)" : ""}`;
}

function canWritePermission(permission) {
    return permission === "write" || permission === "both";
}

function syntaxStorageKey(language) {
    return `cloudIdeSyntax:${language}`;
}

function getSyntaxTemplate(language) {
    return syntaxExamples[language] || syntaxExamples.text;
}

function loadSyntaxTemplate(language) {
    return localStorage.getItem(syntaxStorageKey(language)) || getSyntaxTemplate(language);
}

function renderSyntaxTemplate(language, { preserveCurrent = false } = {}) {
    const stored = loadSyntaxTemplate(language);
    syntaxLanguageBadge.textContent = language.charAt(0).toUpperCase() + language.slice(1);
    if (!preserveCurrent || !syntaxHelperInput.value) {
        syntaxHelperInput.value = stored;
    }
}

function loadSyntaxIntoEditor() {
    const syntax = syntaxHelperInput.value.trimEnd();
    editor.setValue(syntax);
    if (languageSelect.value === "html") {
        previewFrame.srcdoc = syntax;
    }
    queueAutosave();
}

function resetSyntaxTemplate() {
    const language = languageSelect.value;
    const template = getSyntaxTemplate(language);
    syntaxHelperInput.value = template;
    localStorage.removeItem(syntaxStorageKey(language));
}

function applyTheme(theme, { persist = true } = {}) {
    const normalizedTheme = theme === "light" ? "light" : "dark";
    state.theme = normalizedTheme;
    document.body.dataset.theme = normalizedTheme;
    editor.setOption("theme", normalizedTheme === "dark" ? "material-darker" : "default");
    themeToggleButton.textContent = normalizedTheme === "dark" ? "Light Mode" : "Dark Mode";
    if (persist) {
        localStorage.setItem("cloudIdeTheme", normalizedTheme);
    }
}

async function syncWorkspaceState() {
    if (!state.user) return;
    clearTimeout(state.cloudSyncTimer);
    state.cloudSyncTimer = setTimeout(async () => {
        try {
            const payload = {
                selected_file_id: state.selectedFile?.id || null,
                language: languageSelect.value,
                editor_code: editor.getValue(),
                syntax_code: syntaxHelperInput.value,
                stdin_text: stdinInput.value,
                theme: state.theme,
            };
            const data = await apiFetch("/api/sync/state", {
                method: "PUT",
                body: JSON.stringify(payload),
            });
            syncStatus.textContent = `Cloud sync saved at ${new Date().toLocaleTimeString()}`;
            return data.state;
        } catch (error) {
            syncStatus.textContent = error.message;
            throw error;
        }
    }, 700);
}

async function loadWorkspaceState() {
    const data = await apiFetch("/api/sync/state");
    return data.state || {};
}

function renderCloudLinks(links = [], deployments = []) {
    const items = [];
    links.forEach((link) => {
        items.push(`
            <div class="list-card">
                <strong>Workspace Link</strong>
                <span>${link.title}</span>
                <div class="link-row">
                    <button class="ghost-btn" data-link-open="/workspace-link/${link.token}" type="button">Open</button>
                    <button class="ghost-btn" data-link-copy="/workspace-link/${link.token}" type="button">Copy</button>
                </div>
            </div>
        `);
    });
    deployments.forEach((deployment) => {
        items.push(`
            <div class="list-card">
                <strong>Deployment</strong>
                <span>${deployment.title} (${deployment.language.toUpperCase()})</span>
                <div class="link-row">
                    <button class="ghost-btn" data-link-open="/deploy/${deployment.token}" type="button">Open</button>
                    <button class="ghost-btn" data-link-copy="/deploy/${deployment.token}" type="button">Copy</button>
                </div>
            </div>
        `);
    });
    if (!items.length) {
        emptyState(cloudLinkList, "No workspace links or deployments yet.");
        return;
    }
    cloudLinkList.innerHTML = items.join("");
    cloudLinkList.querySelectorAll("[data-link-open]").forEach((button) => {
        button.addEventListener("click", () => window.open(button.dataset.linkOpen, "_blank", "noopener,noreferrer"));
    });
    cloudLinkList.querySelectorAll("[data-link-copy]").forEach((button) => {
        button.addEventListener("click", async () => {
            await navigator.clipboard.writeText(`${window.location.origin}${button.dataset.linkCopy}`);
            cloudLinkStatus.textContent = "Link copied to clipboard.";
        });
    });
}

function allFolders() {
    return [...state.tree.folders].sort((a, b) => a.name.localeCompare(b.name));
}

function buildFolderPath(folderId) {
    const folders = new Map(state.tree.folders.map((folder) => [folder.id, folder]));
    const parts = [];
    let current = folders.get(folderId);
    while (current) {
        parts.unshift(current.name);
        current = folders.get(current.parent_id);
    }
    return parts.join(" / ");
}

function ancestorFolderIds(folderId) {
    const folders = new Map(state.tree.folders.map((folder) => [folder.id, folder]));
    const ids = [];
    let current = folders.get(Number(folderId));
    while (current) {
        ids.unshift(current.id);
        current = folders.get(current.parent_id);
    }
    return ids;
}

function expandFolderPath(folderId) {
    ancestorFolderIds(folderId).forEach((id) => state.expandedFolders.add(id));
}

function selectedFolderIdOrRoot() {
    return state.selectedFolderId ?? "";
}

function getFolderById(folderId) {
    return state.tree.folders.find((folder) => folder.id === Number(folderId)) || null;
}

function renderBreadcrumb() {
    if (!breadcrumbBar) return;
    const crumbs = [
        `<button type="button" class="breadcrumb-segment ${!state.selectedFolderId ? "current" : ""}" data-breadcrumb-folder="">Root Workspace</button>`,
    ];
    const folder = state.selectedFolderId ? getFolderById(state.selectedFolderId) : null;
    if (folder) {
        const ids = ancestorFolderIds(folder.id);
        ids.forEach((folderId, index) => {
            const current = getFolderById(folderId);
            if (!current) return;
            crumbs.push(`<span class="breadcrumb-separator">/</span>`);
            crumbs.push(
                `<button type="button" class="breadcrumb-segment ${index === ids.length - 1 && !state.selectedFile ? "current" : ""}" data-breadcrumb-folder="${current.id}">${current.name}</button>`,
            );
        });
    }
    if (state.selectedFile) {
        crumbs.push(`<span class="breadcrumb-separator">/</span><span class="breadcrumb-file">${state.selectedFile.filename}</span>`);
    }
    breadcrumbBar.innerHTML = crumbs.join("");
    breadcrumbBar.querySelectorAll("[data-breadcrumb-folder]").forEach((button) => {
        button.addEventListener("click", () => selectFolder(button.dataset.breadcrumbFolder || null));
    });
}

function selectFolder(folderId = null) {
    state.selectedFolderId = folderId === null ? null : Number(folderId);
    if (state.selectedFolderId) {
        expandFolderPath(state.selectedFolderId);
    }
    renderFolderOptions();
    renderBreadcrumb();
    renderTree();
}

function renderFolderOptions() {
    parentFolderSelect.innerHTML = `<option value="">Root Workspace</option>${allFolders()
        .map((folder) => `<option value="${folder.id}">${buildFolderPath(folder.id)}</option>`)
        .join("")}`;
    parentFolderSelect.value = state.selectedFile?.folder_id ?? "";
}

function renderTree() {
    const foldersByParent = new Map();
    const filesByParent = new Map();
    state.tree.folders.forEach((folder) => {
        const key = folder.parent_id ?? "root";
        foldersByParent.set(key, [...(foldersByParent.get(key) || []), folder]);
    });
    state.tree.files.forEach((file) => {
        const key = file.folder_id ?? "root";
        filesByParent.set(key, [...(filesByParent.get(key) || []), file]);
    });

    function renderBranch(parentId = null, depth = 0) {
        const key = parentId ?? "root";
        const folders = (foldersByParent.get(key) || []).sort((a, b) => a.name.localeCompare(b.name));
        const files = (filesByParent.get(key) || []).sort((a, b) => a.filename.localeCompare(b.filename));
        return `
            ${folders
                .map((folder) => {
                    const expanded = state.expandedFolders.has(folder.id) || depth < 1;
                    if (depth < 1) state.expandedFolders.add(folder.id);
                    const childFiles = (filesByParent.get(folder.id) || []).sort((a, b) => a.filename.localeCompare(b.filename));
                    const previewFiles = childFiles.slice(0, 5);
                    return `
                        <div class="tree-item" draggable="${canWritePermission(folder.permission)}" data-drag-type="folder" data-id="${folder.id}">
                            <div class="tree-row ${expanded ? "open" : ""} ${state.selectedFolderId === folder.id ? "selected" : ""}" data-folder-toggle="${folder.id}">
                                <span class="tree-caret">${expanded ? "▾" : "▸"}</span>
                                <span class="tree-name">${folder.name}</span>
                                <span class="tree-meta">${folder.permission}</span>
                                <div class="tree-actions">
                                ${canWritePermission(folder.permission) ? `<button data-action="new-file" data-id="${folder.id}">+ File</button><button data-action="new-folder" data-id="${folder.id}">+ Folder</button><button data-action="rename-folder" data-id="${folder.id}">Rename</button><button data-action="delete-folder" data-id="${folder.id}">Del</button>` : ""}
                                </div>
                            </div>
                            <div class="tree-summary" data-summary-folder="${folder.id}">
                                <span class="tree-summary-label">Files</span>
                                ${previewFiles
                                    .map(
                                        (file) => `
                                            <button type="button" class="tree-summary-chip" data-summary-file="${file.id}">
                                                ${file.filename}
                                            </button>
                                        `,
                                    )
                                    .join("")}
                                ${childFiles.length > previewFiles.length ? `<span class="tree-summary-more">+${childFiles.length - previewFiles.length} more</span>` : ""}
                            </div>
                            <div class="tree-children ${expanded ? "" : "hidden"}" data-drop-folder="${folder.id}">
                                ${renderBranch(folder.id, depth + 1)}
                            </div>
                        </div>
                    `;
                })
                .join("")}
            ${files
                .map(
                    (file) => `
                        <div class="tree-file ${state.selectedFile?.id === file.id ? "active" : ""}" draggable="${canWritePermission(file.permission)}" data-file-open="${file.id}" data-drag-type="file" data-id="${file.id}">
                            <span class="tree-name">${file.filename}</span>
                            <span class="tree-meta">${file.owner_username}</span>
                            <div class="tree-actions file-actions">
                                ${canWritePermission(file.permission) ? `<button data-action="rename-file" data-id="${file.id}">Rename</button><button data-action="delete-file" data-id="${file.id}">Delete</button>` : ""}
                            </div>
                        </div>
                    `,
                )
                .join("")}
        `;
    }

    const html = renderBranch();
    treeRoot.innerHTML = html || `<div class="empty">No files yet.</div>`;
    bindTreeEvents();
    renderFolderOptions();
    renderBreadcrumb();
}

function renderShares() {
    if (!state.tree.shares.length) {
        emptyState(shareList, "No sharing rules yet.");
        return;
    }
    shareList.innerHTML = state.tree.shares
        .map(
            (share) => `
                <div class="list-card">
                    <strong>${share.resource_type} #${share.resource_id}</strong>
                    <span>${share.owner_username} → ${share.target_username} (${share.permission})</span>
                    ${share.owner_username === state.user.username ? `<button data-share-delete="${share.id}" class="ghost-btn" type="button">Remove</button>` : ""}
                </div>
            `,
        )
        .join("");
    shareList.querySelectorAll("[data-share-delete]").forEach((button) => {
        button.addEventListener("click", async () => {
            await apiFetch(`/api/shares/${button.dataset.shareDelete}`, { method: "DELETE" });
            await refreshWorkspace();
        });
    });
}

function renderVersions(versions = []) {
    if (!versions.length) {
        emptyState(versionsList, "File versions will appear here.");
        return;
    }
    versionsList.innerHTML = versions
        .map((version) => `<button class="list-card version-btn" data-version="${version.id}">Version ${version.version_number}<span>${new Date(version.created_at).toLocaleString()}</span></button>`)
        .join("");
    versionsList.querySelectorAll("[data-version]").forEach((button) => {
        button.addEventListener("click", async () => {
            const data = await apiFetch(`/api/versions/${button.dataset.version}`);
            editor.setValue(data.version.code);
            autosaveStatus.textContent = `Loaded version ${data.version.version_number}`;
        });
    });
}

function renderHistory(history = []) {
    if (!history.length) {
        emptyState(historyList, "No runs yet.");
        return;
    }
    historyList.innerHTML = history
        .map((item) => `<div class="list-card"><strong>${item.filename || "Unsaved"}</strong><span>${item.language.toUpperCase()} • ${item.status} • ${item.execution_mode}</span></div>`)
        .join("");
}

function renderServers(servers = []) {
    if (!servers.length) {
        emptyState(serverList, "No server data.");
        return;
    }
    serverList.innerHTML = servers
        .map((server) => `<div class="server-card"><strong>${server.name}</strong><span>${server.active_jobs} active • ${server.total_jobs} jobs • ${server.last_duration}s</span></div>`)
        .join("");
}

function renderCloudStatus(dashboard = {}) {
    const stats = dashboard.stats || {};
    const usage = dashboard.storage_usage || {};
    cloudStats.innerHTML = [
        { label: "Files", value: stats.files ?? 0, hint: "Cloud files stored" },
        { label: "Folders", value: stats.folders ?? 0, hint: "Workspace structure" },
        { label: "Runs", value: stats.runs ?? 0, hint: "Execution history" },
        { label: "Shared", value: stats.shared_with_me ?? 0, hint: "Files shared with you" },
        {
            label: "Storage Quota",
            value: `${usage.used_mb ?? 0} / ${usage.quota_mb ?? 10} MB`,
            hint: `${usage.used_percent ?? 0}% used`,
            quota: true,
        },
    ]
        .map(
            (card) => `
                <div class="cloud-card">
                    <strong>${card.value}</strong>
                    <span>${card.label}</span>
                    <small class="muted">${card.hint}</small>
                    ${card.quota ? `<div class="storage-meter"><div class="storage-meter-bar"><div class="storage-meter-fill" style="width: ${usage.used_percent ?? 0}%"></div></div><div class="storage-meter-text"><span>${usage.used_bytes ?? 0} bytes</span><span>${usage.quota_bytes ?? 0} bytes</span></div></div>` : ""}
                </div>
            `,
        )
        .join("");

    const recentFiles = dashboard.recent_files || [];
    if (!recentFiles.length) {
        emptyState(recentFilesList, "No recent cloud files yet.");
        return;
    }
    recentFilesList.innerHTML = recentFiles
        .map(
            (file) => `
                <button class="list-card result-btn" data-recent-file="${file.id}">
                    ${file.filename}
                    <span>${file.language.toUpperCase()} â€¢ ${file.owner_username}</span>
                </button>
            `,
        )
        .join("");
    recentFilesList.querySelectorAll("[data-recent-file]").forEach((button) => {
        button.addEventListener("click", () => openFile(Number(button.dataset.recentFile)));
    });
}

function renderExecutionMeta(result = {}) {
    const monitoring = result.monitoring || {};
    const executionTime = typeof result.execution_time === "number" ? `${result.execution_time}s` : "--";
    const cpuPercent = typeof monitoring.cpu_percent === "number" ? `${monitoring.cpu_percent}% CPU` : "-- CPU";
    const memoryMb = typeof monitoring.memory_mb === "number" ? `${monitoring.memory_mb} MB RAM` : "-- RAM";
    executionMeta.innerHTML = `
        <span class="metric">${result.assigned_server || "--"}</span>
        <span class="metric">${result.execution_mode || "--"}</span>
        <span class="metric">Time ${executionTime}</span>
        <span class="metric">${cpuPercent}</span>
        <span class="metric">${memoryMb}</span>
    `;
}

function focusTerminalPanel() {
    const terminalPanel = outputConsole.closest(".panel");
    if (terminalPanel) {
        terminalPanel.scrollIntoView({ behavior: "smooth", block: "start" });
    }
}

let treeContextActionSerial = 0;
const treeContextActions = new Map();

function hideTreeContextMenu() {
    if (!treeContextMenu) return;
    treeContextMenu.classList.add("hidden");
    treeContextMenu.innerHTML = "";
    treeContextMenu.style.left = "";
    treeContextMenu.style.top = "";
}

function showTreeContextMenu(items, x, y) {
    if (!treeContextMenu) return;
    treeContextActions.clear();
    const menuItems = items.map((item) => {
        treeContextActionSerial += 1;
        const actionId = String(treeContextActionSerial);
        treeContextActions.set(actionId, item.action);
        return { ...item, actionId };
    });
    treeContextMenu.innerHTML = menuItems
        .map(
            (item) => `
                <button type="button" class="${item.danger ? "danger" : ""}" data-menu-action-id="${item.actionId}">
                    ${item.label}
                </button>
            `,
        )
        .join("");
    treeContextMenu.classList.remove("hidden");
    const menuRect = treeContextMenu.getBoundingClientRect();
    const left = Math.min(x, window.innerWidth - menuRect.width - 12);
    const top = Math.min(y, window.innerHeight - menuRect.height - 12);
    treeContextMenu.style.left = `${Math.max(12, left)}px`;
    treeContextMenu.style.top = `${Math.max(12, top)}px`;
    treeContextMenu.querySelectorAll("[data-menu-action-id]").forEach((button) => {
        button.addEventListener("click", async () => {
            hideTreeContextMenu();
            const action = treeContextActions.get(button.dataset.menuActionId);
            try {
                if (typeof action === "function") {
                    await action();
                }
            } catch (error) {
                outputConsole.textContent = error.message;
            }
        });
    });
}

function openFolderContextMenu(folderId, event) {
    const folder = getFolderById(folderId);
    if (!folder) return;
    const items = [{ label: "Open", action: () => selectFolder(folder.id) }];
    if (canWritePermission(folder.permission)) {
        items.push(
            { label: "New File Here", action: () => createFile(folder.id) },
            { label: "New Folder Here", action: () => createFolder(folder.id) },
            { label: "Rename", action: () => renameFolder(folder.id) },
            { label: "Delete", action: () => deleteFolder(folder.id), danger: true },
        );
    }
    showTreeContextMenu(items, event.clientX, event.clientY);
}

function openFileContextMenu(fileId, event) {
    const file = state.tree.files.find((item) => item.id === Number(fileId));
    if (!file) return;
    const items = [{ label: "Open", action: () => openFile(file.id) }];
    if (canWritePermission(file.permission)) {
        items.push(
            { label: "Rename", action: () => renameFile(file.id) },
            { label: "Delete", action: () => deleteFileById(file.id), danger: true },
        );
    }
    showTreeContextMenu(items, event.clientX, event.clientY);
}

async function refreshWorkspace() {
    const [tree, dashboard, history, links, deployments] = await Promise.all([
        apiFetch("/api/workspace/tree"),
        apiFetch("/api/dashboard"),
        apiFetch("/api/history"),
        apiFetch("/api/workspace-links"),
        apiFetch("/api/deployments"),
    ]);
    state.tree = tree;
    workspaceTitle.textContent = `${state.user.username}'s Cloud IDE`;
    storageBadge.textContent = dashboard.storage_backend;
    dockerBadge.textContent = dashboard.docker_enabled ? "Docker enabled" : "Local runtime fallback";
    renderTree();
    renderShares();
    renderCloudStatus(dashboard);
    renderCloudLinks(links.links, deployments.deployments);
    renderBreadcrumb();
    renderHistory(history.history);
    renderServers(dashboard.servers);
}

async function openFile(fileId) {
    const data = await apiFetch(`/api/files/${fileId}`);
    state.selectedFile = data.file;
    selectFolder(data.file.folder_id ?? null);
    if (data.file.folder_id) {
        expandFolderPath(data.file.folder_id);
    }
    editor.setValue(data.file.code);
    editor.setOption("mode", languageModes[data.file.language] || "python");
    editor.setOption("readOnly", !canWritePermission(data.file.permission));
    languageSelect.value = data.file.language;
    fileNameInput.value = data.file.filename;
    parentFolderSelect.value = data.file.folder_id ?? "";
    currentFileLabel.textContent = fileLabel(data.file);
    autosaveStatus.textContent = "Autosave idle";
    renderSyntaxTemplate(data.file.language, { preserveCurrent: !!syntaxHelperInput.value });
    previewFrame.srcdoc = data.file.language === "html" ? data.file.code : "";
    outputConsole.textContent = `Opened ${data.file.filename}`;
    renderTree();
    const versions = await apiFetch(`/api/files/${fileId}/versions`);
    renderVersions(versions.versions);
    renderBreadcrumb();
    syncWorkspaceState().catch(() => {});
}

function bindTreeEvents() {
    treeRoot.querySelectorAll("[data-folder-toggle]").forEach((row) => {
        row.addEventListener("click", (event) => {
            if (event.target.closest("button")) return;
            const id = Number(row.dataset.folderToggle);
            selectFolder(id);
            if (state.expandedFolders.has(id)) state.expandedFolders.delete(id);
            else state.expandedFolders.add(id);
            renderTree();
        });
    });

    treeRoot.querySelectorAll("[data-file-open]").forEach((row) => row.addEventListener("click", () => openFile(Number(row.dataset.fileOpen))));
    treeRoot.querySelectorAll("[data-summary-file]").forEach((button) => button.addEventListener("click", () => openFile(Number(button.dataset.summaryFile))));
    treeRoot.querySelectorAll("[data-action='new-file']").forEach((button) => button.addEventListener("click", () => createFile(button.dataset.id)));
    treeRoot.querySelectorAll("[data-action='new-folder']").forEach((button) => button.addEventListener("click", () => createFolder(button.dataset.id)));
    treeRoot.querySelectorAll("[data-action='rename-folder']").forEach((button) => button.addEventListener("click", () => renameFolder(button.dataset.id)));
    treeRoot.querySelectorAll("[data-action='delete-folder']").forEach((button) => button.addEventListener("click", () => deleteFolder(button.dataset.id)));
    treeRoot.querySelectorAll("[data-action='rename-file']").forEach((button) => {
        button.addEventListener("click", (event) => {
            event.stopPropagation();
            renameFile(button.dataset.id);
        });
    });
    treeRoot.querySelectorAll("[data-action='delete-file']").forEach((button) => {
        button.addEventListener("click", (event) => {
            event.stopPropagation();
            deleteFileById(button.dataset.id);
        });
    });
    treeRoot.querySelectorAll("[data-folder-toggle]").forEach((row) => {
        row.addEventListener("contextmenu", (event) => {
            event.preventDefault();
            event.stopPropagation();
            openFolderContextMenu(Number(row.dataset.folderToggle), event);
        });
    });
    treeRoot.querySelectorAll("[data-file-open]").forEach((row) => {
        row.addEventListener("contextmenu", (event) => {
            event.preventDefault();
            event.stopPropagation();
            openFileContextMenu(Number(row.dataset.fileOpen), event);
        });
    });
    treeRoot.querySelectorAll("[data-summary-file]").forEach((button) => {
        button.addEventListener("contextmenu", (event) => {
            event.preventDefault();
            event.stopPropagation();
            openFileContextMenu(Number(button.dataset.summaryFile), event);
        });
    });

    treeRoot.querySelectorAll("[draggable='true']").forEach((node) => {
        node.addEventListener("dragstart", () => {
            state.dragItem = { id: Number(node.dataset.id), type: node.dataset.dragType };
        });
    });
    treeRoot.querySelectorAll("[data-drop-folder]").forEach((dropZone) => {
        dropZone.addEventListener("dragover", (event) => event.preventDefault());
        dropZone.addEventListener("drop", async (event) => {
            event.preventDefault();
            if (!state.dragItem) return;
            const folderId = Number(dropZone.dataset.dropFolder);
            if (state.dragItem.type === "file") {
                await apiFetch(`/api/files/${state.dragItem.id}`, { method: "PUT", body: JSON.stringify({ folder_id: folderId }) });
            } else {
                await apiFetch(`/api/folders/${state.dragItem.id}`, { method: "PUT", body: JSON.stringify({ parent_id: folderId }) });
            }
            state.dragItem = null;
            await refreshWorkspace();
        });
    });
}

async function createFile(folderId = selectedFolderIdOrRoot()) {
    const filename = prompt("Enter file name", "main.py");
    if (!filename) return;
    const language = filename.includes(".") ? guessLanguage(filename) : languageSelect.value;
    const data = await apiFetch("/api/files", {
        method: "POST",
        body: JSON.stringify({ filename, folder_id: folderId || null, language, code: starterCode[language] || "" }),
    });
    if (folderId !== "" && folderId !== null) {
        expandFolderPath(folderId);
        selectFolder(folderId);
    }
    await refreshWorkspace();
    await openFile(data.file.id);
}

async function createFolder(parentId = selectedFolderIdOrRoot()) {
    const name = prompt("Enter folder name", "src");
    if (!name) return;
    const data = await apiFetch("/api/folders", { method: "POST", body: JSON.stringify({ name, parent_id: parentId || null }) });
    if (parentId !== "" && parentId !== null) {
        expandFolderPath(parentId);
    }
    await refreshWorkspace();
    if (data.folder?.id) {
        expandFolderPath(data.folder.id);
        selectFolder(data.folder.id);
        renderTree();
    }
}

async function renameFile(fileId) {
    const file = state.tree.files.find((item) => item.id === Number(fileId));
    if (!file) return;
    const filename = prompt("Rename file", file.filename);
    if (!filename) return;
    await apiFetch(`/api/files/${fileId}`, {
        method: "PUT",
        body: JSON.stringify({ filename }),
    });
    await refreshWorkspace();
    if (state.selectedFile?.id === Number(fileId)) {
        await openFile(Number(fileId));
    }
}

async function deleteFileById(fileId) {
    const file = state.tree.files.find((item) => item.id === Number(fileId));
    if (!file) return;
    if (!confirm(`Delete ${file.filename}?`)) return;
    await apiFetch(`/api/files/${fileId}`, { method: "DELETE" });
    if (state.selectedFile?.id === Number(fileId)) {
        state.selectedFile = null;
        editor.setOption("readOnly", false);
        editor.setValue(starterCode.python);
        currentFileLabel.textContent = "No file selected";
        renderVersions([]);
    }
    await refreshWorkspace();
}

async function renameFolder(folderId) {
    const folder = state.tree.folders.find((item) => item.id === Number(folderId));
    const name = prompt("Rename folder", folder?.name || "");
    if (!name) return;
    await apiFetch(`/api/folders/${folderId}`, { method: "PUT", body: JSON.stringify({ name }) });
    await refreshWorkspace();
}

async function deleteFolder(folderId) {
    if (!confirm("Delete this folder and all nested items?")) return;
    await apiFetch(`/api/folders/${folderId}`, { method: "DELETE" });
    await refreshWorkspace();
}

function guessLanguage(filename) {
    const suffix = filename.split(".").pop().toLowerCase();
    return { py: "python", c: "c", cpp: "cpp", java: "java", js: "javascript", html: "html", css: "css", json: "json", md: "markdown", txt: "text" }[suffix] || "text";
}

async function saveCurrentFile(autosave = false) {
    if (!state.selectedFile) {
        await createFile();
        return;
    }
    if (!canWritePermission(state.selectedFile.permission)) {
        autosaveStatus.textContent = "Read-only shared file";
        return;
    }
    autosaveStatus.textContent = autosave ? "Autosaving..." : "Saving...";
    const endpoint = autosave ? `/api/files/${state.selectedFile.id}/autosave` : `/api/files/${state.selectedFile.id}`;
    const method = autosave ? "POST" : "PUT";
    const data = await apiFetch(endpoint, {
        method,
        body: JSON.stringify({
            filename: fileNameInput.value.trim() || state.selectedFile.filename,
            folder_id: parentFolderSelect.value || null,
            language: languageSelect.value,
            code: editor.getValue(),
        }),
    });
    state.selectedFile = data.file;
    if (data.file.folder_id) {
        expandFolderPath(data.file.folder_id);
    }
    currentFileLabel.textContent = fileLabel(data.file);
    autosaveStatus.textContent = autosave ? "Autosaved" : "Saved";
    await refreshWorkspace();
    syncWorkspaceState().catch(() => {});
}

function queueAutosave() {
    if (!state.selectedFile || !canWritePermission(state.selectedFile.permission)) return;
    autosaveStatus.textContent = "Changes pending...";
    clearTimeout(state.autosaveTimer);
    state.autosaveTimer = setTimeout(() => {
        saveCurrentFile(true).catch((error) => {
            autosaveStatus.textContent = error.message;
        });
    }, 1200);
}

async function deleteCurrentFile() {
    if (!state.selectedFile) return;
    if (!canWritePermission(state.selectedFile.permission)) return;
    if (!confirm(`Delete ${state.selectedFile.filename}?`)) return;
    await apiFetch(`/api/files/${state.selectedFile.id}`, { method: "DELETE" });
    state.selectedFile = null;
    editor.setOption("readOnly", false);
    editor.setValue(starterCode.python);
    currentFileLabel.textContent = "No file selected";
    renderVersions([]);
    await refreshWorkspace();
    syncWorkspaceState().catch(() => {});
}

async function createWorkspaceLink() {
    const data = await apiFetch("/api/workspace-links", {
        method: "POST",
        body: JSON.stringify({ file_id: state.selectedFile?.id || null }),
    });
    cloudLinkStatus.textContent = `Workspace link ready: ${window.location.origin}${data.url}`;
    await refreshWorkspace();
}

async function deployCurrentApp() {
    const language = state.selectedFile?.language || languageSelect.value;
    if (!["html", "javascript"].includes(language)) {
        cloudLinkStatus.textContent = "Deploy only works for HTML and JavaScript apps.";
        return;
    }
    const title = (fileNameInput.value.trim() || state.selectedFile?.filename || "Deployed app").replace(/\.[^.]+$/, "");
    const data = await apiFetch("/api/deployments", {
        method: "POST",
        body: JSON.stringify({
            file_id: state.selectedFile?.id || null,
            language,
            title,
            content: editor.getValue(),
        }),
    });
    cloudLinkStatus.textContent = `Deployment ready: ${window.location.origin}${data.url}`;
    await refreshWorkspace();
}

async function hydrateCloudWorkspaceState() {
    const sync = await loadWorkspaceState();
    applyTheme(sync.theme || state.theme, { persist: true });
    if (sync.language) {
        languageSelect.value = sync.language;
        editor.setOption("mode", languageModes[sync.language] || "python");
    }
    if (sync.stdin_text) stdinInput.value = sync.stdin_text;
    if (sync.syntax_code) syntaxHelperInput.value = sync.syntax_code;
    renderSyntaxTemplate(languageSelect.value, { preserveCurrent: true });
    syncStatus.textContent = "Cloud state loaded.";
    if (sync.selected_file_id) {
        try {
            await openFile(sync.selected_file_id);
        } catch (error) {
            state.selectedFile = null;
            syncStatus.textContent = "Saved file was not available, loading workspace defaults.";
        }
    }
    if (sync.editor_code) {
        editor.setValue(sync.editor_code);
        if (languageSelect.value === "html") {
            previewFrame.srcdoc = sync.editor_code;
        }
    }
}

async function runCode() {
    try {
        const result = await apiFetch("/run", {
            method: "POST",
            body: JSON.stringify({
                code: editor.getValue(),
                language: languageSelect.value,
                input: stdinInput.value,
                file_id: state.selectedFile?.id || null,
            }),
        });
        renderExecutionMeta(result);
        outputConsole.textContent = result.error ? `Error:\n${result.error}\n\n${result.output || ""}` : result.output || "Program finished with no output.";
        if (languageSelect.value === "html") {
            previewFrame.srcdoc = result.preview_html || editor.getValue();
        }
        const [dashboard, history] = await Promise.all([apiFetch("/api/dashboard"), apiFetch("/api/history")]);
        renderHistory(history.history);
        renderServers(dashboard.servers);
    } catch (error) {
        outputConsole.textContent = error.message;
    } finally {
        focusTerminalPanel();
    }
}

async function shareSelected() {
    if (!state.selectedFile) {
        cloudLinkStatus.textContent = "Select a file before sharing.";
        return;
    }
    const username = $("shareUsername").value.trim();
    if (!username) {
        cloudLinkStatus.textContent = "Enter a username first.";
        return;
    }
    try {
        cloudLinkStatus.textContent = "Saving share...";
        await apiFetch("/api/shares", {
            method: "POST",
            body: JSON.stringify({
                username,
                permission: $("sharePermission").value,
                resource_type: "file",
                resource_id: state.selectedFile.id,
            }),
        });
        $("shareUsername").value = "";
        cloudLinkStatus.textContent = "Share saved successfully.";
        await refreshWorkspace();
    } catch (error) {
        cloudLinkStatus.textContent = error.message;
    }
}

async function searchFiles() {
    const q = $("searchInput").value.trim();
    if (!q) {
        searchResults.innerHTML = "";
        return;
    }
    const data = await apiFetch(`/api/files/search?q=${encodeURIComponent(q)}`);
    searchResults.innerHTML = data.results.length
        ? data.results.map((file) => `<button class="list-card result-btn" data-result-id="${file.id}">${file.filename}<span>${file.owner_username}</span></button>`).join("")
        : `<div class="empty">No matching files.</div>`;
    searchResults.querySelectorAll("[data-result-id]").forEach((button) => {
        button.addEventListener("click", () => openFile(Number(button.dataset.resultId)));
    });
}

async function initializeApp() {
    await apiFetch("/api/bootstrap", { method: "POST", body: JSON.stringify({}) });
    editor.setOption("readOnly", false);
    try {
        await refreshWorkspace();
    } catch (error) {
        syncStatus.textContent = `Cloud workspace partial load: ${error.message}`;
    }
    try {
        await hydrateCloudWorkspaceState();
    } catch (error) {
        syncStatus.textContent = `Cloud state load skipped: ${error.message}`;
    }
    setView("ide");
}

async function logout() {
    await apiFetch("/api/logout", { method: "POST", body: JSON.stringify({}) });
    state.user = null;
    state.selectedFile = null;
    setView("auth");
}

async function checkSession() {
    const data = await apiFetch("/api/session");
    if (!data.authenticated) {
        setView("auth");
        return;
    }
    state.user = data.user;
    await initializeApp();
}

$("loginForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
        const data = await apiFetch("/api/login", { method: "POST", body: JSON.stringify({ username: form.get("username"), password: form.get("password") }) });
        state.user = data.user;
        await initializeApp();
    } catch (error) {
        setMessage("loginMessage", error.message, true);
    }
});

$("signupForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
        const data = await apiFetch("/api/signup", {
            method: "POST",
            body: JSON.stringify({ username: form.get("username"), password: form.get("password"), confirm_password: form.get("confirmPassword") }),
        });
        state.user = data.user;
        await initializeApp();
    } catch (error) {
        setMessage("signupMessage", error.message, true);
    }
});

$("newFolderButton").addEventListener("click", () => createFolder());
$("newFileButton").addEventListener("click", () => createFile());
$("refreshTreeButton").addEventListener("click", refreshWorkspace);
$("logoutButton").addEventListener("click", logout);
$("saveButton").addEventListener("click", () => saveCurrentFile(false));
$("runButton").addEventListener("click", runCode);
$("deleteButton").addEventListener("click", deleteCurrentFile);
$("renameButton").addEventListener("click", () => saveCurrentFile(false));
$("shareButton").addEventListener("click", shareSelected);
$("searchInput").addEventListener("input", searchFiles);
treeRoot.addEventListener("contextmenu", (event) => {
    if (event.target.closest("[data-folder-toggle], [data-file-open], [data-summary-file], [data-action]")) return;
    event.preventDefault();
    showTreeContextMenu(
        [
            { label: "New File Here", action: () => createFile() },
            { label: "New Folder Here", action: () => createFolder() },
        ],
        event.clientX,
        event.clientY,
    );
});
document.addEventListener("click", hideTreeContextMenu);
document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") hideTreeContextMenu();
});
themeToggleButton.addEventListener("click", () => {
    applyTheme(state.theme === "dark" ? "light" : "dark");
    syncWorkspaceState().catch(() => {});
});
workspaceLinkButton.addEventListener("click", () => createWorkspaceLink().catch((error) => {
    cloudLinkStatus.textContent = error.message;
}));
deployAppButton.addEventListener("click", () => deployCurrentApp().catch((error) => {
    cloudLinkStatus.textContent = error.message;
}));
languageSelect.addEventListener("change", () => {
    editor.setOption("mode", languageModes[languageSelect.value] || "python");
    renderSyntaxTemplate(languageSelect.value);
    queueAutosave();
    syncWorkspaceState().catch(() => {});
});

editor.on("change", () => {
    if (languageSelect.value === "html") {
        previewFrame.srcdoc = editor.getValue();
    }
    queueAutosave();
    syncWorkspaceState().catch(() => {});
});

fileNameInput.addEventListener("input", queueAutosave);
parentFolderSelect.addEventListener("change", queueAutosave);
stdinInput.addEventListener("input", () => {
    localStorage.setItem("cloudIdeStdin", stdinInput.value);
    syncWorkspaceState().catch(() => {});
});
stdinInput.value = localStorage.getItem("cloudIdeStdin") || "";
syntaxHelperInput.addEventListener("input", () => {
    localStorage.setItem(syntaxStorageKey(languageSelect.value), syntaxHelperInput.value);
    syncWorkspaceState().catch(() => {});
});
loadSyntaxButton.addEventListener("click", loadSyntaxIntoEditor);
resetSyntaxButton.addEventListener("click", resetSyntaxTemplate);
applyTheme(state.theme, { persist: false });
renderSyntaxTemplate(languageSelect.value);
renderBreadcrumb();
renderExecutionMeta({ assigned_server: "Ready", execution_mode: "Idle", execution_time: 0, monitoring: { cpu_percent: 0, memory_mb: 0 } });
renderVersions([]);
renderHistory([]);
renderServers([]);
renderCloudStatus({});
renderCloudLinks([], []);
syncStatus.textContent = "Cloud sync ready.";
checkSession().catch(() => setView("auth"));
