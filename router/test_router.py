#!/usr/bin/env python3
"""End-to-end tests for the Obsidian Intake router (v1.4.2).

Run:  python3 router/test_router.py
Uses temporary vault/config directories and never opens Obsidian.
Covers: import, duplicate, sync, conflict, migrate, source-path header,
custom extensions and plugin settings handling.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROUTER = Path(__file__).resolve().parent / "obsidian-intake-router"


def run(args, env):
    return subprocess.run(
        [sys.executable, str(ROUTER)] + args,
        env=env, capture_output=True, text=True, timeout=60,
    )


def write_plugin_data(vault, data):
    p = (vault / ".obsidian" / "plugins" / "obsidian-intake")
    p.mkdir(parents=True, exist_ok=True)
    (p / "data.json").write_text(json.dumps(data), encoding="utf-8")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="obsidian-intake-test-"))
    env = dict(os.environ)
    env.update({
        "MD_INTAKE_CONFIG": str(tmp / "config"),
        "MD_INTAKE_VAULT": str(tmp / "vault"),
        "MD_INTAKE_NO_OPEN": "1",
    })
    vault = tmp / "vault"
    downloads = tmp / "downloads"
    downloads.mkdir(parents=True)
    (vault / "Not-Indexed").mkdir(parents=True)

    # plugin settings: source header ON + custom extension 'org'
    write_plugin_data(vault, {
        "extensions": ["md", "txt", "yaml", "json", "csv", "log"],
        "customExtensions": ["org"],
        "writeSourceHeader": True,
    })

    ok = True

    def check(name, cond, extra=""):
        nonlocal ok
        print(("PASS" if cond else "FAIL"), name, extra)
        if not cond:
            ok = False

    src = downloads / "report.md"
    src.write_text("hello report v1", encoding="utf-8")

    # 1) import with source header
    r = run([str(src)], env)
    vault_copy = vault / "Not-Indexed/report.md"
    first_line = vault_copy.read_text(encoding="utf-8").split("\n", 1)[0]
    check("import adds source header",
          first_line.startswith("%% obsidian-intake source: ") and
          str(src) in first_line, first_line)
    check("header then original content",
          "hello report v1" in vault_copy.read_text(encoding="utf-8"))

    recs = json.loads(run(["list"], env).stdout)
    check("import creates record",
          len(recs) == 1 and recs[0]["status"] == "managed")

    # 2) duplicate by content
    dup = downloads / "copy.md"
    dup.write_text("hello report v1", encoding="utf-8")
    run([str(dup)], env)
    recs = json.loads(run(["list"], env).stdout)
    check("no duplicate copy", len(recs) == 1)

    # 3) external change -> auto sync, header preserved
    src.write_text("hello report v2", encoding="utf-8")
    run([str(src)], env)
    text = vault_copy.read_text(encoding="utf-8")
    check("auto sync pulled external",
          text.split("\n", 1)[1] == "hello report v2")
    check("header preserved after sync",
          text.split("\n", 1)[0].startswith("%% obsidian-intake source:"))
    check("external still headerless",
          src.read_text(encoding="utf-8") == "hello report v2")

    # 4) conflict (user edit in vault keeps the header line)
    header_first = f"%% obsidian-intake source: {src} %%"
    src.write_text("external v3", encoding="utf-8")
    vault_copy.write_text(header_first + "\nvault v3\n", encoding="utf-8")
    run([str(src)], env)
    st = json.loads(run(["status"], env).stdout)
    check("conflict detected", st["conflict"] == 1, json.dumps(st))

    # 5) resolve keep vault -> external must stay headerless
    r = run(["conflict", "1", "vault"], env)
    data = json.loads(r.stdout)
    check("conflict resolved keep vault", data.get("ok") is True)
    check("external overwritten headerless",
          src.read_text(encoding="utf-8") == "vault v3\n")
    check("vault copy still has header",
          vault_copy.read_text(encoding="utf-8")
          .split("\n", 1)[0] == header_first)

    # 6) keep external flow: pull keeps header, external stays headerless
    src.write_text("external v4", encoding="utf-8")
    run([str(src)], env)
    text = vault_copy.read_text(encoding="utf-8")
    check("keep external flow: vault matches external",
          text.split("\n", 1)[1] == "external v4", repr(text))
    check("header preserved after pull",
          text.split("\n", 1)[0].startswith("%% obsidian-intake source:"))

    # 7) migrate permanent
    r = run(["migrate", "1"], env)
    data = json.loads(r.stdout)
    check("migrated permanent", data.get("ok") is True
          and data["file"]["status"] == "permanent")
    check("external deleted", not src.exists())

    # 8) double migrate rejected
    r = run(["migrate", "1"], env)
    data = json.loads(r.stdout)
    check("double migrate rejected", data.get("ok") is False)

    # 9) check command
    r = run(["check", "Not-Indexed/report.md"], env)
    data = json.loads(r.stdout)
    check("check reports found", data.get("found") is True)

    # 10) custom extension .org routed and imported
    org = downloads / "note.org"
    org.write_text("org content", encoding="utf-8")
    run([str(org)], env)
    check("custom extension imported",
          (vault / "Not-Indexed/note.org").exists())

    # 11) header disabled via plugin settings
    write_plugin_data(vault, {
        "extensions": ["md"],
        "writeSourceHeader": False,
    })
    src2 = downloads / "plain.md"
    src2.write_text("plain content", encoding="utf-8")
    run([str(src2)], env)
    plain_copy = vault / "Not-Indexed/plain.md"
    check("no header when disabled",
          plain_copy.read_text(encoding="utf-8") == "plain content")

    # 12) move vault copy to another vault folder
    r = run(["move", "2", "projects/work"], env)
    data = json.loads(r.stdout)
    check("move to custom folder", data.get("ok") is True
          and data["file"]["vault_path"] == "projects/work/note.org")
    check("file physically moved",
          (vault / "projects/work/note.org").exists()
          and not (vault / "Not-Indexed/note.org").exists())

    # moved file still routable at new location via original path
    run([str(org)], env)
    check("external opens at moved location",
          (vault / "projects/work/note.org").exists())

    # 13) history-all contains the move event
    r = run(["history-all"], env)
    events = json.loads(r.stdout)
    check("global history has moved event",
          any(e["event"] == "moved" for e in events))

    # 14) delete external, keep only vault copy
    r = run(["delete-external", "3"], env)
    data = json.loads(r.stdout)
    check("delete-external ok", data.get("ok") is True
          and data["file"]["status"] == "permanent")
    check("external file removed", not src2.exists())
    check("vault copy remains",
          (vault / "Not-Indexed/plain.md").exists())

    st = json.loads(run(["status"], env).stdout)
    check("final counts", st["total"] == 3 and st["permanent"] == 2
          and st["managed"] == 1, json.dumps(st))

    # ---------- v1.4 features ----------

    # 15) always-linked flag (banner checkbox)
    r = run(["set-always-linked", "2", "0"], env)
    d = json.loads(r.stdout)
    check("always linked off", d.get("ok") is True
          and d["file"]["always_linked"] == 0)
    r = run(["set-always-linked", "2", "1"], env)
    d = json.loads(r.stdout)
    check("always linked on", d.get("ok") is True
          and d["file"]["always_linked"] == 1)

    # 16) Deleted-Notes countdown: mark-deleted + expiring_soon + cleanup
    write_plugin_data(vault, {
        "extensions": ["md"],
        "writeSourceHeader": False,
        "deletedRetentionValue": 0.00005,  # ~4.3 seconds
        "deletedRetentionUnit": "days",
    })
    r = run(["mark-deleted", "Not-Indexed/plain.md"], env)
    check("mark-deleted ok", json.loads(r.stdout).get("ok") is True)
    r = run(["list-deleted"], env)
    items = json.loads(r.stdout)
    check("list-deleted shows file",
          any(i["vault_path"] == "Not-Indexed/plain.md" for i in items))
    time.sleep(5)
    r = run(["list-deleted"], env)
    items = json.loads(r.stdout)
    it = next((i for i in items
               if i["vault_path"] == "Not-Indexed/plain.md"), None)
    check("expiring soon flagged (<1/5 lifetime)",
          it is not None and it.get("expiring_soon") is True,
          json.dumps(items))
    r = run(["cleanup-deleted"], env)
    d = json.loads(r.stdout)
    check("cleanup purged expired", d.get("removed") == 1)
    check("expired file removed from vault",
          not (vault / "Not-Indexed/plain.md").exists())

    # 17) move out of Deleted-Notes cancels the countdown
    run(["mark-deleted", "projects/work/note.org"], env)
    r = run(["move", "2", "projects"], env)
    check("move ok", json.loads(r.stdout).get("ok") is True)
    r = run(["check", "projects/note.org"], env)
    d = json.loads(r.stdout)
    check("move out clears countdown",
          d.get("entered_deleted_at") is None, json.dumps(d))

    # 18) purge immediately deletes the vault copy
    run(["mark-deleted", "projects/note.org"], env)
    r = run(["purge", "2"], env)
    check("purge ok", json.loads(r.stdout).get("ok") is True)
    check("purged file gone", not (vault / "projects/note.org").exists())

    # 19) restore: file moved out of the vault comes back to same path
    os.rename(vault / "Not-Indexed/report.md", downloads / "moved-out.md")
    src.write_text("vault v3\n", encoding="utf-8")
    r = run([str(src)], env)
    check("restored to previous vault path",
          (vault / "Not-Indexed/report.md").exists())
    r = run(["history", "1"], env)
    events = json.loads(r.stdout)
    check("restored event logged",
          any(e["event"] == "restored" for e in events))

    st = json.loads(run(["status"], env).stdout)
    check("v1.4 final total", st["total"] == 3, json.dumps(st))

    # ---------- v1.4.1 features ----------

    write_plugin_data(vault, {
        "extensions": ["md", "txt"],
        "customExtensions": ["py"],
        "writeSourceHeader": True,
        "deletedRetentionValue": 0.00001,  # ~0.86 seconds
        "deletedRetentionUnit": "days",
    })

    # 20) non-Markdown copies preserve their native text (no Markdown header)
    txt = downloads / "reading.txt"
    txt.write_text("main txt\n", encoding="utf-8")
    run([str(txt)], env)
    recs = json.loads(run(["list"], env).stdout)
    txt_rec = next(r for r in recs if r["original_path"] == str(txt))
    txt_copy = vault / txt_rec["vault_path"]
    check("txt imported without markdown header",
          txt_copy.read_text(encoding="utf-8") == "main txt\n")

    py = downloads / "sample.py"
    py.write_text("print('main')\n", encoding="utf-8")
    run([str(py)], env)
    recs = json.loads(run(["list"], env).stdout)
    py_rec = next(r for r in recs if r["original_path"] == str(py))
    py_copy = vault / py_rec["vault_path"]
    check("py imported without markdown header",
          py_copy.read_text(encoding="utf-8") == "print('main')\n")

    # 21) Deleted-Notes protection prevents timed cleanup, toggle resumes it.
    run(["move", str(txt_rec["id"]), "Deleted-Notes"], env)
    r = run(["set-delete-protected", str(txt_rec["id"]), "1"], env)
    check("delete protection enabled",
          json.loads(r.stdout).get("delete_protected") is True)
    time.sleep(1.2)
    r = run(["cleanup-deleted"], env)
    check("protected deleted copy survives cleanup",
          (vault / "Deleted-Notes/reading.txt").exists())
    run(["toggle-delete-protected", str(txt_rec["id"])], env)
    run(["cleanup-deleted"], env)
    check("unprotected expired copy is purged",
          not (vault / "Deleted-Notes/reading.txt").exists())

    # 22) Re-opening original restores Deleted -> Not-Indexed and creates
    # a recovery decision when the retained copy differs from main.
    run(["move", str(py_rec["id"]), "Deleted-Notes"], env)
    deleted_py = vault / "Deleted-Notes/sample.py"
    deleted_py.write_text("print('edited copy')\n", encoding="utf-8")
    run([str(py)], env)
    restored_py = vault / "Not-Indexed/sample.py"
    check("external click restores deleted copy to Not-Indexed",
          restored_py.exists() and not deleted_py.exists())
    d = json.loads(run(["check", "Not-Indexed/sample.py"], env).stdout)
    check("restored edited copy has recovery pending",
          d.get("recovery_pending") == 1, json.dumps(d))
    check("restored copy keeps temporary-read unchecked persistently",
          d.get("temporary_read_suppressed") == 1, json.dumps(d))
    diff = json.loads(run(["recovery-diff", str(py_rec["id"])], env).stdout)
    check("recovery diff exposes changed lines",
          any(line.startswith("+") and "edited copy" in line
              for line in diff.get("lines", [])), json.dumps(diff))

    r = run(["resolve-recovery", str(py_rec["id"]), "vault"], env)
    check("recover-and-save succeeds", json.loads(r.stdout).get("ok") is True)
    check("recovered py edits saved to main without foreign header",
          py.read_text(encoding="utf-8") == "print('edited copy')\n")

    # 23) Vault rename synchronization keeps the tracked path correct.
    os.rename(restored_py, vault / "Not-Indexed/sample-renamed.py")
    r = run(["vault-renamed", "Not-Indexed/sample.py",
             "Not-Indexed/sample-renamed.py"], env)
    d = json.loads(r.stdout)
    check("vault-renamed updates tracked path",
          d.get("ok") is True and
          d.get("file", {}).get("vault_path") == "Not-Indexed/sample-renamed.py")

    print("ALL PASSED" if ok else "SOME TESTS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

