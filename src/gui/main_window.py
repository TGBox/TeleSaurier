"""Main window: compose a fax, preview the PDF, watch the journal."""

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QGuiApplication
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
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
from src.core.models import FaxError, FaxJob, JournalEntry, normalize_fax_number
from src.core.service import FaxService, count_pages
from src.core.text import local_time
from src.gui import theme
from src.transports import SIMULATED, route_label
from src.zammad import bridge
from src.zammad.client import ZammadClient

REFRESH_MS = 5000
_COLUMNS = ["Zeit", "Richtung", "Nummer", "Betreff", "Weg", "Benutzer", "Status", "Details"]


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
        self._route = QComboBox()
        for name in service.send_routes:
            self._route.addItem(route_label(name), name)
        send = QPushButton("Fax senden")
        send.setDefault(True)
        send.clicked.connect(self._send)

        form = QFormLayout()
        form.addRow("Empfänger:", self._recipient)
        form.addRow("Betreff:", self._subject)
        form.addRow("Dokument (PDF):", _row(self._document, browse))
        form.addRow("Ticket:", _row(self._ticket, load))
        form.addRow("Senden über:", self._route)

        left = QVBoxLayout()
        if SIMULATED in service.send_routes:
            banner = QLabel("Testmodus: Über den Weg „Testmodus“ wird nichts wirklich versendet.")
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
        report = QPushButton("Sendebericht öffnen")
        report.clicked.connect(self._open_report)
        left.addWidget(report, alignment=Qt.AlignmentFlag.AlignRight)
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

        self._build_appearance_menu()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(REFRESH_MS)
        self._reload_table()

    def _build_appearance_menu(self) -> None:
        menu = self.menuBar().addMenu("Ansicht").addMenu("Design")
        group = QActionGroup(self)
        current = theme.saved_appearance()
        for appearance, label in theme.LABELS.items():
            action = QAction(label, self, checkable=True)
            action.setChecked(appearance == current)
            action.triggered.connect(lambda _checked, a=appearance: theme.apply_appearance(a))
            group.addAction(action)
            menu.addAction(action)

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
        document = Path(self._document.text())
        try:
            number = normalize_fax_number(self._recipient.text())
            pages = count_pages(document)
        except FaxError as exc:
            self._warn(str(exc))
            return
        if not self._confirm(number, document, pages):
            return
        job = FaxJob(
            number,
            document,
            self._subject.text().strip(),
            self._ticket_id,
            route=self._route.currentData(),
        )
        try:
            self._service.send(job)
        except FaxError as exc:
            self._warn(str(exc))
            return
        self._reload_table()
        self.statusBar().showMessage("Fax wurde übergeben.", 5000)

    def _confirm(self, number: str, document: Path, pages: int) -> bool:
        """Last check before sending: a wrong number cannot be taken back."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Fax senden?")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(f"<p>Fax senden an</p><p style='font-size:22pt'><b>{number}</b></p>")
        box.setInformativeText(
            f"Dokument: {document.name}\nSeiten: {pages}\nSenden über: {self._route.currentText()}"
        )
        send = box.addButton("Senden", QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton("Abbrechen", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.exec()
        return box.clickedButton() is send

    # --- journal ---------------------------------------------------------

    def _refresh(self) -> None:
        changed = self._service.refresh()
        received = self._service.poll_inbound()
        if self._zammad is not None:
            for entry in changed:
                try:
                    bridge.report_result(self._service, self._zammad, entry)
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
                local_time(entry.created_at),
                entry.direction.label,
                entry.number,
                entry.subject,
                route_label(entry.route),
                entry.user,
                entry.status.label,
                entry.detail,
            ]
            for column, value in enumerate(values):
                self._table.setItem(row, column, QTableWidgetItem(value))

    def _open_entry(self, row: int, _column: int) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._entries[row].document)))

    def _open_report(self) -> None:
        row = self._table.currentRow()
        if row < 0:
            self._warn("Bitte zuerst einen Eintrag im Journal auswählen.")
            return
        entry = self._entries[row]
        try:
            content = self._service.report(entry)
        except FaxError as exc:
            self._warn(str(exc))
            return
        folder = self._settings.data_dir / "reports"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"sendebericht-{entry.id}.pdf"
        path.write_bytes(content)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

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
