import base64
import functools
import http.server
import os
import re
import socketserver
import threading

import flet as ft
import requests

GEMINI_MODEL = "gemini-2.0-flash"
SYSTEM_PROMPT = (
    "You are Jarvis, a coding assistant. When asked to build something, write the COMPLETE "
    "working code in fenced code blocks. Put the file name as a comment on the first line of "
    "each block, like '# file: main.py' or '<!-- file: index.html -->'. "
    "Reply in Hinglish and keep extra text short."
)
SITE_DIR = os.path.expanduser("~/jarvis_site")
SITE_PORT = 8080
BLOCK_RE = re.compile(r"```[\w+-]*\n(.*?)```", re.S)
FILE_RE = re.compile(r"\s*(?:#|//|<!--)\s*file:\s*([\w./-]+)")

site_server = None


def files_from(text):
    out = {}
    for block in BLOCK_RE.findall(text):
        m = FILE_RE.match(block)
        if m:
            out[m.group(1)] = block
    return out


def ask_gemini(key, contents):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    body = {"system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]}, "contents": contents}
    r = requests.post(url, params={"key": key}, json=body, timeout=180)
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def gh_headers(token):
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}


def push_file(token, repo, path, text):
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    body = {
        "message": f"Jarvis: update {path}",
        "content": base64.b64encode(text.encode("utf-8")).decode(),
    }
    old = requests.get(url, headers=gh_headers(token), timeout=30)
    if old.status_code == 200:
        body["sha"] = old.json()["sha"]
    r = requests.put(url, headers=gh_headers(token), json=body, timeout=30)
    r.raise_for_status()
    return r.json()["content"]["html_url"]


def trigger_apk(token, repo):
    url = f"https://api.github.com/repos/{repo}/actions/workflows/build.yml/dispatches"
    r = requests.post(url, headers=gh_headers(token), json={"ref": "main"}, timeout=30)
    r.raise_for_status()
    return f"https://github.com/{repo}/actions"


def serve_local(html):
    global site_server
    os.makedirs(SITE_DIR, exist_ok=True)
    with open(os.path.join(SITE_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    if site_server is None:
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=SITE_DIR)
        site_server = socketserver.TCPServer(("127.0.0.1", SITE_PORT), handler)
        threading.Thread(target=site_server.serve_forever, daemon=True).start()
    return f"http://localhost:{SITE_PORT}"


def need(ok, msg):
    if not ok:
        raise ValueError(msg)


def main(page: ft.Page):
    page.title = "Jarvis"
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = "#0F1115"
    page.padding = 0

    cfg = {"gemini": "", "github": "", "repo": ""}
    contents = []
    last = {"files": {}}

    chat = ft.ListView(expand=True, spacing=12, padding=16, auto_scroll=True)
    status = ft.Text("", size=12, color="#9AA0A6")
    box = ft.TextField(
        hint_text="Jarvis, [काम] बनाओ...",
        expand=True,
        dense=True,
        border_radius=24,
        filled=True,
        bgcolor="#1C1F26",
        border_color="transparent",
        on_submit=lambda e: send(),
    )

    def note(msg):
        page.snack_bar = ft.SnackBar(ft.Text(msg))
        page.snack_bar.open = True
        page.update()

    def bubble(text, is_user, copy=False):
        controls = [ft.Text(text, selectable=True, color=ft.Colors.WHITE)]
        if copy:
            controls.append(
                ft.Row(
                    [ft.IconButton(ft.Icons.COPY, icon_size=18, tooltip="Copy",
                                   on_click=lambda e: page.set_clipboard(text))],
                    alignment=ft.MainAxisAlignment.END,
                )
            )
        return ft.Row(
            [
                ft.Container(
                    content=ft.Column(controls, tight=True, spacing=0),
                    padding=12,
                    border_radius=16,
                    bgcolor="#2A6DF4" if is_user else "#1C1F26",
                    width=300,
                )
            ],
            alignment=ft.MainAxisAlignment.END if is_user else ft.MainAxisAlignment.START,
        )

    def add(text, is_user, copy=False):
        chat.controls.append(bubble(text, is_user, copy))
        page.update()

    k_field = ft.TextField(label="Gemini API key", password=True, can_reveal_password=True)
    t_field = ft.TextField(label="GitHub token", password=True, can_reveal_password=True)
    r_field = ft.TextField(label="GitHub repo (user/repo)")

    def close_settings(e=None):
        dlg.open = False
        page.update()

    def save_settings(e):
        cfg["gemini"] = k_field.value.strip()
        cfg["github"] = t_field.value.strip()
        cfg["repo"] = r_field.value.strip()
        close_settings()
        note("Settings save ho gayi")

    dlg = ft.AlertDialog(
        title=ft.Text("Settings"),
        content=ft.Column([k_field, t_field, r_field], tight=True),
        actions=[ft.TextButton("Save", on_click=save_settings)],
    )
    page.overlay.append(dlg)

    def open_settings(e):
        dlg.open = True
        page.update()

    def route(text):
        low = text.lower()
        try:
            if "apk" in low:
                need(cfg["github"] and cfg["repo"], "Settings mein GitHub token aur repo daalein")
                url = trigger_apk(cfg["github"], cfg["repo"])
                add(f"APK build shuru ho gaya. 5-10 minute baad yahan se download karein: {url}", False)

            elif "github" in low and any(w in low for w in ["daal", "bhej", "push", "upload"]):
                need(cfg["github"] and cfg["repo"], "Settings mein GitHub token aur repo daalein")
                need(last["files"], "Pehle koi code banwayein")
                links = [push_file(cfg["github"], cfg["repo"], name, code)
                         for name, code in last["files"].items()]
                add("GitHub par bhej diya:\n" + "\n".join(links), False)

            elif "localhost" in low or "local host" in low:
                html = next((c for c in last["files"].values()
                             if "<html" in c.lower() or "<!doctype" in c.lower()), None)
                need(html, "Pehle koi HTML app banwayein")
                add(f"Local site chal rahi hai: {serve_local(html)}", False)

            else:
                need(cfg["gemini"], "Settings mein Gemini API key daalein")
                contents.append({"role": "user", "parts": [{"text": text}]})
                answer = ask_gemini(cfg["gemini"], contents)
                contents.append({"role": "model", "parts": [{"text": answer}]})
                last["files"] = files_from(answer)
                add(answer, False, copy=True)
        except Exception as ex:
            add(f"Error: {ex}", False)
        finally:
            status.value = ""
            page.update()

    def send():
        text = box.value.strip()
        if not text:
            return
        box.value = ""
        add(text, True)
        status.value = "Jarvis soch raha hai..."
        page.update()
        threading.Thread(target=route, args=(text,), daemon=True).start()

    page.add(
        ft.SafeArea(
            ft.Column(
                [
                    ft.Container(
                        content=ft.Row(
                            [
                                ft.Text("Jarvis", size=20, weight=ft.FontWeight.W_600),
                                ft.IconButton(ft.Icons.SETTINGS, on_click=open_settings),
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        ),
                        padding=ft.Padding.only(left=16, right=8, top=8),
                    ),
                    chat,
                    ft.Container(content=status, padding=ft.Padding.only(left=16)),
                    ft.Container(
                        content=ft.Row(
                            [box, ft.IconButton(ft.Icons.SEND_ROUNDED, icon_color="#2A6DF4", on_click=lambda e: send())]
                        ),
                        padding=12,
                    ),
                ],
                expand=True,
                spacing=0,
            ),
            expand=True,
        )
    )
    add("Jarvis ready hai. पहले Settings (⚙) में Gemini key, GitHub token और repo डालें। "
        "फिर बोलें: \"Jarvis, एक नोट्स ऐप बनाओ\", \"GitHub पर डालो\", \"APK बनाओ\", या \"localhost पर चलाओ\"।", False)


if __name__ == "__main__":
    if "com.termux" in os.environ.get("PREFIX", ""):
        ft.run(main, view=ft.AppView.WEB_BROWSER, port=8550)
    else:
        ft.run(main)
