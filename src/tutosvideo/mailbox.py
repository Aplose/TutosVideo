"""Lecture IMAP (ou fichier) du code e-mail à 6 chiffres."""

from __future__ import annotations

import imaplib
import re
import time
from email import message_from_bytes
from email.header import decode_header
from email.message import Message

from tutosvideo.config import Settings

CODE_RE = re.compile(r"\b(\d{6})\b")


def _decode_mime(value: str | None) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    out = []
    for data, enc in parts:
        if isinstance(data, bytes):
            out.append(data.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(data)
    return "".join(out)


def _body_text(msg: Message) -> str:
    if msg.is_multipart():
        chunks = []
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype in ("text/plain", "text/html"):
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                chunks.append(payload.decode(charset, errors="replace"))
        return "\n".join(chunks)
    payload = msg.get_payload(decode=True) or b""
    charset = msg.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


def fetch_code_imap(settings: Settings, timeout_s: int = 180) -> str | None:
    if not settings.imap_host or not settings.imap_user or not settings.imap_password:
        return None
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            client = imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port)
            client.login(settings.imap_user, settings.imap_password)
            client.select(settings.imap_folder)
            typ, data = client.search(None, "UNSEEN")
            ids = data[0].split() if data and data[0] else []
            # also recent ALL if unseen empty
            if not ids:
                typ, data = client.search(None, "ALL")
                ids = (data[0].split() if data and data[0] else [])[-8:]
            for msg_id in reversed(ids):
                typ, msg_data = client.fetch(msg_id, "(RFC822)")
                if typ != "OK" or not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                msg = message_from_bytes(raw)
                subject = _decode_mime(msg.get("Subject"))
                body = _body_text(msg)
                blob = subject + "\n" + body
                # Prefer MGC / verification context
                if not any(
                    k in blob.lower()
                    for k in ("verif", "code", "gestion", "cloud", "dolibarr", "instance")
                ):
                    # still accept if only recent code emails
                    pass
                match = CODE_RE.search(blob)
                if match:
                    client.logout()
                    return match.group(1)
            client.logout()
        except Exception:
            pass
        time.sleep(5)
    return None


def wait_for_verify_code(settings: Settings, timeout_s: int = 180) -> str:
    code = fetch_code_imap(settings, timeout_s=timeout_s)
    if code:
        return code

    path = settings.verify_code_file
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        path.unlink()
    print(
        f"\n[mailbox] IMAP indisponible ou code non trouvé.\n"
        f"Écrivez le code à 6 chiffres dans : {path}\n"
        f"Puis attendez…\n"
    )
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if path.is_file():
            text = path.read_text(encoding="utf-8").strip()
            match = CODE_RE.search(text)
            if match:
                return match.group(1)
        time.sleep(2)
    raise SystemExit("Code de vérification introuvable (IMAP + fichier).")
