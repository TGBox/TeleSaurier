# TeleSaurier

Eine Python Anwendung um Faxe bequem am PC zu versenden und zu empfangen –
als Desktop-Programm und als Dienst für das Zammad-Ticketsystem.

> **Stand:** Die echten Fax-Wege (FRITZ!Box, Epson) fehlen noch. Bis dahin läuft alles im
> Testmodus (Weg `simulated`): Es wird **kein Fax wirklich versendet**.
> Was der MVP können soll und wie es weitergeht, steht in der
> [MVP-Spezifikation](https://claude.ai/artifact/9THc1Lpu1ki6na1EeJTxRm).

## Aufbau

```text
src/
├── core/         Faxauftrag, Journal (SQLite), Ablauflogik – ohne GUI und ohne Netzwerk
├── transports/   Fax-Wege zum Senden und Empfangen, einzeln konfigurierbar
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
- Die letzten Ziffern der Nummer bestimmen das Ergebnis: `…0000` schlägt endgültig
  fehl, `…0001` ist immer besetzt (wird wiederholt, bis die Versuche aufgebraucht
  sind), `…0002` ist beim ersten Versuch besetzt und geht beim zweiten durch.
- Eine PDF in `simulated/inbox/` gilt als eingegangenes Fax; der Dateiname
  (ohne `.pdf`) wird als Absendernummer verwendet.

## Funktionen

- **Rückfrage vor dem Senden:** Nummer, Dokument, Seitenzahl und Weg werden vor
  jedem Versand noch einmal angezeigt.
- **Automatische Wiederholung:** bei „besetzt“ oder „keine Antwort“ bis zu
  `max_attempts` Versuche im Abstand von `retry_pause_seconds`.
- **Sendebericht:** PDF zu jedem abgeschlossenen Ausgang („Sendebericht öffnen“);
  bei Ticketbezug hängt er an der Notiz im Ticket.
- **Journal:** alle Ein- und Ausgänge mit Weg, Benutzer und Status.

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

## Fax-Wege

Senden und Empfangen werden in der `config.toml` getrennt eingestellt:

```toml
[fax]
send = ["simulated"]      # der erste ist der Standard
receive = ["simulated"]
```

Sind mehrere Sendewege eingetragen, bietet die Oberfläche unter „Senden über“
alle zur Auswahl an. Faxe aus Zammad-Tickets nehmen den Standardweg.

**Neuen Weg ergänzen:** eine Klasse nach `FaxSender` oder `FaxReceiver`
(`src/transports/base.py`) schreiben und in `SENDERS` bzw. `RECEIVERS` in
`src/transports/__init__.py` eintragen. Die Einstellungen des Wegs stehen im
Abschnitt `[fax.<name>]`.
