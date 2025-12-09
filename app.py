#!/usr/bin/env python3
import sys
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6 import QtWidgets, QtCore
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QLineEdit, QFileDialog, QProgressBar, QTextEdit, QMessageBox
)

# Tema oscuro
import qdarkstyle

# Importa el conversor CLI
try:
    # Importar desde el mismo directorio
    import midi_to_sidecar as conv
except Exception as e:
    raise RuntimeError(f"No se pudo importar midi_to_sidecar.py: {e}")


# --------- Logging a QTextEdit ---------
class QtLogHandler(logging.Handler):
    sig = QtCore.Signal(str)

    def __init__(self):
        super().__init__()
        self.sig.connect(self._append)

    def emit(self, record):
        msg = self.format(record)
        self.sig.emit(msg)

    @QtCore.Slot(str)
    def _append(self, msg):
        # Para conectar luego desde MainWindow
        pass


class Worker(QtCore.QObject):
    progress = QtCore.Signal(int)           # 0..100
    stage = QtCore.Signal(str)              # texto de etapa
    finished = QtCore.Signal(str)           # ruta YAML
    error = QtCore.Signal(str)              # mensaje de error
    log = QtCore.Signal(str)                # log stream

    def __init__(self, mid_path: Path):
        super().__init__()
        self.mid_path = Path(mid_path)

    def _emit_log(self, level, msg):
        self.log.emit(f"[{level}] {msg}")

    @QtCore.Slot()
    def run(self):
        try:
            self.stage.emit("Cargando MIDI...")
            self.progress.emit(3)
            mid = conv.MidiFile(self.mid_path)
            ppq = mid.ticks_per_beat
            self._emit_log("INFO", f"Cargado {self.mid_path.name} (PPQ={ppq})")

            num, den = conv.read_first_timesig(mid)
            self._emit_log("INFO", f"Compás detectado: {num}/{den}")
            if (num, den) != (4, 4):
                self._emit_log("WARN", f"Time Signature {num}/{den} (se esperaba 4/4). Se continúa.")

            bar_ticks = int(ppq * 4 * (num/den))
            self.stage.emit("Leyendo notas...")
            self.progress.emit(10)
            notes = conv.collect_note_intervals(mid)
            if not notes:
                raise ValueError("No se encontraron notas (¿MIDI vacío o sólo percusión?).")

            end_max = max(e for _,e,_,_,_ in notes)
            min_needed = bar_ticks * 8
            if end_max < min_needed:
                self._emit_log("WARN", f"El MIDI parece menor a 8 compases ({end_max} < {min_needed}). Se analizarán 8 compases igualmente.")

            # Global hist + key/mode
            self.stage.emit("Detectando tonalidad...")
            self.progress.emit(25)
            global_hist = conv.weighted_pc_hist(notes, 0, min_needed)
            key_name, mode = conv.choose_key_mode(global_hist)
            key_pc = conv.NAME_TO_PC[key_name]
            self._emit_log("INFO", f"Estimado: key={key_name} mode={mode}")

            # Barras
            bars = []
            for bar_idx in range(8):
                self.stage.emit(f"Analizando compás {bar_idx+1}/8")
                s = bar_ticks * bar_idx
                e = s + bar_ticks
                midpt = s + bar_ticks//2
                r1, c1 = conv.best_roman_for_segment(notes, key_pc, mode, s, midpt)
                r2, c2 = conv.best_roman_for_segment(notes, key_pc, mode, midpt, e)

                if r1 == r2:
                    bars.append(r1)
                    self._emit_log("INFO", f"Bar {bar_idx+1}: {r1}")
                else:
                    bars.append(f"{r1}|{r2}")
                    self._emit_log("INFO", f"Bar {bar_idx+1}: {r1}|{r2}")

                # Progreso: 25 -> 90 lineal a 8 compases
                self.progress.emit(25 + int((bar_idx+1) * (65/8)))

            data = {'key': key_name, 'mode': mode, 'bars': bars}

            # Guardar YAML junto al .mid
            import yaml
            yml_path = self.mid_path.with_suffix('.yml')
            with open(yml_path, 'w', encoding='utf-8') as f:
                yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)

            self.stage.emit("¡Listo!")
            self.progress.emit(100)
            self._emit_log("INFO", f"Generado: {yml_path}")
            self.finished.emit(str(yml_path))
        except Exception as ex:
            self.error.emit(str(ex))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MIDI → sidecar YAML (8 compases)")
        self.resize(800, 520)

        central = QWidget(self)
        self.setCentralWidget(central)
        v = QVBoxLayout(central)

        # Selector de archivo
        hsel = QHBoxLayout()
        self.le_path = QLineEdit()
        self.le_path.setPlaceholderText("Selecciona un archivo .mid...")
        btn_browse = QPushButton("Elegir MIDI")
        btn_browse.clicked.connect(self.on_browse)
        hsel.addWidget(QLabel("Archivo MIDI:"))
        hsel.addWidget(self.le_path, 1)
        hsel.addWidget(btn_browse)
        v.addLayout(hsel)

        # Controles acción
        hact = QHBoxLayout()
        self.btn_run = QPushButton("Convertir a YAML")
        self.btn_run.clicked.connect(self.on_run)
        self.btn_run.setEnabled(False)
        hact.addStretch(1)
        hact.addWidget(self.btn_run)
        v.addLayout(hact)

        # Progreso + etapa
        self.lbl_stage = QLabel("Listo.")
        self.pbar = QProgressBar()
        self.pbar.setRange(0, 100)
        self.pbar.setValue(0)
        v.addWidget(self.lbl_stage)
        v.addWidget(self.pbar)

        # Logs
        self.txt_logs = QTextEdit()
        self.txt_logs.setReadOnly(True)
        v.addWidget(QLabel("Logs:"))
        v.addWidget(self.txt_logs, 1)

        # Wire logging to GUI + archivo
        self._setup_logging()

        # Estilo oscuro
        try:
            # QDarkStyle v3
            self.setStyleSheet(qdarkstyle.load_stylesheet(qt_api='pyside6'))
        except Exception:
            # Fallback genérico
            self.setStyleSheet(qdarkstyle.load_stylesheet())

        # Eventos
        self.le_path.textChanged.connect(lambda t: self.btn_run.setEnabled(bool(t.strip())))

        # Thread placeholders
        self._thread = None
        self._worker = None

    def _setup_logging(self):
        self.logger = logging.getLogger("gui")
        self.logger.setLevel(logging.INFO)

        # Handler a QTextEdit
        class TextEditHandler(logging.Handler):
            def __init__(self, widget):
                super().__init__()
                self.widget = widget
            def emit(self, record):
                msg = self.format(record)
                QtCore.QMetaObject.invokeMethod(
                    self.widget,
                    "append",
                    QtCore.Qt.QueuedConnection,
                    QtCore.Q_ARG(str, msg)
                )

        h_gui = TextEditHandler(self.txt_logs)
        h_gui.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        self.logger.addHandler(h_gui)

        # Handler a archivo rotativo en home
        log_path = Path.home() / ".midi2yaml_gui.log"
        h_file = RotatingFileHandler(log_path, maxBytes=512*1024, backupCount=3, encoding='utf-8')
        h_file.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        self.logger.addHandler(h_file)

    def on_browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Selecciona MIDI", "", "MIDI (*.mid *.midi)")
        if path:
            self.le_path.setText(path)

    def on_run(self):
        path = self.le_path.text().strip()
        if not path:
            return
        p = Path(path)
        if not p.exists():
            QMessageBox.critical(self, "Error", f"No existe: {p}")
            return

        self.txt_logs.clear()
        self.lbl_stage.setText("Iniciando...")
        self.pbar.setValue(0)
        self.btn_run.setEnabled(False)

        # Crear worker en QThread
        self._thread = QtCore.QThread(self)
        self._worker = Worker(p)
        self._worker.moveToThread(self._thread)

        # Conexiones
        self._thread.started.connect(self._worker.run)
        self._worker.stage.connect(self.lbl_stage.setText)
        self._worker.progress.connect(self.pbar.setValue)
        self._worker.log.connect(lambda m: self.logger.info(m))
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)

        # Cleanup
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._worker.error.connect(self._thread.quit)
        self._worker.error.connect(self._worker.deleteLater)

        self._thread.start()

    @QtCore.Slot(str)
    def _on_finished(self, yml_path):
        self.logger.info(f"YAML generado: {yml_path}")
        self.lbl_stage.setText("Completado.")
        self.btn_run.setEnabled(True)
        QMessageBox.information(self, "OK", f"Generado:\n{yml_path}")

    @QtCore.Slot(str)
    def _on_error(self, msg):
        self.logger.error(msg)
        self.lbl_stage.setText("Error.")
        self.btn_run.setEnabled(True)
        QMessageBox.critical(self, "Error", msg)


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
    

if __name__ == "__main__":
    main()
