#!/usr/bin/env python3
"""
Process Manager Ultra — однофайловый менеджер процессов для Linux (PyQt6).

Функции:
• Группировка по корневому предку, PSS-память (Anon+File+Shmem) + GTT,
  чёрный список сессионных процессов.
• Сортировка по CPU/MEM кликом по заголовку.
• Умное обновление без мерцания и с сохранением выделения.
• Kill (SIGTERM → SIGKILL) по кнопке и по клавише Delete.
• Диагностика: ProcessKill.py --why [часть_имени] — почему процесса нет в списке.

Запуск:  python3 ProcessKill.py
Зависимости:  pip install psutil PyQt6
"""
from __future__ import annotations

import html
import os
import re
import sys
import tempfile
import zlib
import psutil
from collections import defaultdict
from contextlib import contextmanager
from PyQt6.QtCore import (
    pyqtSignal,
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
)
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QPolygonF,
)
from PyQt6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QAbstractScrollArea,
    QAbstractSlider,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStyle,
    QStyledItemDelegate,
    QTabBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


# ═══ AURORA (дословно из aurora.py) ══════════════════════════════
# ── 1. ТОКЕНЫ ─────────────────────────────────────────────────────
C_TEXT    = "#e8eaf3"
C_DIM     = "#8a91a8"
C_FAINT   = "#5b6278"
C_ACCENT  = "#8b93ff"     # индиго — основной акцент
C_ACCENT2 = "#2dd4bf"     # бирюза — второй акцент
C_WARN    = "#fbbf24"
C_DANGER  = "#f43f5e"
C_OK      = "#4ade80"

# Сплошные цвета кнопок (v3.3: заливка вместо градиента — цвет несёт смысл, хром тихий).
# Достаточно тёмные, чтобы белый текст давал контраст ≥ 4.5:1.
BTN_KINDS = {
    "danger":  "#e11d48",
    "accent":  "#5b63e6",
    "success": "#16a34a",
}

# ── 1b. ТЕМЫ ──────────────────────────────────────────────────────
# ink — цвет «чернил»: поверхности и рамки это ink с альфой (белый на тёмной, тёмный на светлой).
THEMES = {
    "dark": dict(
        ink=(255, 255, 255), text="#e8eaf3", dim="#8a91a8", faint="#5b6278",
        accent="#8b93ff", accent2="#2dd4bf", warn="#fbbf24", danger="#f43f5e", ok="#4ade80",
        frame_top=(26, 28, 38, 250), frame_bot=(17, 18, 25, 250),
        border_a=(46, 14), shadow_a=11, notch=(12, 13, 20, 130),
        popup="#1d2030", msg_bg="#1a1c26", win_glyph="#a9b0c6", badge_fg="#b4bad0",
        sel_text="#ffffff"),
    "light": dict(
        ink=(24, 28, 50), text="#1b1f33", dim="#5a6179", faint="#8b91a7",
        accent="#5b63e6", accent2="#0d9488", warn="#b45309", danger="#e11d48", ok="#16a34a",
        frame_top=(249, 250, 254, 250), frame_bot=(236, 238, 247, 250),
        border_a=(70, 28), shadow_a=8, notch=(244, 245, 251, 190),
        popup="#ffffff", msg_bg="#f4f5fb", win_glyph="#5a6179", badge_fg="#4a5170",
        sel_text="#1b1f33"),
}
_theme_name = "dark"

SHADOW_M = 16      # поле вокруг окна под тень
BODY_R   = 16      # радиус скругления окна
ROW_R    = 11      # радиус пилюли строки
ROW_H    = 54


# ── 2. БАЗОВЫЕ ХЕЛПЕРЫ ────────────────────────────────────────────
def font(px: float, weight=QFont.Weight.Normal, spacing: float = 0.0) -> QFont:
    """Шрифт приложения (Inter) нужного размера в пикселях. Табличные цифры включены."""
    f = QFont(QApplication.font())
    f.setPixelSize(int(px))
    f.setWeight(weight)
    if spacing:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    try:                                   # tnum: цифры одной ширины (Qt >= 6.7)
        f.setFeature(QFont.Tag("tnum"), 1)
    except Exception:
        pass
    return f


def qc(hex_or_color, alpha: int | None = None) -> QColor:
    c = QColor(hex_or_color)
    if alpha is not None:
        c.setAlpha(alpha)
    return c


def T(key: str):
    """Токен текущей темы: T("popup"), T("frame_top") …"""
    return THEMES[_theme_name][key]


def theme_name() -> str:
    return _theme_name


def ink(alpha: int) -> QColor:
    """Поверхность/рамка: «чернила» темы с альфой (тёмная: белый, светлая: тёмно-синий)."""
    r, g, b = T("ink")
    return QColor(r, g, b, alpha)


def ink_css(alpha: int) -> str:
    r, g, b = T("ink")
    return f"rgba({r},{g},{b},{alpha})"


def accent_css(alpha: int) -> str:
    c = QColor(C_ACCENT)
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"


def set_theme(name: str) -> None:
    """Переключает тему: пересчитывает C_* и QSS-константы. Окна пере-красьте через retheme()."""
    global _theme_name, C_TEXT, C_DIM, C_FAINT, C_ACCENT, C_ACCENT2, C_WARN, C_DANGER, C_OK
    global FORM_QSS, TABLE_QSS, LIST_QSS, LIST_INDICATOR_QSS
    if name not in THEMES:
        return
    _theme_name = name
    th = THEMES[name]
    C_TEXT, C_DIM, C_FAINT = th["text"], th["dim"], th["faint"]
    C_ACCENT, C_ACCENT2, C_WARN = th["accent"], th["accent2"], th["warn"]
    C_DANGER, C_OK = th["danger"], th["ok"]
    FORM_QSS, TABLE_QSS = form_qss(), table_qss()
    LIST_QSS, LIST_INDICATOR_QSS = list_qss(), list_indicator_qss()


def bar_gradient(x1: float, x2: float) -> QLinearGradient:
    """Удалён в v3.3 (радужная заливка бара не несла смысла). Оставлен пустым для совместимости."""
    raise NotImplementedError("bar_gradient удалён в v3.3: draw_scale_bar заливает одним цветом")


def make_icon(bg_hex: str, draw) -> QIcon:
    """Иконка приложения: скруглённый квадрат (22 %) + любой глиф.
    draw(painter) рисует на холсте 128×128."""
    size = 128
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, size, size, size * 0.22, size * 0.22)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(bg_hex))
    p.drawPath(path)
    draw(p)
    p.end()
    return QIcon(pix)


def draw_scale_bar(p: QPainter, track: QRectF, frac: float, ticks: bool = True,
                   min_px: float = 2.5, color: QColor | str | None = None):
    """Бар на шкале 0..1: трек, заливка ОДНИМ цветом (по умолчанию — акцент), насечки 25/50/75 %.
    Свой цвет — только когда он несёт смысл (уровень диска: C_OK/C_WARN/C_DANGER по порогам).
    Радужный градиент удалён в v3.3: цвет по позиции — украшение, не информация."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = track.height() / 2
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(ink(20))
    p.drawRoundedRect(track, r, r)
    frac = max(0.0, min(1.0, frac))
    if frac > 0:
        w = min(max(track.width() * frac, min_px), track.width())
        p.setBrush(QColor(color) if color is not None else QColor(C_ACCENT))
        p.drawRoundedRect(QRectF(track.left(), track.top(), w, track.height()), r, r)
    if ticks:
        for k in (0.25, 0.50, 0.75):
            x = track.left() + track.width() * k
            p.fillRect(QRectF(x - 0.5, track.top(), 1.0, track.height()), QColor(*T("notch")))
    p.restore()


def paint_row_pill(p: QPainter, rect: QRectF, col: int, ncols: int,
                   selected: bool, hovered: bool, viewport_w: float = 0.0):
    """Фон строки таблицы — одна «пилюля» на все колонки.
    Вызывать из QStyledItemDelegate.paint() для КАЖДОЙ ячейки.
    Трюк: рисуем скруглённый прямоугольник, выходящий за границы ячейки, и обрезаем
    клипом до ячейки — скругления остаются только на крайних колонках.
    viewport_w больше не используется (градиент выделения заменён плоской заливкой,
    v3.3) и оставлен в сигнатуре для совместимости старых вызовов."""
    R = ROW_R
    row = QRectF(rect.left(), rect.top() + 2, rect.width(), rect.height() - 4)
    first, last = col == 0, col == ncols - 1
    ext = row.adjusted(0 if first else -R * 2, 0, 0 if last else R * 2, 0)
    p.setClipRect(rect)
    if selected:
        p.setBrush(qc(C_ACCENT, 66))          # активное состояние — шаг поверхности + hairline
        p.setPen(QPen(qc(C_ACCENT, 105), 1))
        p.drawRoundedRect(ext.adjusted(0.5, 0.5, -0.5, -0.5), R, R)
    elif hovered:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ink(11))
        p.drawRoundedRect(ext, R, R)


def paint_selection_marker(p: QPainter, rect: QRectF, cy: float):
    """Акцентная полоска слева у выбранной строки (в первой колонке). Сплошной акцент (v3.3)."""
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(C_ACCENT))
    p.drawRoundedRect(QRectF(rect.left() + 5, cy - 10, 3, 20), 1.5, 1.5)


def paint_avatar(p: QPainter, tile: QRectF, name: str, icon_pm: QPixmap | None = None,
                 letter_font: QFont | None = None):
    """Аватар 34×34: иконка из темы на матовой плитке, иначе буква на градиенте,
    цвет которого стабильно выводится из хэша имени."""
    p.setPen(Qt.PenStyle.NoPen)
    if icon_pm is not None and not icon_pm.isNull():
        p.setBrush(ink(16))
        p.drawRoundedRect(tile, 10, 10)
        dpr = icon_pm.devicePixelRatio()
        p.drawPixmap(QPointF(tile.center().x() - icon_pm.width() / dpr / 2,
                             tile.center().y() - icon_pm.height() / dpr / 2), icon_pm)
        return
    hue = zlib.crc32(name.encode()) % 360
    g = QLinearGradient(tile.topLeft(), tile.bottomRight())
    g.setColorAt(0, QColor.fromHsl(hue, 150, 130))
    g.setColorAt(1, QColor.fromHsl((hue + 40) % 360, 160, 88))
    p.setBrush(QBrush(g))
    p.drawRoundedRect(tile, 10, 10)
    letter = next((ch for ch in name if ch.isalnum()), "•").upper()
    p.setFont(letter_font or font(15, QFont.Weight.Bold))
    p.setPen(QColor(255, 255, 255, 240))
    p.drawText(tile, Qt.AlignmentFlag.AlignCenter, letter)


def paint_badge(p: QPainter, rect: QRectF, text: str, f: QFont | None = None):
    """Бейдж-пилюля («×17», «NEW», «3»)."""
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(ink(22))
    p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
    p.setFont(f or font(10, QFont.Weight.Bold))
    p.setPen(QColor(T("badge_fg")))
    p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)


# ── 3. ОКНО ───────────────────────────────────────────────────────
class DragArea(QWidget):
    """Перетаскивание окна за заголовок. startSystemMove() вызывается у ОКНА верхнего уровня:
    у дочернего виджета windowHandle() в Qt6 всегда None, и на Wayland (где окно нельзя двигать
    через move()) шапка просто не тащила. Запасной путь через move() — для платформ без startSystemMove."""
    def __init__(self, parent):
        super().__init__(parent)
        self._p, self._start = parent, None

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(e)
        wh = self.window().windowHandle()                   # НЕ self.windowHandle(): там None
        if wh is not None and wh.startSystemMove():
            e.accept()
            return
        self._start = e.globalPosition().toPoint()          # запасной путь: двигаем сами
        self._p._offset = self._start - self._p.pos()

    def mouseMoveEvent(self, e):
        if e.buttons() == Qt.MouseButton.LeftButton and self._start is not None \
                and getattr(self._p, '_offset', None) is not None:
            if (e.globalPosition().toPoint() - self._start).manhattanLength() > 5:
                self._p.move(e.globalPosition().toPoint() - self._p._offset)

    def mouseReleaseEvent(self, e):
        self._start, self._p._offset = None, None
        super().mouseReleaseEvent(e)


class WinButton(QAbstractButton):
    """Кнопка окна 28×28: kind = 'min' | 'max' | 'close' (рисуется вручную)."""
    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setFixedSize(28, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def enterEvent(self, e):
        self.update(); super().enterEvent(e)

    def leaveEvent(self, e):
        self.update(); super().leaveEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        hov, down, close = self.underMouse(), self.isDown(), self.kind == "close"
        if hov:
            bg = QColor(244, 63, 94, 190 if down else 235) if close else ink(34 if down else 22)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 8, 8)
        pen = QPen(QColor("#ffffff") if (hov and close) else QColor(T("win_glyph")), 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        c = QPointF(self.width() / 2, self.height() / 2)
        if close:
            d = 4.0
            p.drawLine(QPointF(c.x() - d, c.y() - d), QPointF(c.x() + d, c.y() + d))
            p.drawLine(QPointF(c.x() - d, c.y() + d), QPointF(c.x() + d, c.y() - d))
        elif self.kind == "max":
            p.setBrush(Qt.BrushStyle.NoBrush)
            if self.window().isMaximized():          # «восстановить»: два квадрата
                p.drawRoundedRect(QRectF(c.x() - 4.5, c.y() - 2.5, 7, 7), 1.5, 1.5)
                p.drawLine(QPointF(c.x() - 2.5, c.y() - 4.5), QPointF(c.x() + 4.5, c.y() - 4.5))
                p.drawLine(QPointF(c.x() + 4.5, c.y() - 4.5), QPointF(c.x() + 4.5, c.y() + 2.5))
            else:                                    # «развернуть»: один квадрат
                p.drawRoundedRect(QRectF(c.x() - 4.5, c.y() - 4.5, 9, 9), 2, 2)
        else:
            p.drawLine(QPointF(c.x() - 4.5, c.y() + 0.5), QPointF(c.x() + 4.5, c.y() + 0.5))
        p.end()


class _TitleDblClick(QObject):
    """Фильтр: двойной клик по заголовку = развернуть/восстановить; в развёрнутом виде
    заголовок не «тащит» окно."""
    def __init__(self, win):
        super().__init__(win)
        self._win = win

    def eventFilter(self, obj, e):
        t = e.type()
        if t not in (QEvent.Type.MouseButtonDblClick, QEvent.Type.MouseButtonPress, QEvent.Type.MouseMove):
            return False                      # остальные события (в т.ч. при удалении окна) не трогаем
        try:
            if t == QEvent.Type.MouseButtonDblClick and e.button() == Qt.MouseButton.LeftButton:
                self._win.toggle_maximize()
                return True
            if t != QEvent.Type.MouseButtonDblClick and self._win.isMaximized():
                return True
        except RuntimeError:                  # окно уже удалено при выходе — просто пропускаем
            pass
        return False


class _AuroraFrame:
    """Общая рамка Aurora: тень, тональный фон, рамка, заголовок.
    Подмешивается и в окно (AuroraWindow), и в модальный диалог (AuroraDialog).

        class MyApp(A.AuroraWindow):
            def __init__(self):
                super().__init__("Мой скрипт", size=(600, 740), tag="PRO", icon=my_icon)
                self.body.addWidget(...)      # содержимое — в self.body (QVBoxLayout)
    """
    def __init__(self, title: str, size=(600, 740), tag: str | None = None,
                 icon: QIcon | None = None, on_top: bool = True,
                 dialog: bool = False, parent=None, resizable: bool = False):
        super().__init__(parent)
        self._bg: QPixmap | None = None
        self._offset = None
        self._title_dbl = None
        self._resizable = resizable and not dialog
        self.setWindowTitle(title)
        self.resize(*size)
        flags = Qt.WindowType.FramelessWindowHint
        if dialog:
            flags |= Qt.WindowType.Dialog
        if on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        root = QVBoxLayout(self)
        root.setContentsMargins(SHADOW_M, SHADOW_M, SHADOW_M, SHADOW_M)
        root.setSpacing(0)

        self.title_bar = DragArea(self)
        self.title_bar.setFixedHeight(52)
        self.title_layout = QHBoxLayout(self.title_bar)
        self.title_layout.setContentsMargins(18, 0, 12, 0)
        self.title_layout.setSpacing(10)
        if icon is not None:
            ico = QLabel()
            ico.setPixmap(icon.pixmap(QSize(24, 24)))
            ico.setFixedSize(24, 24)
            self.title_layout.addWidget(ico)
        lbl = QLabel(title)
        lbl.setFont(font(15, QFont.Weight.Bold))
        self.title_label = lbl
        self.title_layout.addWidget(lbl)
        self.title_tag = None
        if tag:
            t = QLabel(tag)
            t.setFont(font(9, QFont.Weight.Bold, 1.2))
            self.title_tag = t
            self.title_layout.addWidget(t, 0, Qt.AlignmentFlag.AlignVCenter)   # не на всю высоту шапки
        self.title_layout.addStretch()
        self._ctrl_index = self.title_layout.count()      # сюда add_title_widget() вставляет свои кнопки
        b_close = WinButton("close")
        b_close.clicked.connect(self.close)
        self.btn_max = None
        if not dialog:                       # у диалога кнопок «свернуть/развернуть» нет
            b_min = WinButton("min")
            b_min.clicked.connect(self.showMinimized)
            self.title_layout.addWidget(b_min)
            if self._resizable:
                self.btn_max = WinButton("max")
                self.btn_max.clicked.connect(self.toggle_maximize)
                self.title_layout.addWidget(self.btn_max)
        self.title_layout.addWidget(b_close)
        root.addWidget(self.title_bar)
        self._apply_title_style()
        if self._resizable:
            self._install_title_filter()
            app = QApplication.instance()
            if app is not None:               # на выходе из приложения фильтр снимается ДО удаления окон
                app.aboutToQuit.connect(self._drop_title_filter)

        self.body = QVBoxLayout()          # сюда кладите всё содержимое
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(0)
        root.addLayout(self.body, 1)

    # ── тема и заголовок ───────────────────────────────────────────
    def _apply_title_style(self):
        # размер/вес заданы и в QSS: общее правило приложения «QLabel { font-size }» иначе перебьёт setFont
        self.title_label.setStyleSheet(f"color: {C_TEXT}; font-size: 15px; font-weight: bold;")
        if self.title_tag is not None:
            # letter-spacing — здесь же в QSS: font-size из QSS сбрасывает интервал,
            # заданный через QFont, и тег сжимался до узкой и кривой плашки
            self.title_tag.setStyleSheet(
                f"color: {C_ACCENT}; background-color: {accent_css(38)}; font-size: 9px; font-weight: bold;"
                "letter-spacing: 1.2px; border-radius: 6px; padding: 2px 7px;")

    def retheme(self):
        """Вызвать после A.set_theme(): перерисовывает фон окна, заголовок и кнопки заголовка."""
        self._apply_title_style()
        self._build_bg()
        self.update()
        for w in self.title_bar.findChildren(QAbstractButton):
            w.update()

    def add_title_widget(self, w: QWidget):
        """Добавляет виджет в заголовок слева от кнопок окна (тема, язык, меню …)."""
        self.title_layout.insertWidget(self._ctrl_index, w)
        self._ctrl_index += 1

    # ── фильтр заголовка (двойной клик = развернуть) ───────────────
    def _install_title_filter(self):
        if self._title_dbl is None:
            self._title_dbl = _TitleDblClick(self)
            self.title_bar.installEventFilter(self._title_dbl)

    def _drop_title_filter(self):
        f, self._title_dbl = self._title_dbl, None
        if f is not None:
            try:
                self.title_bar.removeEventFilter(f)
            except RuntimeError:
                pass

    def closeEvent(self, e):
        super().closeEvent(e)
        if e.isAccepted():
            self._drop_title_filter()

    def showEvent(self, e):
        super().showEvent(e)
        if self._resizable:                   # окно закрывали и показали снова
            self._install_title_filter()

    # ── развернуть / ресайз за края ────────────────────────────────
    def _is_max(self) -> bool:
        return self.isMaximized() or self.isFullScreen()

    def toggle_maximize(self):
        if self._is_max():
            self.showNormal()
        else:
            self.showMaximized()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.Type.WindowStateChange and self._resizable:
            m = 0 if self._is_max() else SHADOW_M
            self.layout().setContentsMargins(m, m, m, m)
            self._build_bg()
            self.update()
            if self.btn_max is not None:
                self.btn_max.update()

    def _edges_at(self, pos):
        if not self._resizable or self._is_max():
            return Qt.Edge(0)
        m, w, h = SHADOW_M, self.width(), self.height()
        edges = Qt.Edge(0)
        if pos.x() < m:
            edges |= Qt.Edge.LeftEdge
        elif pos.x() >= w - m:
            edges |= Qt.Edge.RightEdge
        if pos.y() < m:
            edges |= Qt.Edge.TopEdge
        elif pos.y() >= h - m:
            edges |= Qt.Edge.BottomEdge
        return edges

    def mousePressEvent(self, e):
        if self._resizable and e.button() == Qt.MouseButton.LeftButton:
            ed = self._edges_at(e.position().toPoint())
            wh = self.windowHandle()
            if ed and wh is not None:
                wh.startSystemResize(ed)
                e.accept()
                return
        super().mousePressEvent(e)

    # фон рисуется один раз на resize и кэшируется в QPixmap
    def _build_bg(self):
        w, h = self.width(), self.height()
        dpr = self.devicePixelRatioF()
        pix = QPixmap(max(1, int(w * dpr)), max(1, int(h * dpr)))
        pix.setDevicePixelRatio(dpr)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        full = self._resizable and self._is_max()
        M, R = (0, 0) if full else (SHADOW_M, BODY_R)
        body = QRectF(M, M, w - 2 * M, h - 2 * M)

        p.setPen(Qt.PenStyle.NoPen)
        for i in range(M):                                   # тень: 16 слоёв
            a = int(T("shadow_a") * (1 - i / M) ** 2)
            if a > 0:
                p.setBrush(QColor(0, 0, 0, a))
                p.drawRoundedRect(body.adjusted(-i, -i, i, i).translated(0, 4), R + i, R + i)

        g = QLinearGradient(0, body.top(), 0, body.bottom())  # фон: тональный сдвиг одного нейтрального
        g.setColorAt(0, QColor(*T("frame_top")))
        g.setColorAt(1, QColor(*T("frame_bot")))
        p.setBrush(QBrush(g))
        p.drawRoundedRect(body, R, R)
        # свечение удалено в v3.3: цветное свечение — признак шаблонного AI-UI; глубину
        # дают тональный градиент фона, hairline-рамка и слои поверхности ink с альфой

        if not full:
            bd = QLinearGradient(0, body.top(), 0, body.bottom())  # рамка
            bd.setColorAt(0, ink(T("border_a")[0]))
            bd.setColorAt(1, ink(T("border_a")[1]))
            p.setPen(QPen(QBrush(bd), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(body.adjusted(0.5, 0.5, -0.5, -0.5), R, R)
        p.end()
        self._bg = pix

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._build_bg()

    def paintEvent(self, _):
        if self._bg is None:
            self._build_bg()
        p = QPainter(self)
        p.drawPixmap(0, 0, self._bg)
        p.end()


class AuroraWindow(_AuroraFrame, QWidget):
    """Окно: super().__init__("Заголовок", size=(W+32, H+32), tag=..., icon=...)."""


class AuroraDialog(_AuroraFrame, QDialog):
    """Модальный диалог в стиле Aurora (без системной рамки).
    exec() / accept() / reject() работают как у QDialog; содержимое — в self.body."""
    def __init__(self, title: str, size=(600, 400), tag: str | None = None,
                 icon: QIcon | None = None, parent=None):
        super().__init__(title, size=size, tag=tag, icon=icon,
                         on_top=False, dialog=True, parent=parent)


# ── 4. КНОПКИ И ИНДИКАТОРЫ ────────────────────────────────────────
class PrimaryButton(QPushButton):
    """Главная кнопка: сплошная заливка смыслового цвета, иконка-стоп (опц.), keycap-подсказка (опц.).
    kind: 'danger' | 'accent' | 'success'. Радиус 10 — как у всех контролов (v3.3)."""
    def __init__(self, text: str, kind: str = "accent", hint: str | None = None,
                 stop_icon: bool = False, parent=None, height: int = 48):
        super().__init__(text, parent)
        self.kind, self.hint, self.stop_icon = kind, hint, stop_icon
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedHeight(height)

    def sizeHint(self):                       # ширина по тексту, который рисуем сами (13.5 px DemiBold)
        w = QFontMetrics(font(13.5, QFont.Weight.DemiBold)).horizontalAdvance(self.text()) + 40
        if self.stop_icon:
            w += 26
        if self.hint:                                  # текст + зазор 10 + keycap, как одна группа
            w += QFontMetrics(font(10, QFont.Weight.Bold)).horizontalAdvance(self.hint) + 16 + 10
        return QSize(w, self.height())

    def enterEvent(self, e):
        self.update(); super().enterEvent(e)

    def leaveEvent(self, e):
        self.update(); super().leaveEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        en, hov, down = self.isEnabled(), self.underMouse(), self.isDown()
        if en:
            base = QColor(BTN_KINDS[self.kind])       # сплошная заливка (v3.3): градиент и блик — сопли
            if hov:
                base = base.lighter(108)
            if down:
                base = base.darker(112)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(base)
        else:
            p.setPen(QPen(ink(14), 1))
            p.setBrush(ink(12))
        p.drawRoundedRect(r, 10, 10)

        fg = QColor("#ffffff") if en else QColor(C_DIM)
        f = font(13.5, QFont.Weight.DemiBold)
        p.setFont(f)
        text = self.text()
        tw = QFontMetrics(f).horizontalAdvance(text)
        icon_w = 26 if (en and self.stop_icon) else 0
        kf = font(10, QFont.Weight.Bold)
        kw = QFontMetrics(kf).horizontalAdvance(self.hint) + 16 if (en and self.hint) else 0
        gap = 10 if kw else 0
        x0 = (self.width() - (tw + icon_w + gap + kw)) / 2         # значок + текст + keycap — ОДНА группа по центру
        cy = self.height() / 2
        if icon_w:
            pen = QPen(fg, 1.7); pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(x0 + 8, cy), 7.5, 7.5)
            p.drawLine(QPointF(x0 + 5.2, cy - 2.8), QPointF(x0 + 10.8, cy + 2.8))
            p.drawLine(QPointF(x0 + 5.2, cy + 2.8), QPointF(x0 + 10.8, cy - 2.8))
        p.setPen(fg)
        p.drawText(QRectF(x0 + icon_w, 0, tw + 4, self.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
        if kw:                                                     # keycap сразу за текстом, а не у края кнопки
            p.setFont(kf)
            kr = QRectF(x0 + icon_w + tw + gap, cy - 10, kw, 20)
            p.setPen(QPen(QColor(255, 255, 255, 60), 1))
            p.setBrush(QColor(0, 0, 0, 45))
            p.drawRoundedRect(kr, 6, 6)
            p.setPen(QColor(255, 255, 255, 215))
            p.drawText(kr, Qt.AlignmentFlag.AlignCenter, self.hint)
        p.end()


class GhostButton(QPushButton):
    """Вторичная кнопка: матовая плитка с тонкой рамкой."""
    def __init__(self, text: str, parent=None, height: int = 40):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedHeight(height)

    def sizeHint(self):                       # ширина по тексту, который рисуем сами (13 px DemiBold)
        return QSize(QFontMetrics(font(13, QFont.Weight.DemiBold)).horizontalAdvance(self.text()) + 28,
                     self.height())

    def enterEvent(self, e):
        self.update(); super().enterEvent(e)

    def leaveEvent(self, e):
        self.update(); super().leaveEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        a = 34 if self.isDown() else (20 if self.underMouse() else 11)
        p.setPen(QPen(ink(28 if self.isEnabled() else 12), 1))
        p.setBrush(ink(a if self.isEnabled() else 8))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)
        p.setFont(font(13, QFont.Weight.DemiBold))
        p.setPen(QColor(C_TEXT if self.isEnabled() else C_FAINT))
        p.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignCenter, self.text())
        p.end()


class LiveDot(QWidget):
    """Пульсирующая зелёная точка «активно»."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self._t = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(2000)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(self._tick)
        self._anim.start()

    def _tick(self, v):
        self._t = float(v); self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        ring = QColor(C_OK); ring.setAlpha(int(110 * (1 - self._t)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ring)
        p.drawEllipse(c, 3 + 4 * self._t, 3 + 4 * self._t)
        p.setBrush(QColor(C_OK))
        p.drawEllipse(c, 3.0, 3.0)
        p.end()


class StatCard(QWidget):
    """Карточка-метрика: ЗАГОЛОВОК, крупное значение, малая единица, значение справа, бар (опц.).
        card.set_data(0.36, "5.2", "/ 14.4 GB", "36%")
    bar=True (по умолчанию) — метрика имеет долю (заполнено/всего); для статичных чисел
    бар=False: число ≠ прогресс, лишний бар читается как украшение (v3.3).
    bar_color — свой цвет бара (по умолчанию акцент): цвет данных, отделённый от
    акцента хрома; QColor или "#hex". При смене темы цвет не пересчитывается —
    для темы с переключением создавайте карточку заново или давайте QColor по роли."""
    def __init__(self, title: str, parent=None, bar: bool = True,
                 bar_color: QColor | str | None = None):
        super().__init__(parent)
        self.setFixedHeight(84)
        self._bar = bar
        self._bar_color = bar_color
        self._title, self._big, self._small, self._right = title, "–", "", ""
        self._frac = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(450)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)

    def _on_anim(self, v):
        self._frac = float(v); self.update()

    def set_data(self, frac: float, big: str, small: str, right: str):
        self._big, self._small, self._right = big, small, right
        self._anim.stop()
        self._anim.setStartValue(self._frac)
        self._anim.setEndValue(max(0.0, min(1.0, frac)))
        self._anim.start()
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        w, h = self.width(), self.height()
        p.setPen(QPen(ink(18), 1))
        p.setBrush(ink(9))
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 13, 13)
        p.setFont(font(10, QFont.Weight.Bold, 1.2))
        p.setPen(QColor(C_DIM))
        p.drawText(QRectF(16, 12, w - 32, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._title)
        base_y, fb = 52, font(24, QFont.Weight.Bold)
        p.setFont(fb); p.setPen(QColor(C_TEXT))
        p.drawText(QPointF(16, base_y), self._big)
        adv = QFontMetrics(fb).horizontalAdvance(self._big)
        p.setFont(font(12, QFont.Weight.Medium)); p.setPen(QColor(C_DIM))
        p.drawText(QPointF(16 + adv + 6, base_y), self._small)
        p.setFont(font(12.5, QFont.Weight.DemiBold)); p.setPen(QColor(C_TEXT))
        p.drawText(QRectF(16, base_y - 17, w - 32, 22), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._right)
        if self._bar:
            draw_scale_bar(p, QRectF(16, h - 20, w - 32, 6), self._frac, ticks=False,
                           color=self._bar_color)
        p.end()


# ── 5. ТАБЛИЦА ────────────────────────────────────────────────────
class AuroraHeader(QHeaderView):
    """Плоский заголовок: КАПС 10px, акцентная стрелка у активной колонки.
    aligns — 'left'|'center' на колонку; pads — левый отступ для 'left';
    sortable — индексы кликабельных колонок."""
    def __init__(self, parent=None, sortable=(1, 2), aligns=("left", "center", "left"), pads=(16, 0, 14)):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.sortable, self.aligns, self.pads = tuple(sortable), aligns, pads
        self.sort_col, self.sort_asc = -1, False
        self.setSectionsClickable(True)
        self.setHighlightSections(False)
        self.setMouseTracking(True)
        self.setFixedHeight(36)

    def set_sort(self, col: int, asc: bool):
        self.sort_col, self.sort_asc = col, asc
        self.viewport().update()

    def mouseMoveEvent(self, e):
        idx = self.logicalIndexAt(e.position().toPoint())
        self.viewport().setCursor(Qt.CursorShape.PointingHandCursor if idx in self.sortable
                                  else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(e)

    def paintSection(self, p, rect, i):
        p.save()
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        text = str(self.model().headerData(i, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) or "")
        active = i == self.sort_col
        f = font(10, QFont.Weight.Bold, 1.1)
        p.setFont(f)
        tw = QFontMetrics(f).horizontalAdvance(text)
        total = tw + (12 if active else 0)
        al = self.aligns[i] if i < len(self.aligns) else "left"
        pad = self.pads[i] if i < len(self.pads) else 14
        x = rect.left() + (rect.width() - total) / 2 if al == "center" else rect.left() + pad
        col = QColor(C_ACCENT if active else C_DIM)
        p.setPen(col)
        p.drawText(QRectF(x, rect.top(), tw + 2, rect.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
        if active:
            ax, cy = x + tw + 7, rect.top() + rect.height() / 2
            if self.sort_asc:
                poly = QPolygonF([QPointF(ax, cy + 2.2), QPointF(ax + 8, cy + 2.2), QPointF(ax + 4, cy - 2.6)])
            else:
                poly = QPolygonF([QPointF(ax, cy - 2.2), QPointF(ax + 8, cy - 2.2), QPointF(ax + 4, cy + 2.6)])
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(col); p.drawPolygon(poly)
        p.fillRect(QRectF(rect.left(), rect.bottom(), rect.width(), 1), ink(16))
        p.restore()


class HoverTable(QTableWidget):
    """QTableWidget, помнящий строку под курсором (hover_row) — для подсветки целой строки."""
    def __init__(self, rows, cols, parent=None):
        super().__init__(rows, cols, parent)
        self.hover_row = -1
        self.setMouseTracking(True)

    def mouseMoveEvent(self, e):
        r = self.indexAt(e.position().toPoint()).row()
        if r != self.hover_row:
            self.hover_row = r
            self.viewport().update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        if self.hover_row != -1:
            self.hover_row = -1
            self.viewport().update()
        super().leaveEvent(e)


def table_qss() -> str:
    return f"""
    QTableWidget {{ background: transparent; border: none; outline: none; }}
    QHeaderView {{ background: transparent; border: none; }}
    QHeaderView::section {{ background: transparent; border: none; }}
    QTableCornerButton::section {{ background: transparent; border: none; }}
    QScrollBar:vertical {{ background: transparent; width: 8px; margin: 6px 2px 6px 0; }}
    QScrollBar::handle:vertical {{ background: {ink_css(36)}; border-radius: 3px; min-height: 36px; }}
    QScrollBar::handle:vertical:hover {{ background: {ink_css(70)}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
"""


TABLE_QSS = table_qss()


def setup_aurora_table(table: QTableWidget, row_h: int = ROW_H):
    """Вызывать ПОСЛЕ setHorizontalHeader(AuroraHeader(table)) и setHorizontalHeaderLabels()."""
    table.verticalHeader().hide()
    table.setFrameShape(QFrame.Shape.NoFrame)
    table.setShowGrid(False)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    table.setAlternatingRowColors(False)
    table.viewport().setAutoFillBackground(False)
    pal = table.palette()                     # Fusion иначе заливает выбранную строку синим ПОД делегатом
    pal.setColor(QPalette.ColorRole.Highlight, QColor(0, 0, 0, 0))
    table.setPalette(pal)
    table.verticalHeader().setDefaultSectionSize(row_h)
    table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
    table.setStyleSheet(TABLE_QSS)


# ── 6. QSS ДЛЯ СТАНДАРТНЫХ ВИДЖЕТОВ ───────────────────────────────
CHECK_PNG = os.path.join(tempfile.gettempdir(), "aurora_check.png")


def ensure_check_icon():
    """PNG-галочка для QSS (image: url(...)): сам QSS галочку нарисовать не умеет."""
    if os.path.exists(CHECK_PNG):
        return
    pix = QPixmap(18, 18)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#ffffff"), 2.2)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.drawPolyline(QPolygonF([QPointF(4.6, 9.6), QPointF(7.8, 12.8), QPointF(13.6, 5.6)]))
    p.end()
    try:
        pix.save(CHECK_PNG, "PNG")
    except Exception:
        pass


def form_qss() -> str:
    """QSS стандартных виджетов для текущей темы. Применять: window.setStyleSheet(A.FORM_QSS).
    Включает QMessageBox (иначе он наследует только часть правил и может стать нечитаемым),
    галочку чекбокса и disabled-состояние."""
    popup, msg_bg, sel = T("popup"), T("msg_bg"), T("sel_text")
    return f"""
    QToolTip {{ background-color: {popup}; color: {C_TEXT}; border: 1px solid {ink_css(30)};
               border-radius: 8px; padding: 6px 10px; }}
    QLabel {{ color: {C_TEXT}; }}
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
        background-color: {ink_css(12)}; color: {C_TEXT};
        border: 1px solid {ink_css(24)}; border-radius: 10px; padding: 8px 12px;
        selection-background-color: {accent_css(110)}; }}
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
    QPlainTextEdit:focus, QTextEdit:focus {{ border: 1px solid {C_ACCENT}; background-color: {ink_css(16)}; }}
    QComboBox::drop-down {{ border: none; width: 24px; }}
    QComboBox QAbstractItemView {{ background-color: {popup}; border: 1px solid {ink_css(30)};
        border-radius: 10px; padding: 4px; outline: none; selection-background-color: {accent_css(90)}; }}
    QCheckBox {{ color: {C_TEXT}; spacing: 8px; }}
    QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 6px;
        border: 1px solid {ink_css(40)}; background: {ink_css(10)}; }}
    QCheckBox::indicator:checked {{ image: url({CHECK_PNG}); background-color: {C_ACCENT};
        border: 1px solid {C_ACCENT}; }}
    QCheckBox:disabled {{ color: {C_FAINT}; }}
    QMessageBox {{ background-color: {msg_bg}; }}
    QMessageBox QLabel {{ color: {C_TEXT}; background: transparent; }}
    QMessageBox QPushButton {{ background-color: {ink_css(11)};
        border: 1px solid {ink_css(28)}; border-radius: 10px;
        padding: 7px 18px; min-width: 72px; color: {C_TEXT}; font-weight: 600; }}
    QMessageBox QPushButton:hover {{ background-color: {ink_css(20)}; }}
    QMessageBox QPushButton:pressed {{ background-color: {ink_css(34)}; }}
    QMessageBox QPushButton:default {{ background-color: {accent_css(70)};
        border: 1px solid {C_ACCENT}; color: {sel}; }}
    QFileDialog QLabel {{ color: palette(window-text); }}
    QMenu {{ background-color: {popup}; color: {C_TEXT}; border: 1px solid {ink_css(30)};
        border-radius: 10px; padding: 6px; }}
    QMenu::item {{ padding: 7px 16px; border-radius: 7px; }}
    QMenu::item:selected {{ background-color: {accent_css(70)}; }}
    QSlider:horizontal {{ min-height: 26px; }}
    QSlider::groove:horizontal {{ height: 6px; border-radius: 3px; background: {ink_css(24)}; }}
    QSlider::sub-page:horizontal {{ background: {C_ACCENT}; border-radius: 3px; }}
    QSlider::handle:horizontal {{ width: 16px; margin: -5px 0; border-radius: 8px; background: #ffffff; }}
    QScrollBar:vertical {{ background: transparent; width: 8px; margin: 6px 2px 6px 0; }}
    QScrollBar::handle:vertical {{ background: {ink_css(36)}; border-radius: 3px; min-height: 36px; }}
    QScrollBar::handle:vertical:hover {{ background: {ink_css(70)}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
"""


def list_qss() -> str:
    return f"""
    QListWidget {{
        background: transparent;
        border: none;
        outline: none;
        padding: 0;
    }}
    QListWidget::item {{
        background: {ink_css(9)};
        border: 1px solid {ink_css(16)};
        border-radius: 10px;
        padding: 8px 12px;
        margin: 2px 0;
        color: {C_TEXT};
    }}
    QListWidget::item:hover {{ background: {ink_css(16)}; }}
    QListWidget::item:selected {{
        background: {accent_css(70)};
        border: 1px solid {accent_css(105)};
        color: {T("sel_text")};
    }}
"""


def list_indicator_qss() -> str:
    return f"""
    QListWidget::indicator {{ width: 18px; height: 18px; border-radius: 6px;
        border: 1px solid {ink_css(40)}; background: {ink_css(10)}; }}
    QListWidget::indicator:checked {{ background-color: {C_ACCENT};
        border: 1px solid {C_ACCENT}; image: url({CHECK_PNG}); }}
"""


FORM_QSS = form_qss()
LIST_QSS = list_qss()
LIST_INDICATOR_QSS = list_indicator_qss()


def setup_app(app: QApplication):
    """Один раз ПОСЛЕ создания QApplication: шрифт Inter 10 (нет Inter — первый из запасных, без хинтинга
    и со сглаживанием: так текст ровнее на HiDPI), стиль Fusion, PNG-галочка для QSS."""
    f = QFont("Inter", 10)
    have = set(QFontDatabase.families())
    for fam in ("Inter", "Inter Variable", "Noto Sans", "Cantarell", "DejaVu Sans"):
        if fam in have:
            f.setFamily(fam)
            break
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    app.setFont(f)
    app.setStyle("Fusion")
    ensure_check_icon()


# ── 7. НЕОБЯЗАТЕЛЬНЫЕ БЛОКИ ────────────────────────────────────────
# Ничто выше от этих блоков не зависит — в маленький скрипт берите только нужные.
def ru_plural(n, one, few, many):
    """Склонение по числу: ru_plural(n, "файл", "файла", "файлов"). Без него получается «1 файлов»."""
    n = abs(int(n))
    if 11 <= n % 100 <= 14:
        return many
    if n % 10 == 1:
        return one
    if 2 <= n % 10 <= 4:
        return few
    return many


def extra_qss() -> str:
    """Дополнение к FORM_QSS (в нём этого нет): вкладки, радиокнопки, горизонтальный скроллбар.
    Применять: window.setStyleSheet(A.FORM_QSS + A.extra_qss()); при смене темы — собрать заново."""
    return f"""
    QRadioButton {{ color: {C_TEXT}; spacing: 8px; }}
    QRadioButton::indicator {{ width: 16px; height: 16px; border-radius: 9px;
        border: 1px solid {ink_css(40)}; background: {ink_css(10)}; }}
    QRadioButton::indicator:checked {{ border: 1px solid {C_ACCENT};
        background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
            stop:0 #ffffff, stop:0.38 #ffffff, stop:0.45 {C_ACCENT}, stop:1 {C_ACCENT}); }}
    QTabWidget::pane {{ border: none; top: 0; }}
    QTabBar {{ background: transparent; }}
    QTabBar::tab {{ background: transparent; color: {C_DIM}; padding: 7px 16px; margin: 0 4px 0 0;
        border: 1px solid transparent; border-radius: 10px; font-weight: 600; }}
    QTabBar::tab:hover:!selected {{ background: {ink_css(11)}; color: {C_TEXT}; }}
    QTabBar::tab:selected {{ color: {C_TEXT}; border: 1px solid {accent_css(105)};
        background: {accent_css(56)}; }}
    QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 0 6px 2px 6px; }}
    QScrollBar::handle:horizontal {{ background: {ink_css(36)}; border-radius: 3px; min-width: 36px; }}
    QScrollBar::handle:horizontal:hover {{ background: {ink_css(70)}; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}
"""


def apply_cursors(root: QWidget):
    """«Рука» у кнопок, комбобоксов, ползунков и вкладок внутри root. Вызывать после построения интерфейса
    (и после динамического добавления виджетов). Явно заданные курсоры не трогает. На окно курсор не ставят."""
    for w in root.findChildren(QWidget):
        if isinstance(w, (QAbstractButton, QComboBox, QSlider, QTabBar)) \
                and not w.testAttribute(Qt.WidgetAttribute.WA_SetCursor):
            w.setCursor(Qt.CursorShape.PointingHandCursor)


@contextmanager
def busy_cursor():
    """Синхронная работа в GUI-потоке: `with A.busy_cursor(): ...` (курсор всегда восстанавливается)."""
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        yield
    finally:
        QApplication.restoreOverrideCursor()


def shrinkable(btn: QAbstractButton, min_w: int = 64) -> QAbstractButton:
    """У QPushButton minimumSizeHint == sizeHint, поэтому ряд кнопок раздувает окно.
    Разрешает кнопке ужиматься (aurora-кнопки рисуют текст сами и обрезают его)."""
    btn.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    btn.setMinimumWidth(min_w)
    return btn


_ROLE_ATTR = {"text": "C_TEXT", "dim": "C_DIM", "faint": "C_FAINT", "accent": "C_ACCENT",
              "ok": "C_OK", "warn": "C_WARN", "danger": "C_DANGER"}


def style_label(lbl: QLabel, role: str = "text") -> QLabel:
    """Цвет подписи по РОЛИ, а не по значению: роль запоминается и пересчитывается при смене темы
    (restyle_roles). Цвет, записанный числом в QSS/HTML, при смене темы «застывает»: белое на белом."""
    lbl.setProperty("role", role)
    lbl.setStyleSheet(f"color: {globals()[_ROLE_ATTR[role]]};")
    return lbl


def restyle_roles(root: QWidget):
    """После set_theme(): перекрасить все подписи, созданные через style_label."""
    for lbl in root.findChildren(QLabel):
        role = lbl.property("role")
        if role:
            style_label(lbl, role)


class DragAnywhere(QObject):
    """Окно тащится за любое «пустое» место: подписи, карточки, фон вкладок, поле тени. Не трогает заголовок
    (его ведёт DragArea, иначе ломается двойной клик «развернуть»), поля ввода, кнопки, списки, таблицы,
    скроллбары, вкладки-«ушки» и всё, что помечено setProperty("aurora_nodrag", True)."""
    _INTERACTIVE = (QAbstractButton, QLineEdit, QComboBox, QAbstractSlider, QAbstractScrollArea,
                    QAbstractSpinBox)

    def __init__(self, win):
        super().__init__(win)
        self._win = win

    def _interactive(self, obj: QWidget, pos) -> bool:
        if isinstance(obj, QTabBar) and obj.tabAt(pos) >= 0:
            return True
        w = obj
        while w is not None and w is not self._win:        # обход предков: viewport, контейнер скроллбара …
            if isinstance(w, self._INTERACTIVE) or w.property("aurora_nodrag"):
                return True
            w = w.parentWidget()
        return False

    def eventFilter(self, obj, e):
        if e.type() != QEvent.Type.MouseButtonPress:        # остальные события (и удаление окна) не трогаем
            return False
        try:
            win = self._win
            if e.button() != Qt.MouseButton.LeftButton or not isinstance(obj, QWidget) \
                    or obj.window() is not win or win.isMaximized() or win.isFullScreen():
                return False
            if obj is win:
                if win._edges_at(e.position().toPoint()):   # край поля тени — ресайз, его ведёт сама рамка
                    return False
            elif win.title_bar is obj or win.title_bar.isAncestorOf(obj) \
                    or self._interactive(obj, e.position().toPoint()):
                return False
            wh = win.windowHandle()
            return bool(wh is not None and wh.startSystemMove())
        except RuntimeError:                                # окно уже удалено при выходе — просто пропускаем
            return False


def install_drag_anywhere(win) -> DragAnywhere:
    """Включить перетаскивание за пустые места окна (AuroraWindow). Фильтр снимается на выходе из приложения."""
    f = DragAnywhere(win)
    app = QApplication.instance()
    app.installEventFilter(f)
    app.aboutToQuit.connect(lambda: app.removeEventFilter(f))
    return f


class _StepBtn(QAbstractButton):
    """Кнопка «−»/«+» внутри спинбокса: плитка + глиф пером, автоповтор при удержании."""
    def __init__(self, sign: int, parent):
        super().__init__(parent)
        self.sign = sign
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAutoRepeat(True)
        self.setAutoRepeatDelay(380)
        self.setAutoRepeatInterval(70)

    def enterEvent(self, e):
        self.update(); super().enterEvent(e)

    def leaveEvent(self, e):
        self.update(); super().leaveEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        en = self.isEnabled()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ink((34 if self.isDown() else (22 if self.underMouse() else 12)) if en else 6))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        pen = QPen(QColor(C_TEXT if en else C_FAINT), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        c, d = QPointF(self.width() / 2, self.height() / 2), 3.6
        p.drawLine(QPointF(c.x() - d, c.y()), QPointF(c.x() + d, c.y()))
        if self.sign > 0:
            p.drawLine(QPointF(c.x(), c.y() - d), QPointF(c.x(), c.y() + d))
        p.end()


class _Stepper(QWidget):
    """Числовое поле «− значение +». Кнопки и QLineEdit расставлены ВРУЧНУЮ в resizeEvent: QSS не влияет, значение
    всегда по центру, кнопки на месте при любой теме и масштабе (в layout у QAbstractButton нет своего размера,
    и кнопки схлопывались; системные стрелки QSpinBox под QSS съезжают влево). API как у QSpinBox/QDoubleSpinBox:
    value/setValue/setRange/setSingleStep/setSuffix/setDecimals, сигнал valueChanged; колесо и ↑/↓ — в фокусе;
    ввод с клавиатуры подтверждается Enter или уходом фокуса."""
    valueChanged = pyqtSignal(object)

    def __init__(self, decimals: int | None, parent=None):
        super().__init__(parent)
        self._dec = decimals                                  # None — целые
        self._lo, self._hi = (0, 99) if decimals is None else (0.0, 99.99)
        self._step = 1 if decimals is None else 0.1
        self._val = self._lo
        self._suffix = ""
        self.setProperty("aurora_nodrag", True)               # DragAnywhere не должен тащить окно за поле
        self.setMinimumSize(128, 40)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._minus, self._plus = _StepBtn(-1, self), _StepBtn(+1, self)
        self.edit = QLineEdit(self)
        self.edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.edit.setFrame(False)
        self.edit.setStyleSheet("background: transparent; border: none; padding: 0;")
        self.setFocusProxy(self.edit)
        self._minus.clicked.connect(lambda _=False: self.stepBy(-1))
        self._plus.clicked.connect(lambda _=False: self.stepBy(+1))
        self.edit.editingFinished.connect(self._commit)
        self.edit.installEventFilter(self)
        self._refresh()

    def _clamp(self, v):
        v = max(self._lo, min(self._hi, v))
        return int(round(v)) if self._dec is None else round(float(v), self._dec)

    def value(self):
        return self._val

    def setValue(self, v):
        nv = self._clamp(v)
        changed = nv != self._val
        self._val = nv
        self._refresh()
        if changed:
            self.valueChanged.emit(nv)

    def setRange(self, lo, hi):
        self._lo, self._hi = lo, hi
        self.setValue(self._val)

    def minimum(self):
        return self._lo

    def maximum(self):
        return self._hi

    def setSingleStep(self, s):
        self._step = s

    def singleStep(self):
        return self._step

    def setSuffix(self, s: str):
        self._suffix = s
        self._refresh()

    def setDecimals(self, n: int):
        if self._dec is not None:
            self._dec = n
            self.setValue(self._val)

    def stepBy(self, n: int):
        self.setValue(self._val + n * self._step)

    def stepUp(self):
        self.stepBy(1)

    def stepDown(self):
        self.stepBy(-1)

    def _refresh(self):
        txt = str(self._val) if self._dec is None else f"{self._val:.{self._dec}f}"
        self.edit.setText(txt + self._suffix)
        self._minus.setEnabled(self._val > self._lo)
        self._plus.setEnabled(self._val < self._hi)

    def _commit(self):
        m = re.search(r"[-+]?\d+(?:[.,]\d+)?", self.edit.text())
        if m:
            self.setValue(float(m.group().replace(",", ".")))
        else:
            self._refresh()

    def _place(self):
        m, bw, h = 5, 28, self.height()
        self._minus.setGeometry(m, m, bw, h - 2 * m)
        self._plus.setGeometry(self.width() - m - bw, m, bw, h - 2 * m)
        self.edit.setGeometry(m + bw + 4, m, max(10, self.width() - 2 * (m + bw + 4)), h - 2 * m)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place()

    def showEvent(self, e):
        super().showEvent(e)
        self._place()

    def eventFilter(self, obj, e):
        if obj is self.edit:
            t = e.type()
            if t == QEvent.Type.FocusIn:
                QTimer.singleShot(0, self.edit.selectAll)
                self.update()
            elif t == QEvent.Type.FocusOut:
                self.update()
            elif t == QEvent.Type.KeyPress and e.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                self.stepBy(1 if e.key() == Qt.Key.Key_Up else -1)
                return True
            elif t == QEvent.Type.Wheel and self.edit.hasFocus():
                self.stepBy(1 if e.angleDelta().y() > 0 else -1)
                return True
        return super().eventFilter(obj, e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        focus, en = self.edit.hasFocus(), self.isEnabled()
        p.setPen(QPen(QColor(C_ACCENT) if focus else ink(24 if en else 12), 1))
        p.setBrush(ink(16 if focus else (12 if en else 7)))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)
        p.end()


class AuroraSpinBox(_Stepper):
    """Целое число с кнопками «−/+»: вместо QSpinBox (у него под QSS съезжают значение и стрелки)."""
    def __init__(self, parent=None):
        super().__init__(None, parent)


class AuroraDoubleSpinBox(_Stepper):
    """Дробное число с кнопками «−/+» (setDecimals, setSingleStep)."""
    def __init__(self, parent=None):
        super().__init__(2, parent)


class _ComboPopup(QWidget):
    """Выпадающий список Aurora: собственное окно-попап (прозрачное, скруглённое). Нативный контейнер
    QComboBox рисует под списком прямоугольную подложку, которую QSS не убирает."""
    ROW_H = 30

    def __init__(self, combo: "AuroraComboBox"):
        super().__init__(combo.window(), Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._combo = combo
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        self.lst = QListWidget()
        self.lst.setFrameShape(QFrame.Shape.NoFrame)
        self.lst.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.lst.setMouseTracking(True)
        self.lst.setStyleSheet(
            "QListWidget { background: transparent; border: none; outline: none; }"
            f"QListWidget::item {{ padding: 0 10px; margin: 1px 0; border-radius: 7px; color: {C_TEXT}; }}"
            f"QListWidget::item:hover {{ background: {ink_css(16)}; }}"
            f"QListWidget::item:selected {{ background: {accent_css(90)}; color: {T('sel_text')}; }}")
        for i in range(combo.count()):
            it = QListWidgetItem(combo.itemText(i))
            it.setSizeHint(QSize(0, self.ROW_H))
            self.lst.addItem(it)
        self.lst.setCurrentRow(max(0, combo.currentIndex()))
        self.lst.itemClicked.connect(lambda it: self._pick(self.lst.row(it)))
        self.lst.itemActivated.connect(lambda it: self._pick(self.lst.row(it)))
        lay.addWidget(self.lst)
        rows = max(1, min(combo.count(), 8))                  # от 8 пунктов — прокрутка
        self.lst.setFixedHeight(rows * self.ROW_H + 4)
        self.setFixedSize(combo.width(), rows * self.ROW_H + 4 + 12)

    def _pick(self, row: int):
        self._combo.setCurrentIndex(row)
        self._combo.activated.emit(row)
        self.close()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(ink(30), 1))
        p.setBrush(QColor(T("popup")))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)
        p.end()

    def open(self):
        c = self._combo
        pos = c.mapToGlobal(QPoint(0, c.height() + 4))
        scr = (c.screen() or QGuiApplication.primaryScreen()).availableGeometry()
        if pos.y() + self.height() > scr.bottom():
            pos = c.mapToGlobal(QPoint(0, -self.height() - 4))
        self.move(pos)
        self.show()
        self.lst.setFocus()


class AuroraComboBox(QComboBox):
    """QComboBox со своей стрелкой и своим скруглённым выпадающим списком (API прежний)."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(36)
        self.setStyleSheet("QComboBox::down-arrow { image: none; }")      # стрелку рисуем сами
        self._pop: _ComboPopup | None = None

    def showPopup(self):
        if self.count() == 0:
            return
        self.hidePopup()
        self._pop = _ComboPopup(self)
        self._pop.open()

    def hidePopup(self):
        if self._pop is not None:
            try:
                self._pop.close()
                self._pop.deleteLater()
            except RuntimeError:
                pass
            self._pop = None

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(C_TEXT if self.underMouse() and self.isEnabled() else
                          (C_DIM if self.isEnabled() else C_FAINT)), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        cx, cy = self.width() - 17.0, self.height() / 2.0
        p.drawPolyline(QPolygonF([QPointF(cx - 4, cy - 2), QPointF(cx, cy + 2), QPointF(cx + 4, cy - 2)]))
        p.end()


class CheckList(QListWidget):
    """Список с флажками: клик в ЛЮБОМ месте строки переключает флажок. В QListWidget с QSS зона индикатора
    смещена относительно нарисованной, а быстрый второй клик приходит как двойной и теряется — флажок
    ставился «со второго-третьего раза». Для флажков: item.setFlags(... | ItemIsUserCheckable)."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._swallow = False

    def _toggle_at(self, e) -> bool:
        it = self.itemAt(e.position().toPoint())
        if e.button() != Qt.MouseButton.LeftButton or it is None \
                or not (it.flags() & Qt.ItemFlag.ItemIsUserCheckable):
            return False
        it.setCheckState(Qt.CheckState.Unchecked if it.checkState() == Qt.CheckState.Checked
                         else Qt.CheckState.Checked)
        self.setCurrentItem(it)
        self._swallow = True
        e.accept()
        return True

    def mousePressEvent(self, e):
        if not self._toggle_at(e):
            super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):
        if not self._toggle_at(e):
            super().mouseDoubleClickEvent(e)

    def mouseReleaseEvent(self, e):
        if self._swallow:
            self._swallow = False
            e.accept()
            return
        super().mouseReleaseEvent(e)


class LogView(QPlainTextEdit):
    """Лог: моноширинный 12 px, цвета по уровню (info|dim|ok|warn|error), автопрокрутка, перенос строк.
    Строки хранятся как (текст, уровень) и перерисовываются в restyle() при смене темы: цвет, записанный
    в HTML при добавлении, «застывает» (белый текст на светлом фоне после смены темы)."""
    def __init__(self, parent=None, max_lines: int = 5000):
        super().__init__(parent)
        self._entries: list[tuple[str, str]] = []
        self._max = max_lines
        self.setReadOnly(True)
        self.setMaximumBlockCount(max_lines)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        f = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        f.setPixelSize(12)
        self.setFont(f)
        self.restyle()

    def restyle(self):
        """После set_theme(): фон и цвета всех строк."""
        bg = "rgba(0,0,0,70)" if theme_name() == "dark" else ink_css(10)
        self.setStyleSheet(f"QPlainTextEdit {{ background-color: {bg}; color: {C_TEXT};"
                           f" border: 1px solid {ink_css(16)}; border-radius: 12px; padding: 8px 10px; }}")
        self.clear()
        for text, level in self._entries:
            self._render(text, level)
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _render(self, text: str, level: str):
        col = {"info": C_TEXT, "dim": C_DIM, "ok": C_OK, "warn": C_WARN,
               "error": "#fb7185" if theme_name() == "dark" else C_DANGER}.get(level, C_TEXT)
        for ln in (text.splitlines() or [""]):
            esc = html.escape(ln).replace("  ", "&nbsp;&nbsp;")
            self.appendHtml(f'<span style="color:{col}">{esc}</span>')

    def add(self, text: str, level: str = "info"):
        self._entries.append((text, level))
        del self._entries[:-self._max]
        sb = self.verticalScrollBar()
        stick = sb.value() >= sb.maximum() - 4              # крутим вниз, только если пользователь внизу
        self._render(text, level)
        if stick:
            sb.setValue(sb.maximum())


class AuroraToolTip(QWidget):
    """Подсказка Aurora: компактная, со скруглёнными углами (системная QToolTip углы не скругляет)."""
    MAX_W = 300

    def __init__(self, host: QWidget):
        # родитель обязателен: на Wayland всплывающее окно без родителя не показывается, а виджет без
        # родителя ещё и переживает QApplication и роняет программу при выходе
        super().__init__(host, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 5, 10, 6)
        self.lbl = QLabel()
        self.lbl.setFont(font(12))
        lay.addWidget(self.lbl)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_text(self, text: str, gpos: QPoint):
        self.lbl.setStyleSheet(f"color: {C_TEXT}; background: transparent;")
        self.lbl.setMinimumSize(0, 0)
        self.lbl.setMaximumSize(16777215, 16777215)
        self.lbl.setText(text)
        if QFontMetrics(self.lbl.font()).horizontalAdvance(text) > self.MAX_W:
            self.lbl.setWordWrap(True)
            self.lbl.setFixedWidth(self.MAX_W)
            self.lbl.setFixedHeight(self.lbl.heightForWidth(self.MAX_W))
        else:
            self.lbl.setWordWrap(False)
        self.adjustSize()
        scr = (QGuiApplication.screenAt(gpos) or QGuiApplication.primaryScreen()).availableGeometry()
        x = min(gpos.x() + 12, scr.right() - self.width())
        y = gpos.y() + 20
        if y + self.height() > scr.bottom():
            y = gpos.y() - self.height() - 8
        self.move(max(scr.left(), x), max(scr.top(), y))
        self.show()
        self._timer.start(7000)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(ink(34), 1))
        p.setBrush(QColor(T("popup")))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        p.end()


class _TipFilter(QObject):
    """Глобальный фильтр: показывает AuroraToolTip вместо системной подсказки (widget.setToolTip).
    Подсказки элементов списков/таблиц (item.setToolTip) остаются системными."""
    def __init__(self, app: QApplication):
        super().__init__(app)
        self._cur: AuroraToolTip | None = None

    @staticmethod
    def _tip_for(w: QWidget) -> AuroraToolTip:
        host = w.window()
        tip = host.findChild(AuroraToolTip)
        return tip if tip is not None else AuroraToolTip(host)

    def _hide(self):
        if self._cur is not None:
            try:
                self._cur.hide()
            except RuntimeError:                           # окно-хозяин уже удалено
                pass
            self._cur = None

    def eventFilter(self, obj, e):
        t = e.type()
        if t == QEvent.Type.ToolTip:
            if isinstance(obj, QWidget) and obj.toolTip() and not isinstance(obj.window(), AuroraToolTip):
                tip = self._tip_for(obj)
                tip.show_text(obj.toolTip(), QCursor.pos())
                self._cur = tip
                return True
            return False
        if t in (QEvent.Type.Leave, QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick,
                 QEvent.Type.KeyPress, QEvent.Type.Wheel, QEvent.Type.WindowDeactivate):
            self._hide()
        return False


def install_tooltips(app: QApplication) -> _TipFilter:
    """Один раз после setup_app(): подсказки Aurora (скруглённые, компактные) вместо системных."""
    f = _TipFilter(app)
    app.installEventFilter(f)
    app.aboutToQuit.connect(lambda: app.removeEventFilter(f))      # Python-фильтр не должен жить в разрушении Qt
    return f


A = sys.modules[__name__]      # app-код обращается к Aurora как A.X


# ═══ APP ═══════════════════════════════════════════════════════
ROLE_RPID = Qt.ItemDataRole.UserRole
ROLE_G    = Qt.ItemDataRole.UserRole + 1

APP_ID = "process-kill"
ICON_BG = "#8A0303"     # кроваво-красный фон
ICON_FG = "#F1E9E4"     # цвет черепа (кость)
ICON_DARK = "#1A0000"   # глазницы, нос, зубы

KILL_LABEL = "Завершить процесс"

# Панели загрузки — спокойный тёплый оранжевый: цвет ДАННЫХ, отделён от акцента
# хрома (индиго остаётся выделению, кнопке и маркеру). Не яркий: сатурация ~55 %.
BAR_LOAD       = "#d98e52"   # тёмная тема
BAR_LOAD_LIGHT = "#b06a2f"   # светлая тема


def load_bar_color() -> QColor:
    return A.qc(BAR_LOAD if A.theme_name() == "dark" else BAR_LOAD_LIGHT)


# ── Иконка приложения ─────────────────────────────────────────────
def make_app_icon() -> QIcon:
    """Иконка окна — кроваво-красный скруглённый квадрат с черепом."""
    size = 128
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    bg = QPainterPath()
    bg.addRoundedRect(0, 0, size, size, size * 0.22, size * 0.22)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(ICON_BG))
    p.drawPath(bg)

    skull = QPainterPath()
    skull.addEllipse(26, 18, 76, 70)
    jaw = QPainterPath()
    jaw.addRoundedRect(43, 72, 42, 32, 9, 9)
    skull = skull.united(jaw)
    p.setBrush(QColor(ICON_FG))
    p.drawPath(skull)

    p.setBrush(QColor(ICON_DARK))
    p.drawEllipse(36, 48, 21, 23)
    p.drawEllipse(71, 48, 21, 23)

    nose = QPainterPath()
    nose.moveTo(64, 72)
    nose.lineTo(57, 85)
    nose.lineTo(71, 85)
    nose.closeSubpath()
    p.drawPath(nose)

    pen = QPen(QColor(ICON_DARK), 3.2)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    for x in (54, 64, 74):
        p.drawLine(x, 93, x, 103)
    p.end()
    return QIcon(pix)


def fmt_mem(mb: float) -> str:
    return f"{mb / 1024:.2f} GB" if mb >= 1024 else f"{mb:.1f} MB"


# ── Делегат строки (кирпичики Aurora + содержимое процесса) ───────
class RowDelegate(QStyledItemDelegate):
    """Рисует всю строку: фон-«пилюля», аватар, имя, бейдж, CPU, бар памяти."""
    def __init__(self, table: A.HoverTable):
        super().__init__(table)
        self.table = table
        self.f_name  = A.font(13.5, QFont.Weight.DemiBold)
        self.f_sub   = A.font(10.5, QFont.Weight.Normal)
        self.f_badge = A.font(10, QFont.Weight.Bold)
        self.f_cpu   = A.font(13.5, QFont.Weight.DemiBold)
        self.f_pct   = A.font(10.5, QFont.Weight.Medium)
        self.f_mem   = A.font(12, QFont.Weight.DemiBold)
        self.f_mem_p = A.font(11, QFont.Weight.Medium)
        self.f_ava   = A.font(15, QFont.Weight.Bold)
        self._icon_cache: dict[str, QPixmap | None] = {}

    # ── аватар: иконка из темы, иначе буква (рисует aurora) ───────
    def _icon_for(self, name: str) -> QPixmap | None:
        key = name.lower()
        if key not in self._icon_cache:
            pm = None
            for cand in (key, key.replace('_', '-'), key.split('.')[0]):
                ic = QIcon.fromTheme(cand)
                if not ic.isNull():
                    pm = ic.pixmap(QSize(22, 22))
                    break
            self._icon_cache[key] = pm
        return self._icon_cache[key]

    # ── основной paint ────────────────────────────────────────────
    def paint(self, p, option, index):
        g = index.sibling(index.row(), 0).data(ROLE_G)
        if not g:
            return
        col = index.column()
        rect = QRectF(option.rect)
        sel = bool(option.state & QStyle.StateFlag.State_Selected)
        hov = (index.row() == self.table.hover_row)
        cy = rect.top() + rect.height() / 2

        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        A.paint_row_pill(p, rect, col, self.table.columnCount(),
                         sel, hov, float(self.table.viewport().width()))

        if col == 0:
            self._paint_name(p, rect, cy, g, sel)
        elif col == 1:
            self._paint_cpu(p, rect, cy, g)
        else:
            self._paint_mem(p, rect, cy, g)
        p.restore()

    def _paint_name(self, p, rect, cy, g, sel):
        if sel:   # подпись системы: акцентный маркер слева
            A.paint_selection_marker(p, rect, cy)

        tile = QRectF(rect.left() + 16, cy - 17, 34, 34)
        A.paint_avatar(p, tile, g['name'], icon_pm=self._icon_for(g['name']),
                       letter_font=self.f_ava)

        x = tile.right() + 12
        avail = rect.right() - x - 6
        badge_w = 0.0
        badge_txt = f"×{g['count']}" if g['count'] > 1 else ""
        if badge_txt:
            badge_w = QFontMetrics(self.f_badge).horizontalAdvance(badge_txt) + 12

        fm = QFontMetrics(self.f_name)
        name_avail = avail - (badge_w + 8 if badge_txt else 0)
        name = fm.elidedText(g['name'], Qt.TextElideMode.ElideRight, int(max(20, name_avail)))
        name_w = fm.horizontalAdvance(name)

        p.setFont(self.f_name)
        p.setPen(QColor(A.C_TEXT))
        p.drawText(QRectF(x, cy - 19, name_w + 4, 19),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)

        if badge_txt:
            br = QRectF(x + name_w + 8, cy - 18, badge_w, 17)
            A.paint_badge(p, br, badge_txt, self.f_badge)

        p.setFont(self.f_sub)
        p.setPen(QColor(A.C_FAINT if not sel else A.C_DIM))
        p.drawText(QRectF(x, cy + 1, avail, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"PID {g['rpid']}" + (f" · GPU {fmt_mem(g['gpu'])}" if g.get('gpu', 0) >= 64 else ""))

    def _paint_cpu(self, p, rect, cy, g):
        cpu = g['cpu']
        if cpu < 0.05:
            col, tint = QColor(A.C_FAINT), None
        elif cpu < 10:
            col, tint = QColor(A.C_TEXT), None
        elif cpu < 40:
            col, tint = QColor(A.C_WARN), QColor(251, 191, 36, 30)
        else:
            col, tint = QColor("#fb7185"), QColor(244, 63, 94, 36)

        txt = f"{cpu:.1f}"
        tw = QFontMetrics(self.f_cpu).horizontalAdvance(txt)
        pw = QFontMetrics(self.f_pct).horizontalAdvance("%")
        total = tw + 2 + pw
        x = rect.left() + (rect.width() - total) / 2

        if tint is not None:
            pill = QRectF(x - 10, cy - 12, total + 20, 24)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(tint)
            p.drawRoundedRect(pill, 12, 12)

        p.setFont(self.f_cpu)
        p.setPen(col)
        p.drawText(QRectF(x, cy - 12, tw + 2, 24),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, txt)
        p.setFont(self.f_pct)
        dim = QColor(col)
        dim.setAlpha(150)
        p.setPen(dim)
        p.drawText(QRectF(x + tw + 2, cy - 11, pw + 2, 24),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "%")

    def _paint_mem(self, p, rect, cy, g):
        left = rect.left() + 14
        width = rect.width() - 14 - 18
        p.setFont(self.f_mem)
        p.setPen(QColor(A.C_TEXT))
        p.drawText(QRectF(left, cy - 20, width, 18),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, fmt_mem(g['mem']))
        p.setFont(self.f_mem_p)
        p.setPen(QColor(A.C_DIM))
        p.drawText(QRectF(left, cy - 20, width, 18),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{g['pct']:.1f}%")
        A.draw_scale_bar(p, QRectF(left, cy + 5, width, 7), g['pct'] / 100.0,
                         color=load_bar_color())

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), A.ROW_H)


# ═════════════════════════════════════════════════════════════════
class ProcessCollector:
    """Бэкенд без Qt: сбор процессов, группировка, измерение памяти.
    Запуск диагностики:  ProcessKill.py --why [часть_имени]"""
    MIN_MEM_MB = 20.0
    MIN_CPU_PCT = 0.5
    TOP_BY_RSS = 40        # групп, отбираемых по RSS до точного измерения
    TOP_BY_CPU = 10        # + групп, отбираемых по CPU
    MAX_MEASURE = 120      # максимум процессов за refresh с чтением smaps/fdinfo
    SESSION_BLACKLIST = {
        'xwayland', 'xwaylandvideobridge', 'xsettingsd', 'dbus-daemon', 'dbus-broker',
        'dbus-broker-launch', 'at-spi-bus-launcher', 'at-spi2-registryd', 'gvfsd',
        'gvfsd-fuse', 'gvfs-udisks2-volume-monitor', 'gvfs-goa-volume-monitor',
        'gvfs-mtp-volume-monitor', 'gvfs-afc-volume-monitor', 'gvfs-gphoto2-volume-monitor',
        'xdg-document-portal', 'xdg-desktop-portal', 'xdg-desktop-portal-kde',
        'xdg-desktop-portal-gtk', 'xdg-permission-store', 'pipewire', 'pipewire-pulse',
        'wireplumber', 'kaccess', 'ksystemstats', 'kscreen_backend_launcher', 'kcminit',
        'kcminit_startup', 'kglobalaccel5', 'kglobalaccel6', 'baloo_file',
        'baloo_file_extractor', 'kwalletd5', 'kwalletd6', 'polkit-kde-authentication-agent-1',
        'systemd', 'systemd-logind', 'systemd-journald', 'systemd-udevd', 'systemd-oomd',
        'gpg-agent', 'ssh-agent', 'fusermount', 'fusermount3', 'tracker-store', 'tracker-miner-fs'
    }
    # Процессы-«границы»: группа не поднимается выше них
    STOP = {'systemd', 'init', 'login', 'sshd', 'konsole', 'bash', 'sh', 'zsh', 'fish', 'ksmserver',
            'startplasma-wayland', 'startplasma-x11', 'plasma_session', 'dbus-run-session'}

    def __init__(self):
        self.cpu_count = psutil.cpu_count(logical=True) or 1
        self.my_uid = os.getuid()
        self._cache: dict[int, psutil.Process] = {}
        # отладочные данные последнего сбора (для --why)
        self.dbg_info: dict[int, dict] = {}
        self.dbg_roots: dict[int, int] = {}
        self.dbg_dropped: dict[int, str] = {}
        self._warmup()

    def clear(self):
        self._cache.clear()

    # ── измерение памяти ──────────────────────────────────────────
    @staticmethod
    def _read_pss(pid: int) -> float | None:
        """PSS = Anon + File + Shmem. Shmem (memfd, wine/DXVK, esync/fsync,
        Wayland/X shm) раньше не учитывался — у игр под Proton это сотни MB."""
        try:
            anon = file_mem = shm = 0
            with open(f'/proc/{pid}/smaps_rollup') as f:
                for line in f:
                    if line.startswith('Pss_Anon:'):
                        anon += int(line.split()[1])
                    elif line.startswith('Pss_File:'):
                        file_mem += int(line.split()[1])
                    elif line.startswith('Pss_Shmem:'):
                        shm += int(line.split()[1])
            return (anon + file_mem + shm) / 1024.0
        except (OSError, ValueError, IndexError):
            return None

    @staticmethod
    def _read_gtt(pid: int) -> float:
        """Память GPU в системной RAM (GTT) по fdinfo render-узла.
        На APU (унифицированная память) именно здесь живут текстуры и буферы
        игр: в RSS/PSS процесса этой памяти НЕТ, зато она вычитается из RAM."""
        base = f'/proc/{pid}/fd'
        try:
            fds = os.listdir(base)
        except OSError:
            return 0.0
        total_kib = 0
        seen = set()
        for fd in fds:
            try:
                if not os.readlink(f'{base}/{fd}').startswith('/dev/dri/'):
                    continue
                cid, gtt = None, 0
                with open(f'/proc/{pid}/fdinfo/{fd}') as f:
                    for line in f:
                        if line.startswith('drm-client-id:'):
                            cid = line.split()[1]
                        elif line.startswith('drm-memory-gtt:'):
                            gtt = int(line.split()[1])
            except (OSError, ValueError, IndexError):
                continue
            if cid is not None:                 # один клиент = несколько dup-fd
                if cid in seen:
                    continue
                seen.add(cid)
            total_kib += gtt
        return total_kib / 1024.0

    # ── кэш процессов и снимок ────────────────────────────────────
    def _warmup(self):
        for p in psutil.process_iter():
            try:
                p.cpu_percent(interval=None)
                self._cache[p.pid] = p
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass

    def _sync_cache(self):
        live = set()
        for p in psutil.process_iter():
            live.add(p.pid)
            if p.pid not in self._cache:
                try:
                    p.cpu_percent(interval=None)
                    self._cache[p.pid] = p
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass
        for pid in set(self._cache) - live:
            self._cache.pop(pid, None)

    def _snapshot(self) -> dict[int, dict]:
        """Один проход: имя, ppid, uid, CPU, RSS для ВСЕХ процессов."""
        info = {}
        for pid, p in list(self._cache.items()):
            try:
                with p.oneshot():
                    name = p.name()
                    if not name or name.startswith('['):
                        continue
                    info[pid] = {
                        'name': name,
                        'ppid': p.ppid(),
                        'uid': p.uids().real,
                        'cpu': p.cpu_percent(interval=None) / self.cpu_count,
                        'rss': p.memory_info().rss / 1048576.0,
                    }
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                self._cache.pop(pid, None)
        return info

    def _root_of(self, pid: int, info: dict, memo: dict) -> int:
        chain, seen, cur = [], set(), pid
        while True:
            if cur in memo:
                root = memo[cur]
                break
            if cur in seen:
                root = cur
                break
            seen.add(cur)
            chain.append(cur)
            i = info[cur]
            par = info.get(i['ppid'])
            if i['ppid'] <= 1 or par is None or par['name'] in self.STOP or par['uid'] != i['uid']:
                root = cur
                break
            cur = i['ppid']
        for c in chain:
            memo[c] = root
        return root

    # ── главный метод: группы с точной памятью ────────────────────
    def collect_groups(self) -> tuple[list[dict], int]:
        self._sync_cache()
        info = self._snapshot()
        memo: dict[int, int] = {}
        roots = {pid: self._root_of(pid, info, memo) for pid in info}

        # 1) состав групп. Фильтры по именам/пользователю — на уровне процесса,
        #    пороги по ресурсам — на уровне ГРУППЫ (а не отдельного процесса).
        members: dict[int, list[int]] = defaultdict(list)
        for pid, i in info.items():
            if i['name'].lower() in self.SESSION_BLACKLIST:
                continue
            if i['uid'] != self.my_uid and not (i['cpu'] >= 3.0 or i['rss'] >= 150.0):
                continue
            members[roots[pid]].append(pid)

        dropped: dict[int, str] = {}
        groups = []
        for rpid, pids in members.items():
            rss = sum(info[p]['rss'] for p in pids)
            cpu = sum(info[p]['cpu'] for p in pids)
            if rss < self.MIN_MEM_MB and cpu < self.MIN_CPU_PCT:
                dropped[rpid] = f"группа слишком лёгкая (RSS {rss:.0f} MB, CPU {cpu:.1f}%)"
                continue
            groups.append({'rpid': rpid, 'pids': pids, 'rss': rss, 'cpu': cpu})

        # 2) отбор кандидатов: топ по RSS + топ по CPU (раньше резались одиночные
        #    процессы по RSS до группировки, и группа из многих мелких выпадала)
        keep: dict[int, dict] = {}
        for g in sorted(groups, key=lambda x: x['rss'], reverse=True)[:self.TOP_BY_RSS]:
            keep[g['rpid']] = g
        for g in sorted(groups, key=lambda x: x['cpu'], reverse=True)[:self.TOP_BY_CPU]:
            keep[g['rpid']] = g
        for g in groups:
            if g['rpid'] not in keep:
                dropped[g['rpid']] = "не вошла в топ кандидатов"

        # 3) точное измерение (PSS+Shmem+GTT) для самых тяжёлых процессов
        heavy = [p for g in keep.values() for p in g['pids']
                 if info[p]['rss'] >= self.MIN_MEM_MB or info[p]['cpu'] >= self.MIN_CPU_PCT]
        heavy.sort(key=lambda p: info[p]['rss'], reverse=True)
        measure = set(heavy[:self.MAX_MEASURE])

        out, n_procs = [], 0
        for rpid, g in keep.items():
            mem = gpu = 0.0
            for pid in g['pids']:
                i = info[pid]
                if pid in measure:
                    pss = self._read_pss(pid)
                    if pss is None:
                        pss = i['rss'] * 0.7
                    gtt = self._read_gtt(pid)
                    mem += pss + gtt
                    gpu += gtt
                else:
                    mem += i['rss'] if i['rss'] < self.MIN_MEM_MB else i['rss'] * 0.7
            n_procs += len(g['pids'])
            out.append({'rpid': rpid, 'name': info[rpid]['name'] if rpid in info else str(rpid),
                        'cpu': g['cpu'], 'mem': mem, 'gpu': gpu,
                        'count': len(g['pids']), 'pids': g['pids']})

        self.dbg_info, self.dbg_roots, self.dbg_dropped = info, roots, dropped
        return out, n_procs

    # ── диагностика: «почему процесса нет в списке» ───────────────
    def diagnose(self, needle: str = ''):
        import time
        time.sleep(1.2)                      # чтобы cpu_percent успел накопиться
        groups, _ = self.collect_groups()
        shown = {pid: g for g in groups for pid in g['pids']}
        info, roots = self.dbg_info, self.dbg_roots
        needle = needle.lower()
        rows = sorted(info.items(), key=lambda kv: kv[1]['rss'], reverse=True)
        print(f"{'PID':>7} {'RSS':>8} {'PSS+':>8} {'GTT':>8} {'CPU%':>5}  {'ИМЯ':<18} {'КОРЕНЬ':<18} СТАТУС")
        shown_n = 0
        for pid, i in rows:
            if needle:
                if needle not in i['name'].lower() and needle not in info.get(roots[pid], {}).get('name', '').lower():
                    continue
            elif i['rss'] < 40 and pid not in shown:
                continue
            shown_n += 1
            if shown_n > 80:
                print("… (ограничено 80 строками, уточните фильтр)")
                break
            pss = self._read_pss(pid)
            gtt = self._read_gtt(pid)
            rname = info.get(roots[pid], {}).get('name', '?')
            if pid in shown:
                status = f"ПОКАЗАН в группе «{shown[pid]['name']}»"
            elif i['name'].lower() in self.SESSION_BLACKLIST:
                status = "СКРЫТ: чёрный список"
            elif i['uid'] != self.my_uid and not (i['cpu'] >= 3.0 or i['rss'] >= 150.0):
                status = f"СКРЫТ: чужой uid={i['uid']} и мало ресурсов"
            else:
                status = "СКРЫТ: " + self.dbg_dropped.get(roots[pid], "группа не прошла отбор")
            pss_s = f"{pss:.0f}" if pss is not None else "нет"
            print(f"{pid:>7} {i['rss']:>8.0f} {pss_s:>8} {gtt:>8.0f} {i['cpu']:>5.1f}  "
                  f"{i['name'][:18]:<18} {rname[:18]:<18} {status}")
        print("\nRSS/PSS+/GTT — в MB. PSS+ = Anon+File+Shmem. 'нет' = нет доступа к smaps_rollup (процесс non-dumpable, напр. bwrap).")


class ProcessManager(A.AuroraWindow):
    def __init__(self):
        # 600×740 — вместе с полем тени: видимая часть окна 568×708
        super().__init__("Process Manager", size=(600, 740), tag="ULTRA",
                         icon=make_app_icon())
        self.move(200, 100)
        self.collector = ProcessCollector()
        self.sort_col = 2          # 1 = CPU, 2 = MEM
        self.sort_asc = False
        self._last_group_pids: dict[int, list[int]] = {}   # rpid -> pids, снимок последнего refresh()
        self._kill_locked = False   # защита от двойного нажатия Kill
        psutil.cpu_percent(interval=None)   # «прогрев» системной загрузки ЦП
        self._init_ui()
        self.start_timer()

    # ═══════════════ UI ══════════════════════════════════════════
    def _init_ui(self):
        self.body.setContentsMargins(16, 2, 16, 12)

        # ── Карточки RAM / CPU ─────────────────────────────────────
        cards = QHBoxLayout()
        cards.setSpacing(10)
        self.card_mem = A.StatCard("ОПЕРАТИВНАЯ ПАМЯТЬ",    # обе доли реальные — бар оправдан
                                   bar_color=load_bar_color())
        self.card_cpu = A.StatCard("ПРОЦЕССОР", bar_color=load_bar_color())
        cards.addWidget(self.card_mem)
        cards.addWidget(self.card_cpu)
        self.body.addLayout(cards)

        # ── Таблица ────────────────────────────────────────────────
        self.table = A.HoverTable(0, 3)
        self.table.setHorizontalHeader(A.AuroraHeader(self.table))
        self.table.setHorizontalHeaderLabels(["ПРОЦЕСС", "ЦП", "ПАМЯТЬ"])
        A.setup_aurora_table(self.table, row_h=A.ROW_H)
        self.table.setItemDelegate(RowDelegate(self.table))

        hh = self.table.horizontalHeader()
        hh.sectionClicked.connect(self._on_header_click)
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(1, 84)
        self.table.setColumnWidth(2, 228)

        tbl_wrap = QHBoxLayout()
        tbl_wrap.setContentsMargins(10, 0, 6, 0)
        tbl_wrap.addWidget(self.table)
        self.body.addLayout(tbl_wrap, 1)

        # ── Нижняя панель ──────────────────────────────────────────
        bottom = QVBoxLayout()
        bottom.setContentsMargins(0, 10, 0, 2)
        bottom.setSpacing(12)

        self.btn_kill = A.PrimaryButton(KILL_LABEL, kind="danger", stop_icon=True, hint="Del")
        self.btn_kill.clicked.connect(self.kill_process)
        bottom.addWidget(self.btn_kill)

        st = QHBoxLayout()
        st.setContentsMargins(2, 0, 2, 0)
        st.setSpacing(6)
        st.addWidget(A.LiveDot())
        self.lbl_status = QLabel("Мониторинг")
        self.lbl_status.setFont(A.font(12))
        A.style_label(self.lbl_status, "dim")
        self.lbl_memory = QLabel("обновление каждые 3 с")
        self.lbl_memory.setFont(A.font(12))
        A.style_label(self.lbl_memory, "faint")
        st.addWidget(self.lbl_status)
        st.addStretch()
        st.addWidget(self.lbl_memory)
        bottom.addLayout(st)
        self.body.addLayout(bottom)

    # ── Сортировка ─────────────────────────────────────────────────
    def _on_header_click(self, col_index):
        if col_index not in (1, 2):
            return
        if self.sort_col == col_index:
            self.sort_asc = not self.sort_asc
        else:
            self.sort_col = col_index
            self.sort_asc = False
        self.refresh()

    def _update_headers(self):
        self.table.horizontalHeader().set_sort(self.sort_col, self.sort_asc)

    # ── Умное обновление (flicker‑free, обновление «на месте») ─────
    def refresh(self):
        prev_row = self.table.currentRow()
        sel_rpid = None
        if prev_row >= 0:
            item = self.table.item(prev_row, 0)
            if item:
                sel_rpid = item.data(ROLE_RPID)

        grouped, n_procs = self.collector.collect_groups()

        key = 'cpu' if self.sort_col == 1 else 'mem'
        grouped.sort(key=lambda x: x[key], reverse=not self.sort_asc)
        top = grouped[:30]

        # Снимок для kill_process(): pid'ы соответствуют именно тому, что
        # показано в таблице сейчас.
        self._last_group_pids = {g['rpid']: g['pids'] for g in top}

        # Доля от ВСЕЙ оперативной памяти (дробная, без округления до int)
        vm = psutil.virtual_memory()
        total_mb = vm.total / (1024 ** 2)
        for g in top:
            g['pct'] = min(g['mem'] / total_mb * 100.0, 100.0) if total_mb else 0.0

        self.table.setUpdatesEnabled(False)
        n = len(top)
        if self.table.rowCount() != n:
            self.table.setRowCount(n)
        flags = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
        for i, g in enumerate(top):
            for c in range(3):
                if self.table.item(i, c) is None:
                    it = QTableWidgetItem()
                    it.setFlags(flags)
                    self.table.setItem(i, c, it)
            it0 = self.table.item(i, 0)
            it0.setText(g['name'] + (f"  ×{g['count']}" if g['count'] > 1 else ""))
            it0.setData(ROLE_RPID, g['rpid'])
            it0.setData(ROLE_G, g)

        self._update_headers()

        # Восстановление выделения по rpid
        target = None
        if sel_rpid is not None:
            for i, g in enumerate(top):
                if g['rpid'] == sel_rpid:
                    target = i
                    break
        if target is None and n > 0:
            target = 0 if prev_row < 0 else min(prev_row, n - 1)
        if target is not None and self.table.currentRow() != target:
            self.table.selectRow(target)
        self.table.setUpdatesEnabled(True)
        self.table.viewport().update()

        # Карточки и статус
        used_gb = vm.used / (1024 ** 3)
        total_gb = vm.total / (1024 ** 3)
        self.card_mem.set_data(vm.used / vm.total, f"{used_gb:.1f}",
                               f"/ {total_gb:.1f} GB", f"{vm.used / vm.total * 100:.0f}%")
        cpu_total = psutil.cpu_percent(interval=None)
        self.card_cpu.set_data(cpu_total / 100.0, f"{cpu_total:.0f}", "%",
                               f"{self.collector.cpu_count} {A.ru_plural(self.collector.cpu_count, 'поток', 'потока', 'потоков')}")
        self.lbl_status.setText(
            f"{n} {A.ru_plural(n, 'группа', 'группы', 'групп')} · "
            f"{n_procs} {A.ru_plural(n_procs, 'процесс', 'процесса', 'процессов')}")

    # ── Управление процессами ──────────────────────────────────────
    def _unlock_kill(self):
        """Разблокировать кнопку Kill после задержки."""
        self._kill_locked = False
        self.btn_kill.setEnabled(True)
        self.btn_kill.setText(KILL_LABEL)

    def kill_process(self):
        # Защита от двойного нажатия — игнорируем повторный вызов в течение 2 сек
        if self._kill_locked:
            return
        self._kill_locked = True
        self.btn_kill.setEnabled(False)
        self.btn_kill.setText("Завершение…")
        QTimer.singleShot(2000, self._unlock_kill)

        row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, 0)
        if not item:
            return
        rpid = item.data(ROLE_RPID)
        pids = self._last_group_pids.get(rpid)
        if not pids:
            groups, _ = self.collector.collect_groups()
            pids = []
            for g in groups:
                if g['rpid'] == rpid:
                    pids = g['pids']
                    break

        # Фаза 1 — SIGTERM (вежливый запрос на завершение)
        alive = []
        for pid in pids:
            try:
                p = psutil.Process(pid)
                p.terminate()
                alive.append(p)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        _, still_alive = psutil.wait_procs(alive, timeout=1.5)

        # Фаза 2 — SIGKILL для тех кто не завершился
        for p in still_alive:
            try:
                p.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        psutil.wait_procs(still_alive, timeout=1.0)
        self.refresh()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Delete:
            self.kill_process()
        else:
            super().keyPressEvent(event)

    def start_timer(self):
        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh)
        self.timer.start(3000)
        self.refresh()

    def closeEvent(self, e):
        self.timer.stop()
        self.collector.clear()
        super().closeEvent(e)


def main():
    if '--why' in sys.argv:
        i = sys.argv.index('--why')
        ProcessCollector().diagnose(sys.argv[i + 1] if i + 1 < len(sys.argv) else '')
        return
    # xcb нужен старым WM без Wayland-ресайза; Wayland-конфигурации не перебиваем
    os.environ.setdefault('QT_QPA_PLATFORM', 'xcb')
    # Уникальный argv[0] (X11 WM_CLASS) — иначе DE группирует все
    # python-скрипты в одну кнопку с общей иконкой.
    sys.argv[0] = APP_ID
    app = QApplication(sys.argv)
    A.setup_app(app)                    # шрифт (Inter → запасной), Fusion, PNG-галочка
    app.setApplicationName("Process Manager Ultra")
    app.setDesktopFileName(APP_ID)
    app.setWindowIcon(make_app_icon())
    w = ProcessManager()
    w.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    w.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
