/*
 * Obsidian Intake - Obsidian plugin (v1.4.2)
 * - edit warning shown once per opened file
 * - banner on managed copies (temporary read, save to main, always linked)
 * - Deleted-Notes folder with configurable retention + red expiring badge
 * - scroll-to-top/bottom buttons (optional)
 * - open-attempt limiting handled router-side
 */
const {
    Plugin, Notice, PluginSettingTab, Setting, Modal, TFolder,
    FuzzySuggestModal, MarkdownView, TFile,
} = require("obsidian");
const { execFile } = require("child_process");
const os = require("os");
const path = require("path");

const CORE_EXTENSIONS = ["md", "txt", "yaml", "json", "csv", "log"];

/* Extensions Obsidian opens natively (no extra plugin needed). */
const OBSIDIAN_NATIVE_EXTS = [
    "md",
    "png", "jpg", "jpeg", "gif", "bmp", "svg", "webp",
    "mp3", "ogg", "wav", "flac", "m4a",
    "mp4", "webm", "mov",
    "pdf",
];

/*
 * About-section content. Edit these values when the public repository and
 * donation page are ready. Empty URLs keep the corresponding button disabled
 * rather than sending users to a placeholder address.
 */
const ABOUT_INFO = {
    description:
        "Manage external files opened in Obsidian through a controlled " +
        "Not-Indexed / Deleted-Notes lifecycle, with temporary reads, " +
        "recovery, synchronization and conflict handling.",
    githubUrl: "https://github.com/moein-alas/obsidian-intake",
    donateUrl: "",
};

const DEFAULTS = {
    extensions: ["md", "txt", "yaml", "json", "csv", "log"],
    customExtensions: [],
    managedFolder: "Not-Indexed",
    deletedFolder: "Deleted-Notes",
    deletedRetentionValue: 20,
    deletedRetentionUnit: "days",
    managedFolderSettingName: "Not-Indexed",
    routerPath: path.join(os.homedir(), ".local", "bin", "obsidian-intake-router"),
    writeSourceHeader: true,
    excludeFromGraph: true,
    noticeTimeout: 7,
    noticeAutoDismiss: {},
    temporaryReadDefault: false,
    showScrollButtons: true,
};

function runRouter(plugin, args) {
    return new Promise((resolve) => {
        execFile(
            plugin.settings.routerPath,
            args,
            { timeout: 15000, encoding: "utf8" },
            (err, stdout) => {
                let data = null;
                try { data = JSON.parse(stdout); } catch (e) { /* ignore */ }
                resolve({ err, data, stdout });
            }
        );
    });
}

/*
 * Custom notification with an OK button and an "Auto dismiss" checkbox.
 * The checkbox state is remembered per notification type, so ticking it
 * once makes only THIS type auto-dismiss later (after noticeTimeout
 * seconds, pre-checked). Other notification types keep requiring OK.
 */
class IntakeNotice {
    constructor(plugin, type, message) {
        this.plugin = plugin;
        this.type = type;

        const seconds = Number(plugin.settings.noticeTimeout);
        this.timeoutMs = (seconds > 0 ? seconds : 7) * 1000;
        const memory = plugin.settings.noticeAutoDismiss || {};
        const remembered = memory[type] === true;

        const box = document.createElement("div");
        box.className = "obsidian-intake-notice";
        box.style.cssText =
            "position:fixed;top:16px;right:16px;z-index:9999;max-width:360px;" +
            "padding:12px 14px;border-radius:8px;font-size:14px;" +
            "line-height:1.45;background:var(--background-secondary-alt);" +
            "color:var(--text-normal);border:1px solid " +
            "var(--background-modifier-border);box-shadow:0 4px 16px rgba(0,0,0,0.3);";

        const msgEl = document.createElement("div");
        msgEl.textContent = message;
        msgEl.style.marginBottom = "10px";
        box.appendChild(msgEl);

        const row = document.createElement("div");
        row.style.cssText =
            "display:flex;align-items:center;justify-content:space-between;gap:10px;";

        const label = document.createElement("label");
        label.style.cssText =
            "display:flex;align-items:center;gap:6px;font-size:12px;cursor:pointer;user-select:none;";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = remembered;
        const labelText = document.createElement("span");
        labelText.textContent = "Auto dismiss";
        label.appendChild(checkbox);
        label.appendChild(labelText);
        row.appendChild(label);

        const okBtn = document.createElement("button");
        okBtn.textContent = "OK";
        okBtn.style.cssText =
            "padding:3px 14px;cursor:pointer;border-radius:5px;font-size:12px;" +
            "border:1px solid var(--background-modifier-border);" +
            "background:var(--interactive-accent);color:var(--text-on-accent);";
        row.appendChild(okBtn);
        box.appendChild(row);

        const restack = () => {
            const all = Array.from(document.querySelectorAll(".obsidian-intake-notice"));
            all.forEach((el, i) => {
                el.style.top = (16 + i * 84) + "px";
            });
        };

        let timer = null;
        const close = () => {
            if (timer) clearTimeout(timer);
            box.remove();
            restack();
        };

        checkbox.addEventListener("change", async () => {
            await plugin.setNoticeAutoDismiss(type, checkbox.checked);
            if (checkbox.checked) {
                timer = setTimeout(close, this.timeoutMs);
            } else if (timer) {
                clearTimeout(timer);
                timer = null;
            }
        });
        okBtn.addEventListener("click", close);

        document.body.appendChild(box);
        restack();

        if (remembered) {
            timer = setTimeout(close, this.timeoutMs);
        }
    }
}

const STATUS_LABEL = {
    managed: "Managed",
    permanent: "Permanent",
    conflict: "Conflict",
};

/*
 * Floating banner at the top of a managed document (collapsible
 * <details> drop-down). Shows the copy warning, the temporary-read
 * checkbox, save-to-main controls and the always-linked checkbox.
 */
class IntakeBanner {
    constructor(plugin, view, file) {
        this.plugin = plugin;
        this.view = view;
        this.file = file;
        this.el = null;
        this.checkData = null;
        this.saveBtn = null;
        this.linkCb = null;
        this.tempCb = null;
        this.warningEl = null;
        this.deletedMessageEl = null;
        this.protectCb = null;
        this.recoveryHost = null;
        this.build();
        this.refreshData();
    }

    hostEl() {
        if (!this.view) return null;
        if (this.view.contentEl) return this.view.contentEl;
        if (this.view.containerEl && this.view.containerEl.querySelector) {
            return this.view.containerEl.querySelector(".view-content") ||
                this.view.containerEl;
        }
        return null;
    }

    build() {
        if (this.plugin.isInDeletedFolder(this.file.path)) {
            this.buildDeletedBanner();
        } else {
            this.buildManagedBanner();
        }
    }

    baseStyle(el) {
        el.className = "obsidian-intake-banner";
        el.style.cssText =
            "margin:0 0 4px 0;padding:8px 12px;border-radius:8px;" +
            "background:var(--background-secondary);" +
            "border:1px solid var(--background-modifier-border);" +
            "color:var(--text-muted);font-size:12.5px;";
    }

    buildManagedBanner() {
        const p = this.plugin;
        const details = document.createElement("details");
        this.baseStyle(details);

        const summary = document.createElement("summary");
        summary.style.cssText =
            "cursor:pointer;user-select:none;color:var(--text-muted);";

        const summaryText = document.createElement("span");
        summaryText.textContent = "Viewing a copy — click for more options";
        summary.appendChild(summaryText);

        // Temporary-read is intentionally visible in the collapsed summary.
        const tempRow = document.createElement("label");
        tempRow.style.cssText =
            "display:inline-flex;align-items:center;gap:5px;margin-left:12px;" +
            "cursor:pointer;color:var(--text-normal);font-weight:normal;";
        tempRow.title = "Move this copy to " +
            (p.settings.deletedFolder || "Deleted-Notes") +
            " when the document is closed";
        tempRow.addEventListener("click", (ev) => ev.stopPropagation());
        const tempCb = document.createElement("input");
        tempCb.type = "checkbox";
        const forcedOff = p.tempReadForcedOff.has(this.file.path);
        tempCb.checked = !forcedOff && (
            p.tempReadPaths.has(this.file.path) ||
            (p.settings.temporaryReadDefault === true &&
             !p.tempReadDecided.has(this.file.path))
        );
        if (tempCb.checked && !p.tempReadDecided.has(this.file.path)) {
            p.tempReadPaths.add(this.file.path);
        }
        p.tempReadDecided.add(this.file.path);
        tempCb.addEventListener("click", (ev) => ev.stopPropagation());
        tempCb.addEventListener("change", async () => {
            p.tempReadForcedOff.delete(this.file.path);
            if (tempCb.checked) p.tempReadPaths.add(this.file.path);
            else p.tempReadPaths.delete(this.file.path);
            if (tempCb.checked && this.checkData && this.checkData.id &&
                this.checkData.temporary_read_suppressed) {
                await runRouter(p, [
                    "set-temp-suppressed", String(this.checkData.id), "0",
                ]);
                this.checkData.temporary_read_suppressed = 0;
            }
        });
        const tempText = document.createElement("span");
        tempText.textContent = "Temporary read → Deleted-Notes on close";
        tempRow.appendChild(tempCb);
        tempRow.appendChild(tempText);
        summary.appendChild(tempRow);
        this.tempCb = tempCb;
        details.appendChild(summary);

        const body = document.createElement("div");
        body.style.cssText =
            "margin-top:8px;display:flex;flex-direction:column;gap:8px;";

        const warn = document.createElement("div");
        warn.style.cssText = "color:var(--text-normal);";
        warn.textContent =
            "You are viewing a copy of the external file. " +
            "Changes apply only to the copy until saved to the main file.";
        body.appendChild(warn);
        this.warningEl = warn;

        const recoveryHost = document.createElement("div");
        body.appendChild(recoveryHost);
        this.recoveryHost = recoveryHost;

        this.buildControls(body);
        details.appendChild(body);
        this.el = details;
        const host = this.hostEl();
        if (host) host.prepend(details);
    }

    buildDeletedBanner() {
        const p = this.plugin;
        const box = document.createElement("div");
        this.baseStyle(box);
        box.style.display = "flex";
        box.style.alignItems = "center";
        box.style.gap = "10px";
        box.style.flexWrap = "wrap";

        const msg = document.createElement("div");
        msg.style.cssText = "color:var(--text-normal);flex:1;min-width:260px;";
        msg.textContent =
            "This is a copy of the main file and will be removed " +
            "automatically after the retention period.";
        box.appendChild(msg);
        this.deletedMessageEl = msg;

        const restoreBtn = document.createElement("button");
        restoreBtn.textContent = "Restore";
        restoreBtn.title =
            "Return this copy from Deleted-Notes to " +
            (p.settings.managedFolder || "Not-Indexed");
        restoreBtn.style.fontSize = "12px";
        restoreBtn.addEventListener("click", async () => {
            await p.restoreDeletedFile(this.file.path);
        });
        box.appendChild(restoreBtn);

        const protectLabel = document.createElement("label");
        protectLabel.style.cssText =
            "display:flex;align-items:center;gap:6px;cursor:pointer;";
        const protectCb = document.createElement("input");
        protectCb.type = "checkbox";
        protectCb.checked = false;
        protectCb.addEventListener("change", async () => {
            if (!this.checkData || !this.checkData.id) return;
            const r = await runRouter(p, [
                "set-delete-protected", String(this.checkData.id),
                protectCb.checked ? "1" : "0",
            ]);
            if (!r.data || !r.data.ok) {
                protectCb.checked = !protectCb.checked;
                p.showNotice("delete-protection",
                    "Could not change automatic-delete protection.");
                return;
            }
            await p.refreshDeletedMetadata();
            await this.refreshData();
        });
        const protectText = document.createElement("span");
        protectText.textContent = "Do not delete after the retention limit";
        protectLabel.appendChild(protectCb);
        protectLabel.appendChild(protectText);
        box.appendChild(protectLabel);
        this.protectCb = protectCb;

        this.el = box;
        const host = this.hostEl();
        if (host) host.prepend(box);
    }

    buildControls(body) {
        const p = this.plugin;
        const saveRow = document.createElement("div");
        saveRow.style.cssText =
            "display:flex;align-items:center;gap:10px;flex-wrap:wrap;";
        const saveBtn = document.createElement("button");
        saveBtn.textContent = "Save changes to main file";
        saveBtn.style.cssText =
            "padding:3px 10px;font-size:12px;border-radius:5px;cursor:pointer;";
        saveBtn.disabled = true;
        saveBtn.addEventListener("click", async () => {
            if (!this.checkData || !this.checkData.id) return;
            const r = await runRouter(p,
                ["sync-push", String(this.checkData.id)]);
            if (r.data && r.data.ok) {
                p.dirtyPaths.delete(this.file.path);
                p.showNotice("sync-push", "Changes written to the main file.");
                await this.refreshData();
            } else {
                p.showNotice("sync-push", "Save failed: " +
                    (r.data && r.data.error || "error"));
            }
        });
        saveRow.appendChild(saveBtn);
        this.saveBtn = saveBtn;

        const autoLabel = document.createElement("label");
        autoLabel.style.cssText =
            "display:flex;align-items:center;gap:6px;cursor:pointer;";
        const autoCb = document.createElement("input");
        autoCb.type = "checkbox";
        autoCb.checked = p.autoSavePaths.has(this.file.path);
        autoCb.addEventListener("change", () => {
            if (autoCb.checked) p.autoSavePaths.add(this.file.path);
            else p.autoSavePaths.delete(this.file.path);
            this.updateSaveState();
        });
        const autoText = document.createElement("span");
        autoText.textContent = "Auto save to main";
        autoLabel.appendChild(autoCb);
        autoLabel.appendChild(autoText);
        saveRow.appendChild(autoLabel);
        body.appendChild(saveRow);

        const linkRow = document.createElement("label");
        linkRow.style.cssText =
            "display:flex;align-items:center;gap:6px;cursor:pointer;";
        const linkCb = document.createElement("input");
        linkCb.type = "checkbox";
        linkCb.checked = true;
        linkCb.disabled = p.isInManagedFolder(this.file.path);
        if (linkCb.disabled) {
            linkRow.title = "Unchecking is only possible after the file " +
                "is moved out of " +
                (p.settings.managedFolder || "Not-Indexed");
        }
        linkCb.addEventListener("change", async () => {
            if (!this.checkData || !this.checkData.id) return;
            await runRouter(p, ["set-always-linked",
                String(this.checkData.id), linkCb.checked ? "1" : "0"]);
            p.showNotice("always-linked", linkCb.checked
                ? "File stays linked to the main file."
                : "File is now independent of the main file.");
        });
        const linkText = document.createElement("span");
        linkText.textContent = "Always linked to main file";
        linkRow.appendChild(linkCb);
        linkRow.appendChild(linkText);
        body.appendChild(linkRow);
        this.linkCb = linkCb;

        const hint = document.createElement("div");
        hint.style.cssText = "color:var(--text-faint);";
        hint.textContent =
            "Tip: you can move this document to other vault folders via " +
            "“Move to…” in the Obsidian Intake dashboard.";
        body.appendChild(hint);
    }

    async renderRecovery() {
        if (!this.recoveryHost) return;
        this.recoveryHost.innerHTML = "";
        if (!this.checkData || !this.checkData.recovery_pending) return;

        if (this.el && this.el.tagName === "DETAILS") this.el.open = true;
        const panel = document.createElement("div");
        panel.style.cssText =
            "padding:9px;border-radius:7px;border:1px solid var(--text-warning);" +
            "background:var(--background-primary-alt);color:var(--text-normal);";
        const msg = document.createElement("div");
        msg.style.fontWeight = "600";
        msg.textContent =
            "This copy contains edited changes that are not saved to the main file. " +
            "Do you want to recover and save them?";
        panel.appendChild(msg);

        const diffBox = document.createElement("div");
        diffBox.style.cssText =
            "margin-top:8px;max-height:220px;overflow:auto;font-family:var(--font-monospace);" +
            "font-size:11.5px;border:1px solid var(--background-modifier-border);" +
            "border-radius:5px;";
        diffBox.textContent = "Loading differences…";
        panel.appendChild(diffBox);

        const diff = await runRouter(this.plugin,
            ["recovery-diff", String(this.checkData.id)]);
        diffBox.innerHTML = "";
        if (diff.data && diff.data.ok && diff.data.text_diff_available) {
            const lines = Array.isArray(diff.data.lines) ? diff.data.lines : [];
            for (const line of lines) {
                const row = document.createElement("div");
                row.textContent = line || " ";
                row.style.whiteSpace = "pre-wrap";
                row.style.padding = "1px 6px";
                if (line.startsWith("+") && !line.startsWith("+++")) {
                    row.style.background = "rgba(46, 160, 67, 0.18)";
                } else if (line.startsWith("-") && !line.startsWith("---")) {
                    row.style.background = "rgba(248, 81, 73, 0.18)";
                } else if (line.startsWith("@@")) {
                    row.style.color = "var(--text-accent)";
                } else {
                    row.style.color = "var(--text-muted)";
                }
                diffBox.appendChild(row);
            }
            if (diff.data.truncated) {
                const more = document.createElement("div");
                more.textContent = "Diff preview truncated.";
                more.style.cssText = "padding:4px 6px;color:var(--text-muted);";
                diffBox.appendChild(more);
            }
        } else {
            diffBox.textContent = diff.data && diff.data.message
                ? diff.data.message
                : "Difference preview is unavailable for this file.";
        }

        const actions = document.createElement("div");
        actions.style.cssText = "display:flex;gap:8px;margin-top:8px;";
        const yes = document.createElement("button");
        yes.textContent = "Yes — recover & save";
        yes.addClass && yes.addClass("mod-cta");
        yes.addEventListener("click", () => this.resolveRecovery("vault"));
        const no = document.createElement("button");
        no.textContent = "No — discard copy edits";
        no.addEventListener("click", () => this.resolveRecovery("external"));
        actions.appendChild(yes);
        actions.appendChild(no);
        panel.appendChild(actions);
        this.recoveryHost.appendChild(panel);
    }

    async resolveRecovery(keep) {
        if (!this.checkData || !this.checkData.id) return;
        const r = await runRouter(this.plugin, [
            "resolve-recovery", String(this.checkData.id), keep,
        ]);
        if (r.data && r.data.ok) {
            this.plugin.dirtyPaths.delete(this.file.path);
            this.plugin.showNotice("recovery-resolved",
                keep === "vault"
                    ? "Edited copy recovered and saved to the main file."
                    : "Copied edits discarded; the main file was restored.");
            await this.refreshData();
        } else {
            this.plugin.showNotice("recovery-error",
                (r.data && r.data.error) || "Recovery could not be completed.");
        }
    }

    async refreshData() {
        const r = await runRouter(this.plugin, ["check", this.file.path]);
        if (r.data && r.data.found) {
            this.checkData = r.data;
            if (this.tempCb && r.data.temporary_read_suppressed) {
                this.tempCb.checked = false;
                this.plugin.tempReadPaths.delete(this.file.path);
                this.plugin.tempReadDecided.add(this.file.path);
                this.plugin.tempReadForcedOff.add(this.file.path);
            }
            if (this.linkCb) {
                this.linkCb.checked = r.data.always_linked !== false;
            }
            if (this.warningEl) {
                this.warningEl.textContent =
                    "You are viewing a copy of (" +
                    (r.data.original_path || "external file") +
                    "). Changes apply only to the copy until saved to the main file.";
            }
            if (this.protectCb) {
                this.protectCb.checked = r.data.delete_protected === true ||
                    r.data.delete_protected === 1;
            }
            if (this.deletedMessageEl) {
                const protectedMode = r.data.delete_protected === true ||
                    r.data.delete_protected === 1;
                if (protectedMode) {
                    this.deletedMessageEl.textContent =
                        "This is a copy of the main file. Automatic deletion is currently disabled.";
                } else {
                    const sec = Number(r.data.deleted_remaining_seconds);
                    const days = Number.isFinite(sec)
                        ? Math.max(0, Math.ceil(sec / 86400))
                        : null;
                    this.deletedMessageEl.textContent =
                        "This is a copy of the main file and will be removed " +
                        "automatically after " +
                        (days === null ? "the retention period" : days + " day" +
                         (days === 1 ? "" : "s")) + ".";
                }
            }
        }
        this.updateSaveState();
        await this.renderRecovery();
    }

    updateSaveState() {
        if (!this.saveBtn) return;
        const dirty = this.plugin.dirtyPaths.has(this.file.path);
        const auto = this.plugin.autoSavePaths.has(this.file.path);
        const externalExists = !this.checkData || this.checkData.external_exists;
        this.saveBtn.disabled = auto || !dirty || !externalExists;
    }

    destroy() {
        if (this.el) this.el.remove();
        this.el = null;
    }
}

class DashboardModal extends Modal {
    constructor(app, plugin) {
        super(app);
        this.plugin = plugin;
    }

    async onOpen() {
        this.contentEl.empty();
        this.contentEl.createEl("h2", { text: "Obsidian Intake" });

        // toolbar: refresh + global history
        const toolbar = this.contentEl.createDiv();
        toolbar.style.cssText = "display:flex;gap:6px;margin-bottom:8px;";
        const refreshBtn = toolbar.createEl("button", { text: "Refresh" });
        refreshBtn.addEventListener("click", () => this.refresh());
        const histBtn = toolbar.createEl("button", { text: "History" });
        histBtn.addEventListener("click", async () => {
            const h = await runRouter(this.plugin, ["history-all", "200"]);
            showTimelineModal(this.app,
                Array.isArray(h.data) ? h.data : []);
        });

        const summaryEl = this.contentEl.createDiv();
        const listEl = this.contentEl.createDiv();
        const deletedEl = this.contentEl.createDiv();

        const status = await runRouter(this.plugin, ["status"]);
        if (status.err || !status.data) {
            this.plugin.showNotice("router-error",
                "Obsidian Intake router not reachable. Check plugin settings.");
            summaryEl.createEl("p", { text: "Router error." });
            return;
        }
        const s = status.data;
        summaryEl.createEl("p", {
            text: "Managed files: " + s.total +
                "  |  managed: " + s.managed +
                "  |  permanent: " + s.permanent +
                "  |  conflicts: " + s.conflict,
        });

        const res = await runRouter(this.plugin, ["list"]);
        const files = Array.isArray(res.data) ? res.data : [];
        if (files.length === 0) {
            listEl.createEl("p", { text: "No managed files yet." });
        }
        this.renderManagedFiles(files, listEl);
        await this.renderDeletedFiles(deletedEl);
    }

    renderManagedFiles(files, listEl) {
        for (const f of files) {
            const row = listEl.createDiv("obsidian-intake-row");
            row.style.cssText =
                "display:flex;align-items:center;gap:8px;padding:6px 0;" +
                "border-bottom:1px solid var(--background-modifier-border);";
            const info = row.createDiv();
            info.style.flex = "1";
            info.createEl("div", {
                text: f.vault_path, cls: "obsidian-intake-path",
            });
            const badge = STATUS_LABEL[f.status] || f.status;
            const st = info.createEl("div", {
                text: badge + (f.conflict ? " — external and vault changed" : ""),
            });
            st.style.fontSize = "0.8em";
            st.style.color = f.status === "conflict"
                ? "var(--text-error)" : "var(--text-muted)";

            const actions = row.createDiv();
            actions.style.cssText = "display:flex;gap:4px;flex-wrap:wrap;";

            this.addButton(actions, "Open", async () => {
                await runRouter(this.plugin, ["open", f.vault_path]);
                this.close();
            });
            this.addManagedActions(actions, f);
        }
    }

    addManagedActions(actions, f) {
        const p = this.plugin;
        if (f.status !== "permanent" && f.external_exists) {
            this.addButton(actions, "Move Permanently", async () => {
                const r = await runRouter(p, ["migrate", String(f.id)]);
                p.showNotice("migrate", r.data && r.data.ok
                    ? "Ownership moved to vault."
                    : "Migrate failed: " + (r.data && r.data.error || "error"));
                this.refresh();
            }, true);
        }
        if (f.status === "conflict") {
            this.addButton(actions, "Keep Vault", async () => {
                await runRouter(p, ["conflict", String(f.id), "vault"]);
                p.showNotice("conflict-resolved",
                    "Conflict resolved: vault kept.");
                this.refresh();
            });
            this.addButton(actions, "Keep External", async () => {
                await runRouter(p, ["conflict", String(f.id), "external"]);
                p.showNotice("conflict-resolved",
                    "Conflict resolved: external kept.");
                this.refresh();
            });
        } else if (f.status === "managed" && f.external_exists) {
            this.addButton(actions, "Overwrite External", async () => {
                await runRouter(p, ["sync-push", String(f.id)]);
                p.showNotice("sync-push",
                    "Vault copy pushed to external file.");
                this.refresh();
            });
        }
        this.addButton(actions, "Move to…", () => {
            new FolderSuggestModal(this.app, p, f, () => this.refresh()).open();
        });
        if (f.external_exists) {
            this.addButton(actions, "Delete External", () => {
                new ConfirmModal(this.app,
                    "Delete the external file?\n\n" + f.original_path +
                    "\n\nOnly the vault copy will remain (" +
                    f.vault_path + ").",
                    async () => {
                        const r = await runRouter(p,
                            ["delete-external", String(f.id)]);
                        p.showNotice("delete-external", r.data && r.data.ok
                            ? "External file deleted. Vault copy is now permanent."
                            : "Delete failed: " +
                              (r.data && r.data.error || "error"));
                        this.refresh();
                    }).open();
            });
        }
        this.addButton(actions, "History", async () => {
            const h = await runRouter(p, ["history", String(f.id)]);
            this.showHistory(f, Array.isArray(h.data) ? h.data : []);
        });
    }


    async renderDeletedFiles(deletedEl) {
        const p = this.plugin;
        const deletedFiles = await p.refreshDeletedMetadata();
        if (deletedFiles.length === 0) return;

        deletedEl.createEl("h3", { text: "Deleted-Notes (auto-purge)" });
        for (const f of deletedFiles) {
            const row = deletedEl.createDiv("obsidian-intake-row");
            row.style.cssText =
                "display:flex;align-items:center;gap:8px;padding:6px 0;" +
                "border-bottom:1px solid var(--background-modifier-border);";
            const info = row.createDiv();
            info.style.flex = "1";
            const nameRow = info.createDiv();
            nameRow.style.cssText = "display:flex;align-items:center;gap:8px;";
            const name = nameRow.createEl("span", { text: f.vault_path });
            if (f.expiring_soon) name.style.color = "var(--text-error)";
            const dayBadge = nameRow.createEl("span", {
                text: f.delete_protected
                    ? "∞"
                    : String(Math.max(0,
                        Math.ceil(Number(f.remaining_seconds || 0) / 86400))),
            });
            dayBadge.title = f.delete_protected
                ? "Automatic deletion is disabled"
                : "Days until automatic deletion";
            dayBadge.style.cssText =
                "min-width:22px;padding:0 5px;border-radius:10px;text-align:center;" +
                "font-size:10px;background:var(--background-modifier-hover);" +
                "color:" + (f.expiring_soon
                    ? "var(--text-error)" : "var(--text-muted)") + ";";

            const st = info.createEl("div", {
                text: f.delete_protected
                    ? "Automatic deletion disabled"
                    : "Deletes in " + formatRemaining(f.remaining_seconds),
            });
            st.style.fontSize = "0.8em";
            st.style.color = f.expiring_soon
                ? "var(--text-error)" : "var(--text-muted)";

            const actions = row.createDiv();
            actions.style.cssText = "display:flex;gap:4px;flex-wrap:wrap;";
            const restoreBtn = actions.createEl("button", { text: "Restore" });
            restoreBtn.title = "Restore this copy to " +
                (p.settings.managedFolder || "Not-Indexed");
            restoreBtn.style.fontSize = "0.8em";
            restoreBtn.addEventListener("click", async () => {
                await p.restoreDeletedFile(f.vault_path);
                this.refresh();
            });

            const protectBtn = actions.createEl("button", {
                text: f.delete_protected ? "Resume auto-delete" : "Keep indefinitely",
            });
            protectBtn.style.fontSize = "0.8em";
            protectBtn.addEventListener("click", async () => {
                await p.toggleDeleteProtection(f.vault_path);
                this.refresh();
            });

            const purgeBtn = actions.createEl("button", { text: "Purge now" });
            purgeBtn.style.fontSize = "0.8em";
            purgeBtn.addEventListener("click", () => {
                new ConfirmModal(this.app,
                    "Purge now?\n\n" + f.vault_path +
                    "\n\nThe vault copy will be deleted permanently.",
                    async () => {
                        await runRouter(p, ["purge", String(f.id)]);
                        p.showNotice("purged", "Vault copy deleted.");
                        await p.refreshDeletedMetadata();
                        this.refresh();
                    }).open();
            });
        }
    }

    addButton(container, text, cb, cta) {
        const b = container.createEl("button", { text });
        b.style.fontSize = "0.8em";
        if (cta) b.addClass("mod-cta");
        b.addEventListener("click", cb);
        return b;
    }

    showHistory(file, events) {
        const modal = new Modal(this.app);
        modal.contentEl.createEl("h3", { text: "History: " + file.vault_path });
        const pre = modal.contentEl.createEl("pre");
        pre.style.fontSize = "0.75em";
        pre.textContent = events.length
            ? events.map((e) =>
                e.timestamp + "  " + e.event +
                (e.old_value != null ? "  | " + e.old_value : "") +
                (e.new_value != null ? " -> " + e.new_value : "")
            ).join("\n")
            : "No events.";
        modal.open();
    }

    async refresh() {
        await this.plugin.updateStatusBar();
        await this.onOpen();
    }

    onClose() {
        this.contentEl.empty();
    }
}



/* Global event timeline (dashboard "History" button). */
function showTimelineModal(app, events) {
    const modal = new Modal(app);
    modal.contentEl.createEl("h3", { text: "Change history" });
    const pre = modal.contentEl.createEl("pre");
    pre.style.fontSize = "0.75em";
    pre.style.maxHeight = "60vh";
    pre.style.overflow = "auto";
    pre.textContent = events.length
        ? events.map((e) =>
            e.timestamp + "  #" + e.file_id + "  " + e.event +
            (e.vault_path ? "  [" + e.vault_path + "]" : "") +
            (e.old_value != null ? "  | " + e.old_value : "") +
            (e.new_value != null ? " -> " + e.new_value : "")
        ).join("\n")
        : "No events yet.";
    modal.open();
}

/* Human readable remaining time for the Deleted-Notes countdown. */
function formatRemaining(seconds) {
    if (seconds <= 0) return "now";
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    if (days >= 1) return days + "d " + hours + "h";
    const minutes = Math.floor((seconds % 3600) / 60);
    if (hours >= 1) return hours + "h " + minutes + "m";
    return minutes + "m";
}

/*
 * Vault folder picker for "Move to…" — fuzzy search over vault folders.
 * On choose, moves the vault copy there; later external clicks on the
 * original file open it at the new vault location.
 */
class FolderSuggestModal extends FuzzySuggestModal {
    constructor(app, plugin, file, onDone) {
        super(app);
        this.plugin = plugin;
        this.file = file;
        this.onDone = onDone;
        this.setPlaceholder(
            "Vault folder to move " + file.vault_path + " to"
        );
    }

    getItems() {
        const folders = [];
        const walk = (folder) => {
            for (const child of folder.children) {
                if (child instanceof TFolder) {
                    folders.push(child);
                    walk(child);
                }
            }
        };
        walk(this.plugin.app.vault.getRoot());
        return folders;
    }

    getItemText(folder) {
        return folder.path;
    }

    onChooseItem(folder) {
        runRouter(this.plugin,
            ["move", String(this.file.id), folder.path])
            .then((res) => {
                this.plugin.showNotice("moved", res.data && res.data.ok
                    ? "Moved to " + res.data.file.vault_path +
                      ". External clicks now open at the new location."
                    : "Move failed: " +
                      (res.data && res.data.error || "error"));
                if (this.onDone) this.onDone();
            });
    }
}

/* Simple confirmation modal for destructive actions. */
class ConfirmModal extends Modal {
    constructor(app, message, onConfirm) {
        super(app);
        this.message = message;
        this.onConfirm = onConfirm;
    }

    onOpen() {
        const p = this.contentEl.createEl("p", { text: this.message });
        p.style.whiteSpace = "pre-wrap";
        const row = this.contentEl.createDiv();
        row.style.cssText =
            "display:flex;gap:8px;justify-content:flex-end;margin-top:14px;";
        const cancel = row.createEl("button", { text: "Cancel" });
        cancel.addEventListener("click", () => this.close());
        const ok = row.createEl("button", { text: "Confirm" });
        ok.addClass("mod-warning");
        ok.addEventListener("click", () => {
            this.close();
            this.onConfirm();
        });
    }

    onClose() {
        this.contentEl.empty();
    }
}

module.exports = class ObsidianIntakePlugin extends Plugin {

    async onload() {
        this.settings = Object.assign({}, DEFAULTS, await this.loadData());

        // Per-session UI state. Durable Deleted-Notes/recovery state lives in SQLite.
        this.dirtyPaths = new Set();
        this.noticedPaths = new Set();
        this.tempReadPaths = new Set();
        this.tempReadDecided = new Set();
        this.tempReadForcedOff = new Set();
        this.autoSavePaths = new Set();
        this.banner = null;
        this.bannerFile = null;
        this.autoSaveTimers = {};
        this.tempCloseTimer = null;
        this.deletedMetaCache = new Map();
        this.deletedBadgeTimer = null;
        this.deletedBadgeObserver = null;

        this.addRibbonIcon("external-link", "Obsidian Intake", () =>
            this.openDashboard()
        );

        this.addCommand({
            id: "open-dashboard",
            name: "Open Obsidian Intake dashboard",
            callback: () => this.openDashboard(),
        });
        this.addCommand({
            id: "toggle-scroll-buttons",
            name: "Toggle top/bottom scroll buttons",
            callback: async () => {
                this.settings.showScrollButtons = !this.settings.showScrollButtons;
                await this.saveSettings();
                this.applyScrollButtons();
            },
        });

        this.statusBarItem = this.addStatusBarItem();
        this.updateStatusBar();
        this.applyScrollButtons();
        this.applyGraphExclusion();

        await runRouter(this, ["cleanup-deleted"]);
        await this.refreshDeletedMetadata();
        this.installDeletedBadgeObserver();
        this.registerInterval(window.setInterval(() => {
            this.refreshDeletedMetadata();
        }, 60 * 1000));

        // Edit warning: once per opened managed file.
        this.registerEvent(
            this.app.vault.on("modify", async (file) => {
                if (!(file instanceof TFile)) return;
                if (!this.isManagedFile(file.path)) return;
                this.dirtyPaths.add(file.path);
                if (this.banner && this.bannerFile === file.path) {
                    this.banner.updateSaveState();
                }
                if (this.autoSavePaths.has(file.path) &&
                    !this.isInDeletedFolder(file.path)) {
                    this.scheduleAutoSave(file.path);
                }
                if (!this.noticedPaths.has(file.path)) {
                    this.noticedPaths.add(file.path);
                    const r = await runRouter(this, ["check", file.path]);
                    if (r.data && r.data.found && r.data.conflict) {
                        this.showNotice("conflict",
                            "Conflict: external and vault copies both " +
                            "changed. Open Obsidian Intake dashboard.");
                    } else if (!this.isInDeletedFolder(file.path)) {
                        this.showNotice("managed-edit",
                            "You are editing a managed copy.");
                    }
                }
            })
        );

        // Active-view/banner updates and close detection for ALL file-view types,
        // not only MarkdownView. This fixes txt/py/etc. with supporting views.
        this.registerEvent(
            this.app.workspace.on("active-leaf-change", () => {
                this.scheduleTempReadCloseCheck();
                this.updateBanner();
            })
        );
        this.registerEvent(
            this.app.workspace.on("layout-change", () => {
                this.scheduleTempReadCloseCheck();
                this.scheduleDeletedBadgeApply();
            })
        );
        this.registerEvent(
            this.app.workspace.on("file-open", () => {
                this.scheduleTempReadCloseCheck();
                this.updateBanner();
            })
        );
        this.updateBanner();

        // Keep DB paths and lifecycle state in sync for manual Vault renames/moves
        // as well as router-side filesystem moves detected by Obsidian.
        this.registerEvent(
            this.app.vault.on("rename", async (file, oldPath) => {
                if (!(file instanceof TFile)) return;
                const wasDeleted = this.isInDeletedFolder(oldPath);
                const nowDeleted = this.isInDeletedFolder(file.path);
                if (wasDeleted !== nowDeleted && this.bannerFile === oldPath) {
                    this.detachBanner();
                }
                this.moveSessionPath(oldPath, file.path);
                const leftDeleted = wasDeleted && !nowDeleted;
                if (leftDeleted && this.isInManagedFolder(file.path)) {
                    this.forceTemporaryReadOff(file.path);
                }
                await runRouter(this, ["vault-renamed", oldPath, file.path]);
                await this.refreshDeletedMetadata();
                this.updateBanner();
            })
        );

        // Deleted-Notes context menu.
        this.registerEvent(
            this.app.workspace.on("file-menu", (menu, file) => {
                if (!(file instanceof TFile) || !this.isInDeletedFolder(file.path)) {
                    return;
                }
                const meta = this.deletedMetaCache.get(file.path);
                const protectedMode = !!(meta && meta.delete_protected);
                menu.addItem((item) => item
                    .setTitle("Restore to " +
                        (this.settings.managedFolder || "Not-Indexed"))
                    .setIcon("rotate-ccw")
                    .onClick(() => this.restoreDeletedFile(file.path))
                );
                menu.addItem((item) => item
                    .setTitle(protectedMode
                        ? "Delete automatically after the retention limit"
                        : "Do not delete after the retention limit")
                    .setIcon(protectedMode ? "timer" : "shield")
                    .onClick(() => this.toggleDeleteProtection(file.path))
                );
            })
        );

        this.addSettingTab(new ObsidianIntakeSettingTab(this.app, this));
    }

    onunload() {
        this.detachBanner();
        if (this.scrollBox) this.scrollBox.remove();
        if (this.deletedBadgeObserver) this.deletedBadgeObserver.disconnect();
        if (this.tempCloseTimer) clearTimeout(this.tempCloseTimer);
        if (this.deletedBadgeTimer) clearTimeout(this.deletedBadgeTimer);
    }

    isInManagedFolder(vaultPath) {
        const folder = this.settings.managedFolder || "Not-Indexed";
        return vaultPath === folder || vaultPath.startsWith(folder + "/");
    }

    isInDeletedFolder(vaultPath) {
        const folder = this.settings.deletedFolder || "Deleted-Notes";
        return vaultPath === folder || vaultPath.startsWith(folder + "/");
    }

    isManagedFile(vaultPath) {
        return this.isInManagedFolder(vaultPath) ||
            this.isInDeletedFolder(vaultPath);
    }

    showNotice(type, message) {
        new IntakeNotice(this, type, message);
    }

    async setNoticeAutoDismiss(type, value) {
        const memory = Object.assign({}, this.settings.noticeAutoDismiss || {});
        memory[type] = value;
        this.settings.noticeAutoDismiss = memory;
        await this.saveData(this.settings);
    }

    /* ---- Document banner ---- */

    activeFileView() {
        const leaf = this.app.workspace.activeLeaf;
        const view = leaf && leaf.view ? leaf.view : null;
        let file = view && view.file instanceof TFile ? view.file : null;
        if (!file && view && typeof view.getState === "function") {
            const state = view.getState();
            if (state && typeof state.file === "string") {
                const af = this.app.vault.getAbstractFileByPath(state.file);
                if (af instanceof TFile) file = af;
            }
        }
        if (!file) file = this.app.workspace.getActiveFile();
        return { view, file };
    }

    updateBanner() {
        const { view, file } = this.activeFileView();
        const hasHost = !!(view && (view.contentEl || view.containerEl));
        if (!hasHost || !file || !this.isManagedFile(file.path)) {
            this.detachBanner();
            return;
        }
        if (this.banner && this.bannerFile === file.path) return;
        this.detachBanner();
        this.bannerFile = file.path;
        this.banner = new IntakeBanner(this, view, file);
    }

    detachBanner() {
        if (this.banner) {
            this.banner.destroy();
            this.banner = null;
        }
        this.bannerFile = null;
    }

    /* ---- Temporary read lifecycle ---- */

    getOpenFilePaths() {
        const paths = new Set();
        if (typeof this.app.workspace.iterateAllLeaves === "function") {
            this.app.workspace.iterateAllLeaves((leaf) => {
                const view = leaf && leaf.view;
                const f = view && view.file;
                if (f instanceof TFile) {
                    paths.add(f.path);
                    return;
                }
                if (view && typeof view.getState === "function") {
                    const state = view.getState();
                    if (state && typeof state.file === "string") {
                        paths.add(state.file);
                    }
                }
            });
        } else {
            const active = this.app.workspace.getActiveFile();
            if (active instanceof TFile) paths.add(active.path);
        }
        return paths;
    }

    scheduleTempReadCloseCheck() {
        if (this.tempCloseTimer) clearTimeout(this.tempCloseTimer);
        this.tempCloseTimer = setTimeout(() => {
            this.tempCloseTimer = null;
            this.handleTempReadClose();
        }, 120);
    }

    async handleTempReadClose() {
        const openPaths = this.getOpenFilePaths();
        for (const vaultPath of Array.from(this.tempReadPaths)) {
            if (openPaths.has(vaultPath)) continue;
            this.tempReadPaths.delete(vaultPath);
            const r = await runRouter(this, ["check", vaultPath]);
            if (!r.data || !r.data.found || !r.data.id ||
                this.isInDeletedFolder(vaultPath)) {
                continue;
            }
            const moved = await runRouter(this, [
                "move", String(r.data.id),
                this.settings.deletedFolder || "Deleted-Notes",
            ]);
            if (moved.data && moved.data.ok) {
                this.showNotice("temporary-read",
                    "Temporary read finished. Moved to " +
                    (this.settings.deletedFolder || "Deleted-Notes") + ".");
                await this.refreshDeletedMetadata();
            }
        }
    }

    forceTemporaryReadOff(pathValue) {
        this.tempReadPaths.delete(pathValue);
        this.tempReadDecided.add(pathValue);
        this.tempReadForcedOff.add(pathValue);
    }

    moveSessionPath(oldPath, newPath) {
        const moveSet = (set) => {
            if (set.has(oldPath)) {
                set.delete(oldPath);
                set.add(newPath);
            }
        };
        moveSet(this.dirtyPaths);
        moveSet(this.noticedPaths);
        moveSet(this.tempReadPaths);
        moveSet(this.tempReadDecided);
        moveSet(this.tempReadForcedOff);
        moveSet(this.autoSavePaths);
        if (this.autoSaveTimers[oldPath]) {
            this.autoSaveTimers[newPath] = this.autoSaveTimers[oldPath];
            delete this.autoSaveTimers[oldPath];
        }
        if (this.bannerFile === oldPath) this.bannerFile = newPath;
    }

    async restoreDeletedFile(vaultPath) {
        const check = await runRouter(this, ["check", vaultPath]);
        if (!check.data || !check.data.found || !check.data.id) {
            this.showNotice("restore-deleted", "Could not find the tracked copy.");
            return;
        }
        const r = await runRouter(this,
            ["restore-deleted", String(check.data.id)]);
        if (!r.data || !r.data.ok) {
            this.showNotice("restore-deleted", "Restore failed: " +
                (r.data && r.data.error || "error"));
            return;
        }
        const newPath = r.data.file.vault_path;
        this.moveSessionPath(vaultPath, newPath);
        this.forceTemporaryReadOff(newPath);
        await this.refreshDeletedMetadata();
        this.showNotice("restore-deleted", "Restored to " + newPath + ".");
        await runRouter(this, ["open", newPath]);
        setTimeout(() => this.updateBanner(), 100);
    }

    async toggleDeleteProtection(vaultPath) {
        const check = await runRouter(this, ["check", vaultPath]);
        if (!check.data || !check.data.found || !check.data.id) return;
        const r = await runRouter(this,
            ["toggle-delete-protected", String(check.data.id)]);
        if (r.data && r.data.ok) {
            await this.refreshDeletedMetadata();
            if (this.banner && this.bannerFile === vaultPath) {
                await this.banner.refreshData();
            }
            this.showNotice("delete-protection", r.data.delete_protected
                ? "Automatic deletion disabled for this copy."
                : "Automatic deletion enabled for this copy.");
        }
    }

    /* ---- Deleted-Notes explorer countdown badges ---- */

    async refreshDeletedMetadata() {
        const r = await runRouter(this, ["list-deleted"]);
        const items = Array.isArray(r.data) ? r.data : [];
        this.deletedMetaCache = new Map(items.map((x) => [x.vault_path, x]));
        this.applyDeletedFileBadges();
        return items;
    }

    installDeletedBadgeObserver() {
        if (this.deletedBadgeObserver) this.deletedBadgeObserver.disconnect();
        this.deletedBadgeObserver = new MutationObserver((records) => {
            const ownClass = "obsidian-intake-delete-countdown";
            const relevant = records.some((record) => {
                const nodes = [
                    ...Array.from(record.addedNodes || []),
                    ...Array.from(record.removedNodes || []),
                ];
                if (nodes.length === 0) return true;
                return nodes.some((node) => {
                    if (!(node instanceof HTMLElement)) return true;
                    return !node.classList.contains(ownClass);
                });
            });
            if (relevant) this.scheduleDeletedBadgeApply();
        });
        this.deletedBadgeObserver.observe(document.body, {
            childList: true,
            subtree: true,
        });
        this.applyDeletedFileBadges();
    }

    scheduleDeletedBadgeApply() {
        if (this.deletedBadgeTimer) return;
        this.deletedBadgeTimer = setTimeout(() => {
            this.deletedBadgeTimer = null;
            this.applyDeletedFileBadges();
        }, 80);
    }

    applyDeletedFileBadges() {
        document.querySelectorAll(".obsidian-intake-delete-countdown")
            .forEach((el) => el.remove());
        document.querySelectorAll(".obsidian-intake-extension-tag")
            .forEach((el) => {
                el.style.marginLeft =
                    el.dataset.obsidianIntakePrevMarginLeft || "";
                el.style.marginRight =
                    el.dataset.obsidianIntakePrevMarginRight || "";
                delete el.dataset.obsidianIntakePrevMarginLeft;
                delete el.dataset.obsidianIntakePrevMarginRight;
                el.classList.remove("obsidian-intake-extension-tag");
            });
        if (!this.deletedMetaCache || this.deletedMetaCache.size === 0) return;

        const candidates = document.querySelectorAll(
            ".nav-file-title[data-path], .tree-item-self[data-path]"
        );
        for (const el of candidates) {
            const pathValue = el.getAttribute("data-path");
            const meta = this.deletedMetaCache.get(pathValue);
            if (!meta) continue;
            const badge = document.createElement("span");
            badge.className = "obsidian-intake-delete-countdown";
            const protectedMode = !!meta.delete_protected;
            const days = Math.max(0,
                Math.ceil(Number(meta.remaining_seconds || 0) / 86400));
            badge.textContent = protectedMode ? "∞" : String(days);
            badge.title = protectedMode
                ? "Automatic deletion is disabled"
                : days + " day(s) until automatic deletion";
            // Keep Obsidian's extension tag (PY, TXT, …) directly beside
            // the countdown badge. The extension tag owns the auto margin,
            // so the pair stays grouped at the far-right edge of the row.
            let extTag = el.querySelector(
                ".nav-file-tag, .tree-item-flair, .nav-file-title-tag"
            );
            // Theme/Obsidian-version fallback: identify a direct-child tag
            // whose visible text equals the file extension (e.g. PY).
            if (!extTag) {
                const base = String(pathValue || "").split("/").pop() || "";
                const dot = base.lastIndexOf(".");
                const expectedExt = dot >= 0
                    ? base.slice(dot + 1).trim().toUpperCase()
                    : "";
                if (expectedExt) {
                    extTag = Array.from(el.children || []).find((child) => {
                        const text = (child.textContent || "").trim().toUpperCase();
                        return text === expectedExt;
                    }) || null;
                }
            }
            if (extTag) {
                extTag.dataset.obsidianIntakePrevMarginLeft =
                    extTag.style.marginLeft || "";
                extTag.dataset.obsidianIntakePrevMarginRight =
                    extTag.style.marginRight || "";
                extTag.classList.add("obsidian-intake-extension-tag");
                extTag.style.marginLeft = "auto";
                extTag.style.marginRight = "5px";
            }
            badge.style.cssText =
                (extTag ? "margin-left:0;" : "margin-left:auto;") +
                "min-width:22px;padding:0 5px;border-radius:10px;" +
                "text-align:center;font-size:10px;line-height:18px;" +
                "flex:0 0 auto;background:var(--background-modifier-hover);" +
                "color:" + (meta.expiring_soon
                    ? "var(--text-error)" : "var(--text-muted)") + ";";
            el.appendChild(badge);
        }
    }

    /* ---- Auto save to main ---- */

    scheduleAutoSave(pathValue) {
        if (this.autoSaveTimers[pathValue]) {
            clearTimeout(this.autoSaveTimers[pathValue]);
        }
        this.autoSaveTimers[pathValue] = setTimeout(async () => {
            delete this.autoSaveTimers[pathValue];
            const r = await runRouter(this, ["check", pathValue]);
            if (r.data && r.data.found && r.data.id &&
                !r.data.in_sync && r.data.external_exists &&
                !r.data.recovery_pending) {
                const res = await runRouter(this,
                    ["sync-push", String(r.data.id)]);
                if (res.data && res.data.ok) {
                    this.dirtyPaths.delete(pathValue);
                    if (this.banner && this.bannerFile === pathValue) {
                        this.banner.updateSaveState();
                    }
                }
            }
        }, 2000);
    }

    /* ---- Scroll-to-top / scroll-to-bottom buttons ---- */

    applyScrollButtons() {
        if (!this.scrollBox) {
            const box = document.createElement("div");
            box.className = "obsidian-intake-scroll-buttons";
            box.style.cssText =
                "position:fixed;right:20px;bottom:60px;z-index:9998;" +
                "display:flex;flex-direction:column;gap:8px;";
            const mk = (label, dir) => {
                const b = document.createElement("button");
                b.textContent = label;
                b.setAttribute("aria-label",
                    dir === "top" ? "Scroll to top" : "Scroll to bottom");
                b.title = dir === "top" ? "Scroll to top" : "Scroll to bottom";
                b.style.cssText =
                    "width:36px;height:36px;border-radius:50%;" +
                    "cursor:pointer;font-size:16px;line-height:1;" +
                    "border:1px solid var(--background-modifier-border);" +
                    "background:var(--background-secondary-alt);" +
                    "color:var(--text-normal);box-shadow:0 2px 8px rgba(0,0,0,0.25);";
                b.addEventListener("click", () => this.scrollDoc(dir));
                return b;
            };
            box.appendChild(mk("↑", "top"));
            box.appendChild(mk("↓", "bottom"));
            document.body.appendChild(box);
            this.scrollBox = box;
        }
        this.scrollBox.style.display =
            this.settings.showScrollButtons ? "flex" : "none";
    }

    scrollDoc(dir) {
        const leaf = this.app.workspace.activeLeaf;
        const view = leaf && leaf.view ? leaf.view : null;
        if (!view) return;
        const root = view.containerEl || view.contentEl;
        const selectors = [
            ".cm-scroller",
            ".CodeMirror-scroll",
            ".markdown-preview-view",
            ".markdown-reading-view",
            ".view-content",
        ];
        let scroller = null;
        if (root && root.querySelector) {
            for (const selector of selectors) {
                const el = root.querySelector(selector);
                if (el && el.scrollHeight > el.clientHeight + 2) {
                    scroller = el;
                    break;
                }
            }
        }
        if (!scroller && view.contentEl &&
            view.contentEl.scrollHeight > view.contentEl.clientHeight + 2) {
            scroller = view.contentEl;
        }
        if (scroller) {
            const top = dir === "top" ? 0 : scroller.scrollHeight;
            if (typeof scroller.scrollTo === "function") {
                try { scroller.scrollTo({ top, left: 0, behavior: "smooth" }); }
                catch (e) { scroller.scrollTop = top; }
            } else {
                scroller.scrollTop = top;
            }
            return;
        }
        // Last-resort editor API fallback for themes/views with hidden scrollers.
        if (view.editor && typeof view.editor.scrollIntoView === "function") {
            const lastLine = Math.max(0, view.editor.lineCount() - 1);
            const pos = dir === "top"
                ? { line: 0, ch: 0 }
                : { line: lastLine, ch: view.editor.getLine(lastLine).length };
            view.editor.scrollIntoView({ from: pos, to: pos }, true);
        }
    }

    async updateStatusBar() {
        const r = await runRouter(this, ["status"]);
        if (r.data && typeof r.data.total === "number") {
            const s = r.data;
            this.statusBarItem.setText(
                "Obsidian Intake: " + s.total +
                (s.conflict ? " (" + s.conflict + " conflict)" : "")
            );
        }
    }

    async applyGraphExclusion() {
        try {
            const adapter = this.app.vault.adapter;
            const file = ".obsidian/app.json";
            let data = {};
            try {
                data = JSON.parse(await adapter.read(file));
            } catch (e) { /* no app.json yet */ }
            const entry = (this.settings.managedFolder || "Not-Indexed") + "/";
            let filters = Array.isArray(data.userIgnoreFilters)
                ? data.userIgnoreFilters.slice()
                : [];
            const idx = filters.indexOf(entry);
            if (this.settings.excludeFromGraph && idx === -1) {
                filters.push(entry);
            } else if (!this.settings.excludeFromGraph && idx !== -1) {
                filters.splice(idx, 1);
            } else {
                return;
            }
            data.userIgnoreFilters = filters;
            await adapter.write(file, JSON.stringify(data, null, 2));
        } catch (e) {
            console.warn("Obsidian Intake: graph exclusion not applied:", e);
        }
    }

    openDashboard() {
        new DashboardModal(this.app, this).open();
    }

    async saveSettings() {
        await this.saveData(this.settings);
    }
};

class ObsidianIntakeSettingTab extends PluginSettingTab {
    constructor(app, plugin) {
        super(app, plugin);
        this.plugin = plugin;
    }

    display() {
        const { containerEl } = this;
        containerEl.empty();
        const pluginTitle = containerEl.createEl("h2", { text: "Obsidian Intake" });
        pluginTitle.classList.add("obsidian-intake-settings-title");
        pluginTitle.style.cssText =
            "font-size:1.7em;font-weight:750;line-height:1.15;" +
            "margin:0 0 18px 0;letter-spacing:-0.01em;";

        // ---- Managed extensions (checkboxes) ----
        containerEl.createEl("h3", { text: "Managed extensions" });
        for (const ext of CORE_EXTENSIONS) {
            const checked = this.plugin.settings.extensions.includes(ext);
            const native = OBSIDIAN_NATIVE_EXTS.includes(ext);
            const nameFrag = document.createDocumentFragment();
            nameFrag.appendChild(document.createTextNode("." + ext + " "));
            if (!native) {
                const span = document.createElement("span");
                span.textContent = "(needs plugin)";
                span.style.color = "var(--text-muted)";
                span.style.fontSize = "0.85em";
                nameFrag.appendChild(span);
            }
            new Setting(containerEl)
                .setName(nameFrag)
                .setDesc("Open ." + ext + " files with Obsidian Intake router")
                .addToggle((t) => {
                    t.setValue(checked);
                    t.onChange(async (v) => {
                        const list = this.plugin.settings.extensions.filter(
                            (x) => x !== ext
                        );
                        if (v) list.push(ext);
                        this.plugin.settings.extensions = list;
                        await this.plugin.saveSettings();
                    });
                });
        }

        // ---- Custom extensions (free text) ----
        new Setting(containerEl)
            .setName("Custom extensions")
            .setDesc(
                "Extra extensions beyond the checklist above, comma " +
                "separated. Example: ipynb, rmd, org, asciidoc. " +
                "Formats Obsidian does not open by default need a " +
                "supporting plugin."
            )
            .addText((t) => {
                t.setPlaceholder("Example: ipynb, rmd, org");
                t.setValue(
                    (this.plugin.settings.customExtensions || []).join(", ")
                );
                t.onChange(async (v) => {
                    this.plugin.settings.customExtensions = v
                        .split(",")
                        .map((x) => x.trim().replace(/^\.+/, ""))
                        .filter(Boolean);
                    await this.plugin.saveSettings();
                });
            });


        // ---- Files & folders ----
        containerEl.createEl("h3", { text: "Files & folders" });
        new Setting(containerEl)
            .setName("Managed folder")
            .setDesc("Vault folder used for imported (managed) copies")
            .addText((t) => {
                t.setValue(this.plugin.settings.managedFolder);
                t.onChange(async (v) => {
                    this.plugin.settings.managedFolder =
                        v.trim().replace(/^\/+|\/+$/g, "") || "Not-Indexed";
                    await this.plugin.saveSettings();
                });
            });

        new Setting(containerEl)
            .setName("Deleted-Notes folder")
            .setDesc(
                "Vault folder for temporary reads; files here are " +
                "purged automatically after the retention below"
            )
            .addText((t) => {
                t.setValue(this.plugin.settings.deletedFolder);
                t.onChange(async (v) => {
                    this.plugin.settings.deletedFolder =
                        v.trim().replace(/^\/+|\/+$/g, "") || "Deleted-Notes";
                    await this.plugin.saveSettings();
                });
            });

        new Setting(containerEl)
            .setName("Deleted-Notes retention")
            .setDesc(
                "How long files stay in the Deleted-Notes folder " +
                "before automatic deletion (default: 20 days)"
            )
            .addText((t) => {
                t.setValue(String(this.plugin.settings.deletedRetentionValue));
                t.onChange(async (v) => {
                    const n = Number(v);
                    this.plugin.settings.deletedRetentionValue =
                        n > 0 ? n : 20;
                    await this.plugin.saveSettings();
                });
            })
            .addDropdown((d) => {
                d.addOption("days", "Days");
                d.addOption("months", "Months");
                d.addOption("years", "Years");
                d.setValue(this.plugin.settings.deletedRetentionUnit || "days");
                d.onChange(async (v) => {
                    this.plugin.settings.deletedRetentionUnit = v;
                    await this.plugin.saveSettings();
                });
            });

        new Setting(containerEl)
            .setName("Temporary read by default")
            .setDesc(
                "Pre-check the 'Temporary read' checkbox in the document " +
                "banner, so copies move to Deleted-Notes when closed"
            )
            .addToggle((t) => {
                t.setValue(this.plugin.settings.temporaryReadDefault);
                t.onChange(async (v) => {
                    this.plugin.settings.temporaryReadDefault = v;
                    await this.plugin.saveSettings();
                });
            });

        new Setting(containerEl)
            .setName("Write source path on import")
            .setDesc(
                "Add the original file path as a gray comment (%% ... %%) " +
                "on the first line of imported copies"
            )
            .addToggle((t) => {
                t.setValue(this.plugin.settings.writeSourceHeader);
                t.onChange(async (v) => {
                    this.plugin.settings.writeSourceHeader = v;
                    await this.plugin.saveSettings();
                });
            });

        new Setting(containerEl)
            .setName("Exclude managed folder from graph")
            .setDesc(
                "Hide the managed folder from graph view and search " +
                "(uses Obsidian 'Excluded files'; reload Obsidian if the " +
                "graph does not refresh). Ignored if unsupported."
            )
            .addToggle((t) => {
                t.setValue(this.plugin.settings.excludeFromGraph);
                t.onChange(async (v) => {
                    this.plugin.settings.excludeFromGraph = v;
                    await this.plugin.saveSettings();
                    await this.plugin.applyGraphExclusion();
                });
            });


        // ---- Interface & navigation ----
        containerEl.createEl("h3", { text: "Interface & navigation" });
        new Setting(containerEl)
            .setName("Show top/bottom scroll buttons")
            .setDesc(
                "Show the floating ↑/↓ buttons in the bottom-right corner. " +
                "They jump to the actual top/bottom scroll container of the " +
                "active document and can be disabled here at any time."
            )
            .addToggle((t) => {
                t.setValue(this.plugin.settings.showScrollButtons);
                t.onChange(async (v) => {
                    this.plugin.settings.showScrollButtons = v;
                    await this.plugin.saveSettings();
                    this.plugin.applyScrollButtons();
                });
            });

        // ---- Notifications ----
        containerEl.createEl("h3", { text: "Notifications" });
        new Setting(containerEl)
            .setName("Auto dismiss delay (seconds)")
            .setDesc(
                "Seconds before a notification disappears when its " +
                "'Auto dismiss' checkbox is ticked (default: 7)"
            )
            .addText((t) => {
                t.setValue(String(this.plugin.settings.noticeTimeout));
                t.onChange(async (v) => {
                    const n = Number(v);
                    this.plugin.settings.noticeTimeout = n > 0 ? n : 7;
                    await this.plugin.saveSettings();
                });
            });

        new Setting(containerEl)
            .setName("Reset auto dismiss memory")
            .setDesc(
                "Forget all ticked 'Auto dismiss' checkboxes so every " +
                "notification requires an OK click again"
            )
            .addButton((b) => {
                b.setButtonText("Reset").onClick(async () => {
                    this.plugin.settings.noticeAutoDismiss = {};
                    await this.plugin.saveSettings();
                    this.plugin.showNotice("settings-reset",
                        "Auto dismiss memory cleared.");
                });
            });

        // ---- Router ----
        containerEl.createEl("h3", { text: "Router" });
        new Setting(containerEl)
            .setName("Router path")
            .setDesc("Absolute path of the obsidian-intake-router executable")
            .addText((t) => {
                t.setValue(this.plugin.settings.routerPath);
                t.onChange(async (v) => {
                    this.plugin.settings.routerPath = v.trim();
                    await this.plugin.saveSettings();
                });
            });

        new Setting(containerEl)
            .setName("Check router")
            .setDesc("Run 'status' to verify the connection")
            .addButton((b) => {
                b.setButtonText("Test").onClick(async () => {
                    const r = await runRouter(this.plugin, ["status"]);
                    if (r.data && typeof r.data.total === "number") {
                        this.plugin.showNotice("router-test",
                            "Router OK — " + r.data.total + " file(s) tracked.");
                    } else {
                        this.plugin.showNotice("router-test",
                            "Router NOT reachable. Check the path.");
                    }
                });
            });


        // ---- About ----
        containerEl.createEl("h3", { text: "About" });

        // --- Disclaimer (below the About heading, above the about box) ---
        const disclaimerEl = containerEl.createEl("div", {
            text: "Obsidian Intake is an independent project and has no affiliation, " +
                "endorsement, or support from Obsidian (Dynalist Inc.).",
        });
        disclaimerEl.style.cssText =
            "font-size:0.9em;color:var(--text-muted);margin:-8px 16px 10px 16px;";

        // --- About box ---
        const aboutBox = containerEl.createDiv("obsidian-intake-about");
        aboutBox.style.cssText =
            "padding:12px 14px;margin:4px 0 8px;border-radius:8px;" +
            "background:var(--background-secondary);" +
            "border:1px solid var(--background-modifier-border);";
        const aboutDesc = aboutBox.createEl("p", { text: ABOUT_INFO.description });
        aboutDesc.style.cssText = "margin:0 0 8px 0;line-height:1.5;";
        const version = (this.plugin.manifest && this.plugin.manifest.version) || "unknown";
        const versionEl = aboutBox.createEl("div", { text: "Version " + version });
        versionEl.style.cssText =
            "font-size:0.9em;color:var(--text-muted);margin-bottom:4px;";
        const desktopEl = aboutBox.createEl("div", {
            text: "Desktop-only helper for controlled external-file intake.",
        });
        desktopEl.style.cssText = "font-size:0.9em;color:var(--text-muted);";

        const addAboutLink = (name, desc, url, buttonText) => {
            new Setting(containerEl)
                .setName(name)
                .setDesc(url ? desc + " — " + url : desc + " (not configured yet)")
                .addButton((b) => {
                    b.setButtonText(buttonText);
                    if (!url) {
                        b.setDisabled(true);
                        b.buttonEl.title =
                            "Set this URL in ABOUT_INFO near the top of extension/main.js";
                        return;
                    }
                    b.buttonEl.title = url;
                    b.onClick(() => {
                        try {
                            window.open(url, "_blank", "noopener,noreferrer");
                        } catch (e) {
                            this.plugin.showNotice("about-link",
                                "Could not open: " + url);
                        }
                    });
                });
        };

        addAboutLink(
            "GitHub repository",
            "Source code, releases, issues and project documentation",
            ABOUT_INFO.githubUrl,
            "GitHub"
        );
        addAboutLink(
            "Support / Donate",
            "Optional link for supporting development",
            ABOUT_INFO.donateUrl,
            "Donate"
        );
    }
}

