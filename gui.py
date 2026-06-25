"""
PDF 彩色/黑白页面识别工具 - GUI 版（PySide6）
拖拽或选择 PDF → 调阈值/单价 → 识别 → 拆分 / 报告
"""
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QDoubleSpinBox, QSlider, QProgressBar,
    QTableWidget, QTableWidgetItem, QFileDialog, QHeaderView,
    QGroupBox, QFormLayout, QComboBox, QAbstractItemView,
)

import fitz
import color_detect as core


class DetectWorker(QThread):
    """后台线程：跑识别避免卡 UI"""
    progress = Signal(int, int, str)            # current, total, message
    finished_ok = Signal(list, list, list)      # results, color_pages, bw_pages
    error = Signal(str)

    def __init__(self, pdf_path: str, threshold: float, dpi: int):
        super().__init__()
        self.pdf_path = pdf_path
        self.threshold = threshold
        self.dpi = dpi

    def run(self):
        try:
            doc = fitz.open(self.pdf_path)
            total = len(doc)
            results, color_pages, bw_pages = [], [], []
            for i, page in enumerate(doc, 1):
                pix = page.get_pixmap(dpi=self.dpi)
                _, _, ratio = core.analyze_page(pix)
                ratio_pct = ratio * 100
                is_color = ratio_pct >= self.threshold
                (color_pages if is_color else bw_pages).append(i)
                results.append((i, ratio_pct, is_color))
                self.progress.emit(i, total, f"P{i}/{total}")
            doc.close()
            self.finished_ok.emit(results, color_pages, bw_pages)
        except Exception as e:
            self.error.emit(f"识别失败：{e}")


def _hbox(*widgets, stretch_last=True):
    """把多个控件横向排列"""
    box = QHBoxLayout()
    for w in widgets:
        box.addWidget(w)
    if stretch_last:
        box.addStretch()
    return box


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.pdf_path: str | None = None
        self.worker: DetectWorker | None = None
        self.results: list = []
        self.color_pages: list[int] = []
        self.bw_pages: list[int] = []
        self.results_stale = False  # 参数变了需要重新识别

        self.setWindowTitle("PDF 彩色/黑白页面识别工具")
        self.setMinimumSize(760, 680)
        self.setAcceptDrops(True)
        self._build_ui()
        self._refresh_state()

    # ────────────── UI 构建 ──────────────
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        # 文件区
        file_box = QGroupBox("PDF 文件")
        fb = QHBoxLayout(file_box)
        self.btn_open = QPushButton("📂  选择 PDF")
        self.btn_open.clicked.connect(self.on_open)
        self.lbl_file = QLabel("未选择（也可将 PDF 拖到窗口）")
        self.lbl_file.setStyleSheet("color: #666;")
        fb.addWidget(self.btn_open)
        fb.addWidget(self.lbl_file, 1)
        root.addWidget(file_box)

        # 参数区
        param_box = QGroupBox("识别参数")
        pf = QFormLayout(param_box)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)  # 0.0-10.0%（×10）
        self.slider.setValue(20)
        self.slider.valueChanged.connect(self._on_threshold_change)
        self.lbl_threshold = QLabel("2.0 %")
        pf.addRow("彩色判定阈值:", _hbox(self.slider, self.lbl_threshold, stretch_last=False))

        self.cmb_dpi = QComboBox()
        self.cmb_dpi.addItems(["72", "100", "150", "200"])
        self.cmb_dpi.setCurrentText("100")
        self.cmb_dpi.currentTextChanged.connect(self._mark_stale)
        pf.addRow("渲染 DPI（高=准=慢）:", self.cmb_dpi)

        self.spn_color = QDoubleSpinBox()
        self.spn_color.setRange(0, 999)
        self.spn_color.setDecimals(2)
        self.spn_color.setValue(core.COLOR_PRICE)
        self.spn_color.valueChanged.connect(self._on_price_change)
        self.spn_bw = QDoubleSpinBox()
        self.spn_bw.setRange(0, 999)
        self.spn_bw.setDecimals(2)
        self.spn_bw.setValue(core.BW_PRICE)
        self.spn_bw.valueChanged.connect(self._on_price_change)
        pf.addRow("打印单价（元/页）:", _hbox(
            QLabel("彩色"), self.spn_color,
            QLabel("   黑白"), self.spn_bw,
        ))
        root.addWidget(param_box)

        # 操作区
        action = QHBoxLayout()
        self.btn_run = QPushButton("▶  开始识别")
        self.btn_run.setStyleSheet("font-size: 14px; padding: 8px 28px; font-weight: bold;")
        self.btn_run.clicked.connect(self.on_run)
        self.btn_split = QPushButton("📦  拆分 PDF")
        self.btn_split.clicked.connect(self.on_split)
        self.btn_report = QPushButton("📋  生成 HTML 报告")
        self.btn_report.clicked.connect(self.on_report)
        action.addWidget(self.btn_run)
        action.addStretch()
        action.addWidget(self.btn_split)
        action.addWidget(self.btn_report)
        root.addLayout(action)

        # 进度条
        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress.setFormat("等待开始")
        root.addWidget(self.progress)

        # 汇总
        self.lbl_summary = QLabel("尚未识别")
        self.lbl_summary.setStyleSheet(
            "font-size: 13px; padding: 8px; background: #f5f5f7; border-radius: 4px;"
        )
        root.addWidget(self.lbl_summary)

        # 结果表
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["页码", "判定", "彩色像素占比", "可视化"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        root.addWidget(self.table, 1)

        self.statusBar().showMessage("就绪")

    # ────────────── 事件 ──────────────
    def _on_threshold_change(self, v: int):
        th = v / 10.0
        self.lbl_threshold.setText(f"{th:.1f} %")
        self._mark_stale()

    def _on_price_change(self):
        # 价格变化只影响汇总，不需要重识别
        self._update_summary()

    def _mark_stale(self):
        if self.results:
            self.results_stale = True
            self.lbl_summary.setText("⚠️  参数已变更，需重新识别")
            self.statusBar().showMessage("参数变更")

    # ── 拖拽 ──
    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls() and any(u.toLocalFile().lower().endswith(".pdf")
                                          for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent):
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if p.lower().endswith(".pdf"):
                self._set_pdf(p)
                break

    # ── 文件选择 ──
    def on_open(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 PDF", "", "PDF 文件 (*.pdf)")
        if path:
            self._set_pdf(path)

    def _set_pdf(self, path: str):
        try:
            doc = fitz.open(path)
            n = len(doc)
            doc.close()
        except Exception as e:
            self.statusBar().showMessage(f"打开失败：{e}")
            return
        self.pdf_path = path
        self.results = []
        self.color_pages = []
        self.bw_pages = []
        self.results_stale = False
        self.table.setRowCount(0)
        self.lbl_file.setText(f"{Path(path).name}  （{n} 页）")
        self.lbl_file.setStyleSheet("color: #1d1d1f; padding: 4px; font-weight: 500;")
        self.progress.setValue(0)
        self.progress.setFormat("等待开始")
        self.lbl_summary.setText("已加载，可调整参数后点击「开始识别」")
        self.statusBar().showMessage(f"已加载 {path}")
        self._refresh_state()

    # ── 识别 ──
    def on_run(self):
        if not self.pdf_path:
            self.statusBar().showMessage("请先选择 PDF")
            return
        if self.worker and self.worker.isRunning():
            return
        threshold = self.slider.value() / 10.0
        dpi = int(self.cmb_dpi.currentText())
        self.worker = DetectWorker(self.pdf_path, threshold, dpi)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_done)
        self.worker.error.connect(self._on_error)
        self.btn_run.setEnabled(False)
        self.btn_run.setText("识别中…")
        self.progress.setFormat("处理中 %p%")
        self.worker.start()

    def _on_progress(self, cur, total, msg):
        pct = int(cur / total * 100) if total else 0
        self.progress.setValue(pct)
        self.statusBar().showMessage(msg)

    def _on_done(self, results, color_pages, bw_pages):
        self.results = results
        self.color_pages = color_pages
        self.bw_pages = bw_pages
        self.results_stale = False
        self.btn_run.setEnabled(True)
        self.btn_run.setText("▶  开始识别")
        self.progress.setValue(100)
        self.progress.setFormat("完成 %p%")
        self._fill_table()
        self._update_summary()
        self.statusBar().showMessage(
            f"识别完成：彩色 {len(color_pages)} 页 · 黑白 {len(bw_pages)} 页"
        )
        self._refresh_state()

    def _on_error(self, msg: str):
        self.btn_run.setEnabled(True)
        self.btn_run.setText("▶  开始识别")
        self.progress.setFormat("失败")
        self.statusBar().showMessage(msg)

    def _fill_table(self):
        self.table.setRowCount(len(self.results))
        threshold = self.slider.value() / 10.0
        for r, (page_num, ratio_pct, is_color) in enumerate(self.results):
            tag = "彩色" if is_color else "黑白"
            color = "#d32f2f" if is_color else "#666"
            edge = abs(ratio_pct - threshold) < 1.0
            edge_tag = "  ⚠️" if edge else ""

            p_item = QTableWidgetItem(f"P{page_num}")
            p_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(r, 0, p_item)

            t_item = QTableWidgetItem(tag + edge_tag)
            t_item.setTextAlignment(Qt.AlignCenter)
            from PySide6.QtGui import QColor, QBrush
            t_item.setForeground(QBrush(QColor(color)))
            self.table.setItem(r, 1, t_item)

            ratio_item = QTableWidgetItem(f"{ratio_pct:.2f}%")
            ratio_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(r, 2, ratio_item)

            bar = "█" * min(int(ratio_pct), 30) or "·"
            bar_item = QTableWidgetItem(bar)
            bar_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self.table.setItem(r, 3, bar_item)

    def _update_summary(self):
        if not self.results:
            return
        cp = self.spn_color.value()
        bp = self.spn_bw.value()
        total_pages = len(self.results)
        smart = len(self.color_pages) * cp + len(self.bw_pages) * bp
        full = total_pages * cp
        saved = full - smart
        self.lbl_summary.setText(
            f"彩色 {len(self.color_pages)} 页 × {cp:.2f} 元  +  "
            f"黑白 {len(self.bw_pages)} 页 × {bp:.2f} 元  =  "
            f"<b>{smart:.2f} 元</b>　"
            f"（vs 全彩 {full:.2f}，省 {saved:.2f} 元）"
        )

    # ── 拆分 ──
    def on_split(self):
        if not self._require_results():
            return
        # 临时覆盖 core 的单价（让 split 后的 console 输出对得上；split 本身不用单价）
        color_pages, bw_pages = self.color_pages, self.bw_pages
        try:
            outputs = core.split_pdf(self.pdf_path, color_pages, bw_pages)
            self.statusBar().showMessage(
                "已生成：" + "、".join(Path(o).name for o in outputs)
            )
        except Exception as e:
            self.statusBar().showMessage(f"拆分失败：{e}")

    # ── 报告 ──
    def on_report(self):
        if not self._require_results():
            return
        threshold = self.slider.value() / 10.0
        cp = self.spn_color.value()
        bp = self.spn_bw.value()
        # 临时覆盖单价
        old_cp, old_bp = core.COLOR_PRICE, core.BW_PRICE
        core.COLOR_PRICE, core.BW_PRICE = cp, bp
        try:
            out = core.generate_html_report(
                self.pdf_path, self.results,
                self.color_pages, self.bw_pages, threshold,
            )
            self.statusBar().showMessage(f"已生成 {Path(out).name}")
        except Exception as e:
            self.statusBar().showMessage(f"报告失败：{e}")
        finally:
            core.COLOR_PRICE, core.BW_PRICE = old_cp, old_bp

    def _require_results(self) -> bool:
        if not self.results:
            self.statusBar().showMessage("请先点击「开始识别」")
            return False
        if self.results_stale:
            self.statusBar().showMessage("参数已变更，请先重新识别")
            return False
        return True

    def _refresh_state(self):
        has_pdf = bool(self.pdf_path)
        has_results = bool(self.results) and not self.results_stale
        self.btn_run.setEnabled(has_pdf and not (self.worker and self.worker.isRunning()))
        self.btn_split.setEnabled(has_results)
        self.btn_report.setEnabled(has_results)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
