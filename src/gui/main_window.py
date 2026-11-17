"""Main window: compose a fax, preview the PDF, watch the journal."""

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.config import Settings
from src.core.models import FaxError, FaxJob, JournalEntry
from src.core.service import FaxService
from src.transports import SIMULATED
from src.zammad import bridge
from src.zammad.client import ZammadClient

REFRESH_MS = 5000
_COLUMNS = ["Zeit", "Richtung", "Nummer", "Betreff", "Status", "Details"]


class MainWindow(QMainWindow):
    def __init__(
        self, settings: Settings, service: FaxService, zammad: ZammadClient | None
    ) -> None:
        super().__init__()
        self._settings = settings
        self._service = service
        self._zammad = zammad
        self._ticket_id: int | None = None
        self._entries: list[JournalEntry] = []
        self._downloads = settings.data_dir / "downloads"

        self.setWindowTitle("TeleSaurier")
        self.resize(1100, 720)

        self._recipient = QLineEdit()
        self._recipient.setPlaceholderText("z. B. 030 1234567")
        self._subject = QLineEdit()
        self._document = QLineEdit()
        self._document.setReadOnly(True)
        browse = QPushButton("Durchsuchen …")
        browse.clicked.connect(self._choose_document)
        self._ticket = QLineEdit()
        self._ticket.setPlaceholderText("Ticketnummer (optional)")
        self._ticket.textEdited.connect(self._forget_ticket)
        load = QPushButton("Anhänge laden")
        load.clicked.connect(self._load_from_ticket)
        if zammad is None:
            self._ticket.setEnabled(False)
            self._ticket.setPlaceholderText("Zammad ist nicht konfiguriert")
            load.setEnabled(False)
        send = QPushButton("Fax senden")
        send.setDefault(True)
        send.clicked.connect(self._send)

        form = QFormLayout()
        form.addRow("Empfänger:", self._recipient)
        form.addRow("Betreff:", self._subject)
        form.addRow("Dokument (PDF):", _row(self._document, browse))
        form.addRow("Ticket:", _row(self._ticket, load))

        left = QVBoxLayout()
        if settings.transport == SIMULATED:
            banner = QLabel("Testmodus: Faxe werden nur simuliert und nicht wirklich versendet.")
            banner.setStyleSheet("background: #fff3cd; color: #664d03; padding: 6px;")
            banner.setWordWrap(True)
            left.addWidget(banner)
        left.addLayout(form)
        left.addWidget(send, alignment=Qt.AlignmentFlag.AlignRight)
        left.addWidget(QLabel("Journal (Doppelklick öffnet das Dokument):"))
        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.cellDoubleClicked.connect(self._open_entry)
        left.addWidget(self._table)
        left_widget = QWidget()
        left_widget.setLayout(left)

        self._pdf = QPdfDocument(self)
        self._preview = QPdfView()
        self._preview.setDocument(self._pdf)
        self._preview.setPageMode(QPdfView.PageMode.MultiPage)
        self._preview.setZoomMode(QPdfView.ZoomMode.FitToWidth)

        splitter = QSplitter()
        splitter.addWidget(left_widget)
        splitter.addWidget(self._preview)
        splitter.setSizes([620, 480])
        self.setCentralWidget(splitter)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(REFRESH_MS)
        self._reload_table()

    # --- composing -------------------------------------------------------

    def _set_document(self, path: Path) -> None:
        self._document.setText(str(path))
        self._pdf.load(str(path))

    def _choose_document(self) -> None:
        name, _ = QFileDialog.getOpenFileName(self, "PDF auswählen", "", "PDF-Dateien (*.pdf)")
        if name:
            self._set_document(Path(name))
            if not self._subject.text():
                self._subject.setText(Path(name).name)

    def _forget_ticket(self) -> None:
        self._ticket_id = None

    def _load_from_ticket(self) -> None:
        if self._zammad is None or not self._ticket.text().strip():
            return
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            ticket = self._zammad.find_ticket(self._ticket.text())
            attachments = self._zammad.pdf_attachments(ticket["id"])
        except FaxError as exc:
            self._warn(str(exc))
            return
        finally:
            QGuiApplication.restoreOverrideCursor()
        if not attachments:
            self._warn("Dieses Ticket enthält keine PDF-Anhänge.")
            return
        chosen = attachments[-1]
        if len(attachments) > 1:
            names = [f"{i + 1}: {a.filename}" for i, a in enumerate(attachments)]
            name, ok = QInputDialog.getItem(
                self, "Anhang auswählen", "PDF-Anhang:", names, len(names) - 1, False
            )
            if not ok:
                return
            chosen = attachments[names.index(name)]
        try:
            content = self._zammad.download(chosen)
        except FaxError as exc:
            self._warn(str(exc))
            return
        self._downloads.mkdir(parents=True, exist_ok=True)
        path = self._downloads / f"{chosen.attachment_id}.pdf"
        path.write_bytes(content)
        self._ticket_id = ticket["id"]
        self._set_document(path)
        self._subject.setText(chosen.filename)
        number = assigned_fax_number(ticket, self._settings)
        if number and not self._recipient.text():
            self._recipient.setText(number)

    def _send(self) -> None:
        if not self._document.text():
            self._warn("Bitte zuerst ein PDF-Dokument auswählen.")
            return
        job = FaxJob(
            self._recipient.text(),
            Path(self._document.text()),
            self._subject.text().strip(),
            self._ticket_id,
        )
        try:
            self._service.send(job)
        except FaxError as exc:
            self._warn(str(exc))
            return
        self._reload_table()
        self.statusBar().showMessage("Fax wurde übergeben.", 5000)

    # --- journal ---------------------------------------------------------

    def _refresh(self) -> None:
        changed = self._service.refresh()
        received = self._service.poll_inbound()
        if self._zammad is not None:
            for entry in changed:
                try:
                    bridge.report_result(self._zammad, entry)
                except FaxError as exc:
                    self.statusBar().showMessage(str(exc), 10000)
        if received:
            self.statusBar().showMessage(f"{len(received)} neues Fax empfangen.", 10000)
        if changed or received:
            self._reload_table()

    def _reload_table(self) -> None:
        self._entries = self._service.journal.entries()
        self._table.setRowCount(len(self._entries))
        for row, entry in enumerate(self._entries):
            values = [
                entry.created_at.astimezone().strftime("%d.%m.%Y %H:%M"),
                entry.direction.label,
                entry.number,
                entry.subject,
                entry.status.label,
                entry.detail,
            ]
            for column, value in enumerate(values):
                self._table.setItem(row, column, QTableWidgetItem(value))

    def _open_entry(self, row: int, _column: int) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._entries[row].document)))

    def _warn(self, message: str) -> None:
        QMessageBox.warning(self, "TeleSaurier", message)


def assigned_fax_number(ticket: dict, settings: Settings) -> str:
    if settings.zammad is None:
        return ""
    return str(ticket.get(settings.zammad.fax_number_field) or "")


def _row(*widgets: QWidget) -> QWidget:
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    for widget in widgets:
        layout.addWidget(widget)
    return container
