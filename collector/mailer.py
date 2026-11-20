"""The morning email, sent with Gmail's SMTP server. Send only: nothing here can read the mailbox.

The Gmail app password is kept in the macOS Keychain, never in a file:
    security add-generic-password -s radar-vie-gmail -a <adresse gmail> -w
(the command asks for the password; nothing is shown or saved elsewhere).
"""

import html
import smtplib
import subprocess
from email.message import EmailMessage

KEYCHAIN_SERVICE = "radar-vie-gmail"
MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


def gmail_password(account: str) -> str | None:
    found = subprocess.run(
        ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account, "-w"],
        capture_output=True, text=True,
    )
    return found.stdout.strip() or None


def day_label(d) -> str:
    return f"{d.day} {MONTHS[d.month - 1]}"


def euros(n) -> str:
    return f"{n:,}".replace(",", " ") + " €" if isinstance(n, int) else "?"


def build(offers: list[dict], bands: dict[int, str], app_url: str, today, problems: list[str]) -> tuple[str, str, str]:
    """Subject, plain text and HTML. `offers` are the new offers kept, already in app format."""
    n = len(offers)
    subject = f"VIE · {n} nouvelle{'s' if n > 1 else ''} offre{'s' if n > 1 else ''} · {day_label(today)}" if n else f"VIE · rien de nouveau · {day_label(today)}"
    intro = (f"{n} nouvelle{'s' if n > 1 else ''} offre{'s' if n > 1 else ''} ce matin. Valide ou passe-les dans ton radar : {app_url}"
             if n else f"Rien de nouveau ce matin. Ton radar : {app_url}")
    link = f'<a href="{html.escape(app_url)}">{html.escape(app_url)}</a>'
    text, rows = [intro, ""], ["<p>" + html.escape(intro).replace(html.escape(app_url), link) + "</p>"]
    ordered = sorted(offers, key=lambda o: (o.get("priorite") or 9, -(o.get("score") or 0)))
    current = None
    for i, o in enumerate(ordered):
        if o.get("priorite") != current:
            current = o.get("priorite")
            title = bands.get(current, "Autres pays")
            text += ["", title.upper()]
            rows.append(f"<h3 style=\"margin:20px 0 6px\">{html.escape(title)}</h3>")
        if i >= 15:  # beyond 15 offers, one line each
            line = f"{o['titre']} · {o['entreprise']} · {o['ville']} · {o['score']}/100"
            text.append(line)
            rows.append(f"<p style=\"margin:2px 0\">{html.escape(line)}</p>")
            continue
        facts = (f"{o['ville']}, {o['pays']} · {euros(o.get('indemnite'))} par mois · {o.get('duree_mois') or '?'} mois"
                 f" · début {o.get('debut') or '?'} · expire le {o.get('expire_le') or '?'}")
        skills = ", ".join(o.get("competences", []))
        text += ["", f"{o['titre']} · {o['entreprise']}", facts, f"Compétences : {skills}", o.get("resume", ""),
                 f"Score {o['score']}/100. {o.get('pourquoi', '')}", o["lien"]]
        rows.append(
            f"<p style=\"margin:12px 0 2px\"><b>{html.escape(o['titre'])}</b> · {html.escape(o['entreprise'])}</p>"
            f"<p style=\"margin:0;color:#555\">{html.escape(facts)}</p>"
            f"<p style=\"margin:4px 0\">Compétences : {html.escape(skills)}</p>"
            f"<p style=\"margin:4px 0\">{html.escape(o.get('resume', ''))}</p>"
            f"<p style=\"margin:4px 0\"><b>{o['score']}/100</b>. {html.escape(o.get('pourquoi', ''))}</p>"
            f"<p style=\"margin:4px 0\"><a href=\"{html.escape(o['lien'])}\">Voir l'offre</a></p>"
        )
    if problems:
        text += ["", "À signaler : " + " ".join(problems)]
        rows.append(f"<p style=\"color:#7d2412\">À signaler : {html.escape(' '.join(problems))}</p>")
    body_html = "<div style=\"font-family:-apple-system,Segoe UI,sans-serif;font-size:14px;line-height:1.45\">" + "".join(rows) + "</div>"
    return subject, "\n".join(text), body_html


def send(account: str, to: str, subject: str, text: str, body_html: str) -> bool:
    password = gmail_password(account)
    if not password:
        return False
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = f"Radar VIE <{account}>", to, subject
    message.set_content(text)
    message.add_alternative(body_html, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as smtp:
        smtp.login(account, password)
        smtp.send_message(message)
    return True
