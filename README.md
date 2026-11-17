# TeleSaurier

Eine Python Anwendung um Faxe bequem am PC zu versenden und zu empfangen –
als Desktop-Programm und als Dienst für das Zammad-Ticketsystem.

> **Stand:** Der echte Fax-Weg (OpenScape) fehlt noch. Bis dahin läuft alles im
> Testmodus (`transport = "simulated"`): Es wird **kein Fax wirklich versendet**.

## Aufbau

```text
src/
├── core/         Faxauftrag, Journal (SQLite), Ablauflogik – ohne GUI und ohne Netzwerk
├── transports/   Fax-Wege: base.py (Schnittstelle), simulated.py (Testmodus)
├── zammad/       REST-Client und Abläufe Ticket <-> Fax
├── server/       Dienst: nimmt Zammad-Webhooks an, meldet Ergebnisse ins Ticket
├── gui/          Desktop-Oberfläche (PySide6)
├── config.py     Einstellungen aus config.toml
└── main.py       Start der Desktop-Oberfläche
```

## Quickstart

```bash
uv sync

uv run main      # Desktop-Oberfläche
uv run server    # Zammad-Dienst (braucht Konfiguration, siehe unten)
uv run test      # Tests
uv run lint
uv run build     # Exe mit PyInstaller
```

## Konfiguration

`config.example.toml` nach `~/.telesaurier/config.toml` kopieren und anpassen.
Ohne Datei startet die Oberfläche im Testmodus ohne Zammad. Token und
Webhook-Geheimnis gehören nicht ins Repository; sie lassen sich über
`TELESAURIER_ZAMMAD_TOKEN` und `TELESAURIER_WEBHOOK_SECRET` setzen.

## Testmodus ausprobieren

Alle Daten liegen unter `~/.telesaurier/`.

- Gesendete Faxe landen in `simulated/outbox/`.
- Eine Nummer, die auf `0000` endet, schlägt absichtlich fehl.
- Eine PDF in `simulated/inbox/` gilt als eingegangenes Fax; der Dateiname
  (ohne `.pdf`) wird als Absendernummer verwendet.

## Zammad

**Aus der Oberfläche:** Ticketnummer eintragen, „Anhänge laden“. Das Ergebnis des
Versands wird als interne Notiz ins Ticket geschrieben.

**Aus dem Ticket (Dienst):**

1. In Zammad ein Ticket-Attribut für die Faxnummer anlegen (Standardname `fax_number`).
2. Einen Webhook auf `http://<host>:8520/webhook/zammad` mit HMAC-SHA1-Token anlegen.
3. Einen Trigger anlegen, der diesen Webhook auslöst, z. B. wenn ein Artikel mit
   Anhang erstellt wird und ein bestimmter Tag gesetzt ist.

Der Dienst faxt die PDF-Anhänge des auslösenden Artikels an die Nummer im Ticket,
schreibt das Ergebnis als interne Notiz zurück und legt für eingehende Faxe ein
neues Ticket an. Notizen von TeleSaurier beginnen mit „TeleSaurier:“ und lösen
selbst nie ein Fax aus.

## Neuen Fax-Weg ergänzen

Eine Klasse mit den drei Methoden aus `src/transports/base.py` schreiben und in
`build_transport` (`src/transports/__init__.py`) eintragen.
