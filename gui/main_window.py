"""Main application window and navigation for the FlySWATTER GUI."""

from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
)

from gui.app_state import AppState
from gui.screens.choose_analysis_screen import ChooseAnalysisScreen
from gui.screens.pulse_progress_screen import PulseProgressScreen
from gui.screens.pulse_results_screen import PulseResultsScreen
from gui.screens.pulse_upload_screen import PulseUploadScreen
from gui.screens.score_progress_screen import ScoreProgressScreen
from gui.screens.score_results_screen import ScoreResultsScreen
from gui.screens.score_upload_screen import ScoreUploadScreen
from gui.screens.sleep_definition_screen import SleepDefinitionScreen
from gui.screens.time_window_screen import TimeWindowScreen
from gui.screens.welcome_screen import WelcomeScreen
from gui.screens.well_mapping_screen import WellMappingScreen
from gui.workers import FunctionWorker
from services.default_well_mapping import get_default_genotype_order, get_default_mapping, validate_default_mapping
from services.pulse_service import estimate_window_file_count, get_folder_window_summary, run_pulse_analysis
from services.researcher_store import load_researcher_names, save_researcher_name
from services.run_paths import build_run_output_dir
from services.score_service import run_score_analysis
from services.validators import (
    validate_accel_folder,
    validate_monitor_file,
    validate_researcher_name,
    validate_score_file,
)


class FlySwatterMainWindow(QMainWindow):
    def __init__(self, project_root: Path, data_root: Path, resource_root: Path, parent=None) -> None:
        super().__init__(parent)
        self.project_root = Path(project_root)
        self.data_root = Path(data_root)
        self.resource_root = Path(resource_root)
        self.state = AppState(
            project_root=self.project_root,
            resource_root=self.resource_root,
            data_root=self.data_root,
        )
        self.thread_pool = QThreadPool.globalInstance()
        validate_default_mapping()

        self.setWindowTitle("FlySWATTER")
        self.resize(1360, 920)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.welcome_screen = WelcomeScreen()
        self.choose_screen = ChooseAnalysisScreen()
        self.score_upload_screen = ScoreUploadScreen()
        self.mapping_screen = WellMappingScreen()
        self.sleep_definition_screen = SleepDefinitionScreen()
        self.score_progress_screen = ScoreProgressScreen()
        self.score_results_screen = ScoreResultsScreen()
        self.pulse_upload_screen = PulseUploadScreen()
        self.time_window_screen = TimeWindowScreen()
        self.pulse_progress_screen = PulseProgressScreen()
        self.pulse_results_screen = PulseResultsScreen()

        for screen in [
            self.welcome_screen,
            self.choose_screen,
            self.score_upload_screen,
            self.mapping_screen,
            self.sleep_definition_screen,
            self.score_progress_screen,
            self.score_results_screen,
            self.pulse_upload_screen,
            self.time_window_screen,
            self.pulse_progress_screen,
            self.pulse_results_screen,
        ]:
            self.stack.addWidget(screen)

        self._active_workers = set()
        self._pulse_folder_summary = None
        self._connect_signals()
        self._refresh_researcher_names()
        self.stack.setCurrentWidget(self.welcome_screen)

    def _track_worker(self, worker) -> None:
        """Hold a reference to a worker until it reports back.

        A worker only stays alive through this reference. If it is dropped
        while the job is still running, the QObject carrying its signals is
        collected and the next progress update raises "Signal source has been
        deleted", which takes the app down mid-analysis.
        """
        self._active_workers.add(worker)
        worker.signals.finished.connect(lambda *_: self._active_workers.discard(worker), Qt.QueuedConnection)
        worker.signals.error.connect(lambda *_: self._active_workers.discard(worker), Qt.QueuedConnection)

    def _analysis_in_progress(self) -> bool:
        return bool(self._active_workers)

    def _connect_signals(self) -> None:
        self.welcome_screen.continueRequested.connect(self._handle_researcher_continue)
        self.choose_screen.backRequested.connect(lambda: self.stack.setCurrentWidget(self.welcome_screen))
        self.choose_screen.scoreRequested.connect(self._show_score_upload)
        self.choose_screen.pulseRequested.connect(self._show_pulse_upload)
        self.score_upload_screen.backRequested.connect(lambda: self.stack.setCurrentWidget(self.choose_screen))
        self.score_upload_screen.submitRequested.connect(self._handle_score_file_selected)
        self.mapping_screen.backRequested.connect(lambda: self.stack.setCurrentWidget(self.score_upload_screen))
        self.mapping_screen.submitRequested.connect(self._handle_mapping_confirmed)
        self.sleep_definition_screen.backRequested.connect(lambda: self.stack.setCurrentWidget(self.mapping_screen))
        self.sleep_definition_screen.confirmRequested.connect(self._run_score_analysis)
        self.score_results_screen.downloadRequested.connect(self._handle_score_download)
        self.score_results_screen.computePulseRequested.connect(self._show_pulse_upload)
        self.score_results_screen.restartRequested.connect(self._restart_to_choose_analysis)
        self.pulse_upload_screen.backRequested.connect(lambda: self.stack.setCurrentWidget(self.choose_screen))
        self.pulse_upload_screen.submitRequested.connect(self._handle_pulse_folder_selected)
        self.time_window_screen.backRequested.connect(lambda: self.stack.setCurrentWidget(self.pulse_upload_screen))
        self.time_window_screen.continueRequested.connect(self._run_pulse_analysis)
        self.pulse_results_screen.downloadRequested.connect(self._handle_pulse_download)
        self.pulse_results_screen.restartRequested.connect(self._restart_to_choose_analysis)

    def _refresh_researcher_names(self) -> None:
        self.welcome_screen.set_researcher_names(load_researcher_names(self.data_root), self.state.researcher_name)

    def _handle_researcher_continue(self, researcher_name: str) -> None:
        validation = validate_researcher_name(researcher_name)
        if not validation.valid:
            self._show_error(
                validation.message,
                validation.details,
                help_document=validation.help_document,
            )
            return
        self.state.researcher_name = researcher_name.strip()
        save_researcher_name(self.data_root, self.state.researcher_name)
        self._refresh_researcher_names()
        self.stack.setCurrentWidget(self.choose_screen)

    def _show_score_upload(self) -> None:
        self.state.reset_score_flow()
        self.score_upload_screen.set_selected_path(str(self.state.selected_score_file or ""))
        self.score_upload_screen.set_selected_monitor_path(str(self.state.selected_monitor_file or ""))
        self.stack.setCurrentWidget(self.score_upload_screen)

    def _show_pulse_upload(self) -> None:
        self.state.reset_pulse_flow()
        self._pulse_folder_summary = None
        self.pulse_upload_screen.set_selected_path(str(self.state.selected_pulse_folder or ""))
        self.stack.setCurrentWidget(self.pulse_upload_screen)

    def _handle_score_file_selected(self, file_path: str, monitor_path: str = "") -> None:
        try:
            validation = validate_score_file(file_path)
        except OSError as exc:
            self._show_error(
                "Could not read the selected Zantiks file.",
                [
                    str(exc),
                    "If the file is on a network drive, confirm it is still mounted.",
                    "If macOS is blocking access, grant FlySWATTER permission under "
                    "System Settings > Privacy & Security > Files and Folders.",
                ],
            )
            return
        if not validation.valid:
            self._show_error(
                validation.message,
                validation.details,
                help_document=validation.help_document,
            )
            return

        monitor_path = (monitor_path or "").strip()
        if monitor_path:
            try:
                monitor_validation = validate_monitor_file(monitor_path)
            except OSError as exc:
                self._show_error(
                    "Could not read the selected monitor temperature log.",
                    [
                        str(exc),
                        "If the file is on a network drive, confirm it is still mounted.",
                    ],
                )
                return
            if not monitor_validation.valid:
                self._show_error(monitor_validation.message, monitor_validation.details)
                return
            self.state.selected_monitor_file = Path(monitor_path)
        else:
            self.state.selected_monitor_file = None

        self.state.selected_score_file = Path(file_path)
        self.state.genotype_order = get_default_genotype_order()
        self.state.genotype_mapping = get_default_mapping()
        self.mapping_screen.set_mapping(self.state.genotype_mapping, self.state.genotype_order)
        self.stack.setCurrentWidget(self.mapping_screen)

    def _handle_mapping_confirmed(self, mapping: dict, genotype_order: list) -> None:
        self.state.genotype_mapping = {str(name): list(wells) for name, wells in mapping.items()}
        self.state.genotype_order = list(genotype_order)
        self.sleep_definition_screen.set_minutes(self.state.score_sleep_minutes)
        self.stack.setCurrentWidget(self.sleep_definition_screen)

    def _run_score_analysis(self, sleep_minutes: int) -> None:
        if self.state.selected_score_file is None:
            self._show_error("Select a Zantiks behavior data file first.")
            return
        if not self.state.genotype_mapping or not self.state.genotype_order:
            self._show_error("Confirm genotype mapping before starting score analysis.")
            self.stack.setCurrentWidget(self.mapping_screen)
            return
        self.state.score_sleep_minutes = max(1, int(sleep_minutes))
        sleep_threshold_sec = self.state.score_sleep_minutes * 60
        output_dir = build_run_output_dir(self.data_root, self.state.researcher_name)
        worker = FunctionWorker(
            run_score_analysis,
            self.state.selected_score_file,
            self.state.genotype_mapping,
            self.state.genotype_order,
            output_dir,
            pre_sec=sleep_threshold_sec,
            sleep_threshold_sec=sleep_threshold_sec,
            monitor_file=self.state.selected_monitor_file,
        )
        worker.setAutoDelete(False)
        worker.signals.finished.connect(self._handle_score_finished, Qt.QueuedConnection)
        worker.signals.error.connect(self._handle_worker_error, Qt.QueuedConnection)
        self._track_worker(worker)
        self.score_progress_screen.start_indeterminate(
            f"Scoring sleep & arousal using {self.state.score_sleep_minutes} minute inactivity threshold\u2026\n"
            "Processing typically takes ~2-6 minutes to complete."
        )
        self.stack.setCurrentWidget(self.score_progress_screen)
        self.thread_pool.start(worker)

    def _handle_score_finished(self, result) -> None:
        self.score_progress_screen.finish("Score analysis complete.")
        self.state.score_result = result
        self.score_results_screen.set_result(result)
        self.stack.setCurrentWidget(self.score_results_screen)

    def _handle_score_download(self, key: str) -> None:
        if self.state.score_result is None:
            return
        if key == "sleep_zip":
            self._copy_artifact(self.state.score_result.sleep_zip)
        elif key == "arousal_zip":
            self._copy_artifact(self.state.score_result.arousal_zip)
        else:
            self._copy_artifact(Path(key))

    def _handle_pulse_folder_selected(self, folder_path: str) -> None:
        try:
            validation = validate_accel_folder(folder_path)
        except OSError as exc:
            # Network shares and privacy-protected locations fail here, and an
            # error escaping this slot would take the whole app down.
            self._show_error(
                "Could not read the selected accelerometer log folder.",
                [
                    str(exc),
                    "If the data is on a network drive, confirm it is still mounted.",
                    "If macOS is blocking access, grant FlySWATTER permission under "
                    "System Settings > Privacy & Security > Files and Folders.",
                ],
            )
            return
        if not validation.valid:
            self._show_error(
                validation.message,
                validation.details,
                help_document=validation.help_document,
            )
            return
        self.state.selected_pulse_folder = Path(folder_path)
        worker = FunctionWorker(get_folder_window_summary, folder_path)
        worker.setAutoDelete(False)
        worker.signals.finished.connect(self._handle_folder_summary_ready, Qt.QueuedConnection)
        worker.signals.error.connect(self._handle_folder_scan_error, Qt.QueuedConnection)
        self._track_worker(worker)
        self.pulse_progress_screen.start_indeterminate("Scanning folder for time window bounds\u2026")
        self.stack.setCurrentWidget(self.pulse_progress_screen)
        self.thread_pool.start(worker)

    def _handle_folder_summary_ready(self, summary) -> None:
        self._pulse_folder_summary = summary
        try:
            self.time_window_screen.set_summary(summary)
        except Exception as exc:
            self.pulse_progress_screen.finish()
            self._show_error("Could not prepare the time window screen.", [str(exc)])
            self.stack.setCurrentWidget(self.pulse_upload_screen)
            return
        self.stack.setCurrentWidget(self.time_window_screen)

    def _handle_folder_scan_error(self, message: str) -> None:
        self.pulse_progress_screen.finish()
        self._show_error("Failed to scan folder.", [message])
        self.stack.setCurrentWidget(self.pulse_upload_screen)

    def _run_pulse_analysis(self, start_iso: str, end_iso: str, pulse_windows=None) -> None:
        if self.state.selected_pulse_folder is None:
            self._show_error("Select an accelerometer log folder first.")
            return
        if not pulse_windows:
            self._show_error("Add at least one expected pulse window before starting analysis.")
            return
        if self._analysis_in_progress():
            self._show_error("An analysis is already running. Wait for it to finish before starting another.")
            return
        output_dir = build_run_output_dir(self.data_root, self.state.researcher_name)
        worker = FunctionWorker(
            run_pulse_analysis,
            self.state.selected_pulse_folder,
            output_dir,
            window_start_iso=start_iso,
            window_end_iso=end_iso,
            pulse_windows=pulse_windows,
        )
        worker.setAutoDelete(False)
        worker.signals.finished.connect(self._handle_pulse_finished, Qt.QueuedConnection)
        worker.signals.error.connect(self._handle_worker_error, Qt.QueuedConnection)
        self._track_worker(worker)
        try:
            n_files = estimate_window_file_count(
                self.state.selected_pulse_folder,
                window_start_iso=start_iso,
                window_end_iso=end_iso,
            )
        except Exception:
            n_files = len(self._pulse_folder_summary.csv_files) if self._pulse_folder_summary else 1
        n_files = max(n_files, 1)
        # Roughly a second per log file plus aggregation and rendering; the
        # upper bound allows for slow network shares.
        est_min = 10 + n_files * 2
        est_max = 30 + n_files * 8
        self.pulse_progress_screen.start_timed(
            est_min,
            "Starting pulse metrics analysis\u2026",
            estimated_seconds_max=est_max,
        )
        self.stack.setCurrentWidget(self.pulse_progress_screen)
        self.thread_pool.start(worker)

    def _handle_pulse_finished(self, result) -> None:
        self.pulse_progress_screen.finish("Pulse metrics analysis complete.")
        self.state.pulse_result = result
        self.pulse_results_screen.set_result(result)
        self.stack.setCurrentWidget(self.pulse_results_screen)

    def _handle_pulse_download(self, key: str) -> None:
        if self.state.pulse_result is None:
            return
        if key == "pulse_zip":
            self._copy_artifact(self.state.pulse_result.zip_path)
        else:
            self._copy_artifact(Path(key))

    def _restart_to_choose_analysis(self) -> None:
        self.state.reset_score_flow()
        self.state.reset_pulse_flow()
        self._pulse_folder_summary = None
        self.stack.setCurrentWidget(self.choose_screen)

    def _copy_artifact(self, path: Path) -> None:
        if not path.exists():
            self._show_error(f"File not found: {path}")
            return
        destination, _ = QFileDialog.getSaveFileName(self, "Save Copy", str(path.name))
        if destination:
            shutil.copy2(path, destination)
            QMessageBox.information(self, "Saved", f"Saved a copy to:\n{destination}")

    def _handle_worker_error(self, message: str) -> None:
        self.score_progress_screen.finish()
        self.pulse_progress_screen.finish()
        self._show_error("Analysis failed.", [message])
        self.stack.setCurrentWidget(self.choose_screen)

    def _show_error(self, message: str, details=None, help_document: str | None = None) -> None:
        details = details or []
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Critical)
        dialog.setWindowTitle("FlySWATTER")
        dialog.setText(message)
        if details:
            dialog.setInformativeText("\n".join(details))

        help_path = self._resolve_help_document(help_document)
        open_guide_button = None
        if help_path is not None:
            open_guide_button = dialog.addButton("Open filename guide", QMessageBox.ActionRole)
            dialog.addButton(QMessageBox.Ok)

        dialog.exec()
        if open_guide_button is not None and dialog.clickedButton() == open_guide_button:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(help_path)))

    def _resolve_help_document(self, help_document: str | None) -> Path | None:
        if not help_document:
            return None
        candidate = self.resource_root / help_document
        if candidate.is_file():
            return candidate
        return None
